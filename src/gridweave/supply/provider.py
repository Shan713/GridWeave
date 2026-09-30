"""Campus Supply Provider implementing the production SupplyProvider protocol."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, DispatchResult, SourceType, SupplyOffer
from gridweave.supply.accounting import SupplyAccountant
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.dispatcher import SupplyDispatcher
from gridweave.supply.events import SupplyEventHandler
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.metrics import SupplyMetrics, SupplyMetricsCalculator
from gridweave.supply.solar_agent import SolarEnergyAgent
from gridweave.utils.validation import ValidationError


class CampusSupplyProvider:
    """Production aggregator implementing :class:`gridweave.interfaces.SupplyProvider`.

    Coordinates multiple autonomous energy supply agents:
      - GridSupplyAgents (external imports, dynamic tariffs, outage handling)
      - SolarEnergyAgents (PV generation, solar forecasting, curtailment)
      - BatteryStorageAgents (BESS storage, reserves, degradation, charge/discharge)

    Features:
      - Pure query ``offers(time_slot)``: no physical state side effects
      - Atomic batch validation and execution via :class:`SupplyDispatcher`
      - First-law energy conservation auditing via :class:`SupplyAccountant`
      - Dynamic event handling via :class:`SupplyEventHandler`
      - Telemetry snapshots and metrics export for P4 Coordinator
    """

    def __init__(
        self,
        grid_agents: Sequence[GridSupplyAgent] | None = None,
        solar_agents: Sequence[SolarEnergyAgent] | None = None,
        battery_agents: Sequence[BatteryStorageAgent] | None = None,
        auto_charge_surplus_solar: bool = True,
        grid_charge_off_peak: bool = True,
        grid_charge_hours: tuple[float, float] = (0.0, 6.0),
    ) -> None:
        self.grid_agents = [GridSupplyAgent()] if grid_agents is None else list(grid_agents)
        self.solar_agents = [SolarEnergyAgent()] if solar_agents is None else list(solar_agents)
        self.battery_agents = [BatteryStorageAgent()] if battery_agents is None else list(battery_agents)
        self.auto_charge_surplus_solar = auto_charge_surplus_solar
        # Surplus solar rarely exists on a campus (solar is the cheapest source, so the
        # market uses all of it), so without a grid path the battery empties once and
        # never recovers. Off-peak grid charging refills it with cheap night energy.
        start, end = (float(h) for h in grid_charge_hours)
        if not 0.0 <= start < end <= 24.0:
            raise ValidationError(f"grid_charge_hours must satisfy 0 <= start < end <= 24, got {grid_charge_hours}")
        self.grid_charge_off_peak = grid_charge_off_peak
        self.grid_charge_hours = (start, end)

        # Index all sources by unique source_id
        all_sources: list[Any] = [*self.grid_agents, *self.solar_agents, *self.battery_agents]
        source_ids = [s.source_id for s in all_sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValidationError(f"Duplicate source IDs registered in CampusSupplyProvider: {source_ids}")

        self.sources: dict[str, Any] = {s.source_id: s for s in all_sources}
        self.source_types = {
            s.source_id: SourceType.GRID if isinstance(s, GridSupplyAgent)
            else SourceType.SOLAR if isinstance(s, SolarEnergyAgent)
            else SourceType.BATTERY
            for s in all_sources
        }
        self.dispatcher = SupplyDispatcher(self.sources)
        self.accountant = SupplyAccountant()
        self.events = SupplyEventHandler(self.grid_agents, self.solar_agents, self.battery_agents)

        self._active_offers: dict[str, SupplyOffer] = {}
        self._current_slot: TimeSlot | None = None
        self._settled_slots: set[TimeSlot] = set()

    # -------------------------------------------------------------------------
    # SupplyProvider Protocol Implementation
    # -------------------------------------------------------------------------
    def offers(self, time_slot: TimeSlot) -> list[SupplyOffer]:
        """Query deliverable capacity and marginal price for every source.

        Pure query: Strictly non-mutating.
        """
        self._current_slot = time_slot
        offer_list: list[SupplyOffer] = []

        for source in self.sources.values():
            offer = source.get_offer(time_slot)
            offer_list.append(offer)

        self._active_offers = {o.source_id: o for o in offer_list}
        return offer_list

    def dispatch(self, requests: Sequence[DispatchRequest]) -> list[DispatchResult]:
        """Execute accepted market clearing dispatch instructions atomically.

        Updates physical states, records accounting ledger, and optionally absorbs
        surplus solar generation into available battery storage.
        """
        if not requests:
            return []

        slot = requests[0].time_slot
        if self._current_slot != slot:
            raise ValidationError(
                f"Dispatch slot {slot} is stale; request offers for that slot before dispatch"
            )
        if any(request.time_slot != slot for request in requests):
            raise ValidationError("Dispatch batch contains multiple time slots")
        if slot in self._settled_slots:
            raise ValidationError(f"Dispatch for already settled slot {slot} was submitted again")

        # 1. Execute physical market dispatch
        results = self.dispatcher.dispatch(requests, self._active_offers)

        # 2. Check for surplus unrequested solar generation to absorb into battery
        total_solar_gen = sum(s.generation_kw(slot) for s in self.solar_agents)
        solar_ids = {source.source_id for source in self.solar_agents}
        solar_dispatched = sum(r.delivered_kw for r in results if r.source_id in solar_ids)
        surplus_solar_kw = max(0.0, total_solar_gen - solar_dispatched)

        total_battery_charged_kw = 0.0
        if self.auto_charge_surplus_solar and surplus_solar_kw > 0.1:
            for b in self.battery_agents:
                if surplus_solar_kw <= 0.01:
                    break
                absorbed_kw = b.charge(surplus_solar_kw, slot, source_price_per_kwh=0.0)
                surplus_solar_kw -= absorbed_kw
                total_battery_charged_kw += absorbed_kw

        # 2b. Off-peak grid charging from spare grid capacity (never displaces campus supply)
        grid_to_battery_kw = self._charge_from_grid(slot, results)
        total_battery_charged_kw += grid_to_battery_kw

        # 3. Supply ledger & conservation audit
        grid_tariff = (
            self.grid_agents[0].current_tariff(slot) if self.grid_agents else 10.0
        )
        batt_deg = (
            self.battery_agents[0].degradation_cost_per_kwh if self.battery_agents else 7.0
        )

        # Compute battery efficiency losses
        loss_kwh = sum(
            r.state.get("efficiency_loss_kwh", 0.0)
            for r in results
            if r.source_id in {b.source_id for b in self.battery_agents}
        )

        self.accountant.record_slot(
            slot=slot,
            results=results,
            solar_generated_kw=total_solar_gen,
            battery_charged_kw=total_battery_charged_kw,
            battery_efficiency_losses_kwh=loss_kwh,
            grid_tariff=grid_tariff,
            battery_deg_rate=batt_deg,
            source_types=self.source_types,
            grid_to_battery_kw=grid_to_battery_kw,
        )
        self._settled_slots.add(slot)

        return results

    def _in_grid_charge_window(self, slot: TimeSlot) -> bool:
        mid = slot.start.hour + (slot.start.minute + slot.duration_minutes / 2.0) / 60.0
        start, end = self.grid_charge_hours
        return start <= mid < end

    def _charge_from_grid(self, slot: TimeSlot, results: Sequence[DispatchResult]) -> float:
        """Charge batteries from spare grid capacity during the off-peak window.

        A battery that discharged in this slot is not charged (charge and discharge
        are mutually exclusive per slot). Returns total grid power imported for storage.
        """
        if not (self.grid_charge_off_peak and self.grid_agents and self._in_grid_charge_window(slot)):
            return 0.0
        delivered = {r.source_id: r.delivered_kw for r in results}
        total = 0.0
        for battery in self.battery_agents:
            if delivered.get(battery.source_id, 0.0) > 0.0:
                continue
            want_kw = battery.available_charge_kw(slot)
            for grid in self.grid_agents:
                if want_kw <= 0.01:
                    break
                imported = grid.import_for_storage(want_kw, slot, delivered.get(grid.source_id, 0.0))
                if imported <= 0.0:
                    continue
                accepted = battery.charge(imported, slot, source_price_per_kwh=grid.current_tariff(slot))
                # battery accepted <= imported by construction (imported <= its own charge limit)
                delivered[grid.source_id] = delivered.get(grid.source_id, 0.0) + accepted
                total += accepted
                want_kw -= accepted
        return total

    # -------------------------------------------------------------------------
    # System Telemetry, Reset, and Metrics
    # -------------------------------------------------------------------------
    def metrics(self) -> SupplyMetrics:
        """Compute consolidated KPI report."""
        peak_grid = max((g.state.peak_imported_kw for g in self.grid_agents), default=0.0)
        total_batt_cap = sum(b.capacity_kwh for b in self.battery_agents)
        return SupplyMetricsCalculator.compute(
            accountant=self.accountant,
            peak_grid_kw=peak_grid,
            battery_nominal_capacity_kwh=total_batt_cap,
        )

    def reset(self) -> None:
        """Deterministically reset all sources, accountant, and event logs."""
        for s in self.sources.values():
            s.reset()
        self.accountant.reset()
        self.events.reset()
        self._active_offers.clear()
        self._current_slot = None
        self._settled_slots.clear()

    def snapshot(self) -> dict[str, Any]:
        """Source-state snapshot for P4 coordinator and dashboard."""
        return {
            "sources": {s_id: s.snapshot() for s_id, s in self.sources.items()},
            "accounting_summary": self.accountant.summary(),
            "active_offers_count": len(self._active_offers),
            "current_slot": self._current_slot.to_dict() if self._current_slot else None,
            "settled_slots": len(self._settled_slots),
        }

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> CampusSupplyProvider:
        """Instantiate a configured multi-source supply provider."""
        grid_agents: list[GridSupplyAgent] = []
        solar_agents: list[SolarEnergyAgent] = []
        battery_agents: list[BatteryStorageAgent] = []

        sources_cfg = config.get("sources", [])
        for entry in sources_cfg:
            entry = dict(entry)
            kind = entry.pop("type", "").lower()
            if kind == "grid":
                grid_agents.append(GridSupplyAgent(**entry))
            elif kind == "solar":
                solar_agents.append(SolarEnergyAgent(**entry))
            elif kind == "battery":
                battery_agents.append(BatteryStorageAgent(**entry))
            else:
                raise ValidationError(f"Unknown supply source type {kind!r}")

        return cls(
            grid_agents=grid_agents,
            solar_agents=solar_agents,
            battery_agents=battery_agents,
            auto_charge_surplus_solar=bool(config.get("auto_charge_surplus_solar", True)),
            grid_charge_off_peak=bool(config.get("grid_charge_off_peak", True)),
            grid_charge_hours=tuple(config.get("grid_charge_hours", (0.0, 6.0))),
        )
