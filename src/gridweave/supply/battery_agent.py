"""Autonomous Battery Energy Storage Agent with electrochemical efficiency and degradation modeling."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, DispatchResult, SourceType, SupplyOffer
from gridweave.utils.validation import (
    ValidationError,
    require_fraction,
    require_non_empty,
    require_non_negative,
    require_positive,
)


class BatteryOperatingMode(str, Enum):
    IDLE = "idle"
    CHARGE = "charge"
    DISCHARGE = "discharge"
    RESERVE = "reserve"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class BatteryReservePolicy:
    """Configurable reserve policy governing battery discharge thresholds.

    Attributes
    ----------
    physical_min_soc : float
        Absolute physical low voltage cutoff below which cell degradation accelerates (e.g. 0.10).
    operational_reserve_soc : float
        Standard operational floor reserved for unforecasted intra-day variations (e.g. 0.20).
    emergency_reserve_soc : float
        High-security floor activated during grid storm warnings or islanding preparation (e.g. 0.35).
    emergency_released : bool
        Whether coordinator has explicitly authorized dispatch down to physical minimum.
    """

    physical_min_soc: float = 0.10
    operational_reserve_soc: float = 0.20
    emergency_reserve_soc: float = 0.35
    emergency_released: bool = False

    def __post_init__(self) -> None:
        require_fraction("physical_min_soc", self.physical_min_soc)
        require_fraction("operational_reserve_soc", self.operational_reserve_soc)
        require_fraction("emergency_reserve_soc", self.emergency_reserve_soc)
        if not (self.physical_min_soc <= self.operational_reserve_soc <= self.emergency_reserve_soc):
            raise ValidationError(
                f"Reserve policy must satisfy physical_min <= operational <= emergency, got "
                f"[{self.physical_min_soc}, {self.operational_reserve_soc}, {self.emergency_reserve_soc}]"
            )

    @property
    def active_reserve_soc(self) -> float:
        """Effective minimum SOC below which market dispatch is prohibited."""
        if self.emergency_released:
            return self.physical_min_soc
        return self.operational_reserve_soc


@dataclass
class BatteryOperationalState:
    """Telemetry and physical accounting ledger for the Battery Agent."""

    total_discharged_kwh: float = 0.0
    total_charged_kwh: float = 0.0
    total_throughput_kwh: float = 0.0
    total_degradation_cost: float = 0.0
    equivalent_full_cycles: float = 0.0
    last_mode: BatteryOperatingMode = BatteryOperatingMode.IDLE
    last_dispatched_kw: float = 0.0
    last_charged_kw: float = 0.0
    dispatch_count: int = 0


class BatteryStorageAgent:
    """Autonomous agent modeling a campus Battery Energy Storage System (BESS).

    Adheres to rigorous thermodynamic and electrochemical principles:
      1. Distinguishes Power (kW) from Energy (kWh): Energy = Power * Slot Duration.
      2. Asymmetric charging (eta_c) and discharging (eta_d) efficiencies.
      3. Strict state-of-charge bounds: min_soc <= soc <= max_soc.
      4. Tiered reserve governance preventing auction over-extraction.
      5. Cycle-based degradation cost modeling ($/kWh throughput).
      6. Mutually exclusive charge/discharge operating modes per slot.
    """

    def __init__(
        self,
        source_id: str = "battery",
        capacity_kwh: float = 200.0,
        initial_soc: float = 0.80,
        max_charge_kw: float = 50.0,
        max_discharge_kw: float = 50.0,
        charge_efficiency: float = 0.95,
        discharge_efficiency: float = 0.95,
        min_soc: float = 0.10,
        max_soc: float = 0.95,
        degradation_cost_per_kwh: float = 7.0,  # e.g., currency units per kWh throughput
        reserve_policy: BatteryReservePolicy | None = None,
        initial_energy_cost_per_kwh: float = 0.0,
    ) -> None:
        self.source_id = require_non_empty("source_id", source_id)
        self.capacity_kwh = require_positive("capacity_kwh", capacity_kwh)
        self.max_charge_kw = require_non_negative("max_charge_kw", max_charge_kw)
        self.max_discharge_kw = require_non_negative("max_discharge_kw", max_discharge_kw)
        self.charge_efficiency = require_fraction("charge_efficiency", charge_efficiency)
        self.discharge_efficiency = require_fraction("discharge_efficiency", discharge_efficiency)
        self.min_soc = require_fraction("min_soc", min_soc)
        self.max_soc = require_fraction("max_soc", max_soc)
        require_fraction("initial_soc", initial_soc)

        if not (0.0 <= self.min_soc < self.max_soc <= 1.0):
            raise ValidationError(f"Invalid SOC limits [{self.min_soc}, {self.max_soc}]")
        if not (self.min_soc <= initial_soc <= self.max_soc):
            raise ValidationError(f"Initial SOC {initial_soc} not in [{self.min_soc}, {self.max_soc}]")

        self.initial_soc = initial_soc
        self.soc = initial_soc
        self.degradation_cost_per_kwh = require_non_negative("degradation_cost_per_kwh", degradation_cost_per_kwh)
        self.reserve_policy = reserve_policy or BatteryReservePolicy(
            physical_min_soc=self.min_soc,
            operational_reserve_soc=max(self.min_soc, 0.20),
            emergency_reserve_soc=max(self.min_soc, 0.35),
        )

        self.state = BatteryOperationalState()
        self.operating_mode: BatteryOperatingMode = BatteryOperatingMode.IDLE
        self._last_dispatched_slot: TimeSlot | None = None
        self._derated_max_discharge_kw: float | None = None
        # Average cost of the energy held in the cells (currency per stored kWh).
        self.initial_energy_cost_per_kwh = require_non_negative(
            "initial_energy_cost_per_kwh", initial_energy_cost_per_kwh
        )
        self._energy_cost_basis = self.initial_energy_cost_per_kwh

    @property
    def energy_cost_per_kwh(self) -> float:
        """Cost of the stored energy per kWh *delivered* (includes discharge losses)."""
        if self.discharge_efficiency <= 0:
            return 0.0
        return self._energy_cost_basis / self.discharge_efficiency

    @property
    def offer_price_per_kwh(self) -> float:
        """Marginal offer price: cell wear plus the cost of the energy being sold.

        Pricing only the wear cost made the market use the battery whenever it was
        cheaper than the grid (e.g. at 06:00), even when the stored energy was
        bought at a price that made that a loss.
        """
        return round(self.degradation_cost_per_kwh + self.energy_cost_per_kwh, 4)

    @property
    def stored_energy_kwh(self) -> float:
        """Instantaneous energy stored in battery chemistry (kWh)."""
        return self.soc * self.capacity_kwh

    @property
    def round_trip_efficiency(self) -> float:
        """AC-to-AC round-trip storage efficiency."""
        return self.charge_efficiency * self.discharge_efficiency

    def available_discharge_kw(self, slot: TimeSlot, respect_reserve: bool = True) -> float:
        """Calculate maximum AC power (kW) deliverable over the slot.

        Discharge equation:
          usable_kwh = (soc - floor_soc) * capacity_kwh
          max_power_from_energy = (usable_kwh * discharge_efficiency) / slot.hours
          deliverable_kw = min(max_discharge_kw, max_power_from_energy)
        """
        if self.operating_mode == BatteryOperatingMode.UNAVAILABLE:
            return 0.0

        floor_soc = self.reserve_policy.active_reserve_soc if respect_reserve else self.min_soc
        usable_kwh = max(0.0, (self.soc - floor_soc) * self.capacity_kwh)
        power_from_energy_kw = (usable_kwh * self.discharge_efficiency) / slot.hours
        configured_limit = self._derated_max_discharge_kw
        power_limit = (
            self.max_discharge_kw
            if configured_limit is None
            else min(self.max_discharge_kw, configured_limit)
        )
        return round(max(0.0, min(power_limit, power_from_energy_kw)), 4)

    def available_charge_kw(self, slot: TimeSlot) -> float:
        """Calculate maximum AC charging power (kW) acceptable over the slot.

        Charging equation:
          headroom_kwh = (max_soc - soc) * capacity_kwh
          max_power_to_headroom = (headroom_kwh / charge_efficiency) / slot.hours
          acceptable_kw = min(max_charge_kw, max_power_to_headroom)
        """
        if self.operating_mode == BatteryOperatingMode.UNAVAILABLE:
            return 0.0

        headroom_kwh = max(0.0, (self.max_soc - self.soc) * self.capacity_kwh)
        power_to_headroom_kw = (headroom_kwh / self.charge_efficiency) / slot.hours
        return round(max(0.0, min(self.max_charge_kw, power_to_headroom_kw)), 4)

    def get_offer(self, slot: TimeSlot) -> SupplyOffer:
        """Generate a valid SupplyOffer for the auction engine.

        Pure query: Strictly does not mutate internal SOC or charge state.
        The marginal price reflects the battery's degradation cost + opportunity value.
        """
        available_kw = self.available_discharge_kw(slot, respect_reserve=True)
        decision_trace = self._generate_decision_trace(slot, available_kw)

        constraints = {
            "soc": round(self.soc, 4),
            "min_soc": self.min_soc,
            "max_soc": self.max_soc,
            "active_reserve_soc": self.reserve_policy.active_reserve_soc,
            "capacity_kwh": self.capacity_kwh,
            "max_discharge_kw": self.max_discharge_kw,
            "max_charge_kw": self.max_charge_kw,
            "mode": self.operating_mode.value,
            "degradation_cost_per_kwh": self.degradation_cost_per_kwh,
            "energy_cost_per_kwh": round(self.energy_cost_per_kwh, 4),
            "decision_trace": decision_trace,
        }

        return SupplyOffer(
            source_id=self.source_id,
            source_type=SourceType.BATTERY,
            time_slot=slot,
            available_kw=available_kw,
            marginal_price=self.offer_price_per_kwh,
            constraints=constraints,
        )

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        """Execute physical discharge instruction from market clearing.

        Enforces physical electrochemical state update:
          delivered_kwh = delivered_kw * slot.hours
          energy_drawn_from_chemistry = delivered_kwh / discharge_efficiency
          soc_new = soc_old - (energy_drawn_from_chemistry / capacity_kwh)
        """
        if request.source_id != self.source_id:
            raise ValidationError(
                f"Battery Agent {self.source_id!r} received dispatch for wrong source {request.source_id!r}"
            )

        slot = request.time_slot
        # Available discharge respecting current active reserve
        avail_kw = self.available_discharge_kw(slot, respect_reserve=True)
        delivered_kw = min(request.requested_kw, avail_kw)

        del_kwh = delivered_kw * slot.hours
        chem_drawn_kwh = del_kwh / self.discharge_efficiency if self.discharge_efficiency > 0 else 0.0

        # State of Charge update
        delta_soc = chem_drawn_kwh / self.capacity_kwh
        self.soc = max(self.min_soc, min(self.max_soc, self.soc - delta_soc))

        # Operational tracking
        deg_cost = del_kwh * self.degradation_cost_per_kwh
        self.state.total_discharged_kwh += del_kwh
        self.state.total_throughput_kwh += del_kwh
        self.state.total_degradation_cost += deg_cost
        self.state.equivalent_full_cycles = self.state.total_throughput_kwh / (2.0 * self.capacity_kwh)
        self.state.last_mode = BatteryOperatingMode.DISCHARGE if delivered_kw > 0 else BatteryOperatingMode.IDLE
        self.state.last_dispatched_kw = delivered_kw
        self.state.last_charged_kw = 0.0
        self.state.dispatch_count += 1
        self.operating_mode = self.state.last_mode
        self._last_dispatched_slot = slot

        # Remaining capacity after this dispatch
        remaining_kw = self.available_discharge_kw(slot, respect_reserve=True)

        return DispatchResult(
            source_id=self.source_id,
            time_slot=slot,
            requested_kw=request.requested_kw,
            delivered_kw=min(request.requested_kw, round(delivered_kw, 6)),
            remaining_capacity_kw=round(remaining_kw, 6),
            state={
                "soc": round(self.soc, 6),
                "stored_energy_kwh": round(self.stored_energy_kwh, 4),
                "delivered_kwh": round(del_kwh, 4),
                "chemical_drawn_kwh": round(chem_drawn_kwh, 4),
                "efficiency_loss_kwh": round(chem_drawn_kwh - del_kwh, 4),
                "operating_mode": self.operating_mode.value,
                "degradation_cost": round(deg_cost, 4),
            },
        )

    def charge(self, power_kw: float, slot: TimeSlot, source_price_per_kwh: float = 0.0) -> float:
        """Directly charge the battery (e.g. from surplus solar or off-peak grid).

        Enforces charging physics:
          accepted_power_kw = min(power_kw, available_charge_kw)
          chem_stored_kwh = (accepted_power_kw * slot.hours) * charge_efficiency
          soc_new = soc_old + (chem_stored_kwh / capacity_kwh)

        ``source_price_per_kwh`` is what the charging energy cost (0 for surplus
        solar, the tariff for grid energy). It updates the weighted-average cost
        of the dispatchable stored energy, which feeds the offer price.

        Returns
        -------
        float
            Actual AC power absorbed (kW).
        """
        require_non_negative("power_kw", power_kw)
        if power_kw <= 0.001 or self.operating_mode == BatteryOperatingMode.UNAVAILABLE:
            return 0.0

        max_accept_kw = self.available_charge_kw(slot)
        accepted_kw = min(power_kw, max_accept_kw)

        require_non_negative("source_price_per_kwh", source_price_per_kwh)
        ac_energy_kwh = accepted_kw * slot.hours
        chem_stored_kwh = ac_energy_kwh * self.charge_efficiency

        # Weighted-average cost of dispatchable energy (above the physical minimum)
        held_kwh = max(0.0, (self.soc - self.min_soc) * self.capacity_kwh)
        if held_kwh + chem_stored_kwh > 0:
            self._energy_cost_basis = (
                held_kwh * self._energy_cost_basis + ac_energy_kwh * source_price_per_kwh
            ) / (held_kwh + chem_stored_kwh)

        # State of Charge update
        delta_soc = chem_stored_kwh / self.capacity_kwh
        self.soc = max(self.min_soc, min(self.max_soc, self.soc + delta_soc))

        # Operational tracking
        self.state.total_charged_kwh += ac_energy_kwh
        self.state.total_throughput_kwh += ac_energy_kwh
        self.state.equivalent_full_cycles = self.state.total_throughput_kwh / (2.0 * self.capacity_kwh)
        self.state.last_mode = BatteryOperatingMode.CHARGE if accepted_kw > 0 else BatteryOperatingMode.IDLE
        self.state.last_charged_kw = accepted_kw
        self.state.last_dispatched_kw = 0.0
        self.operating_mode = self.state.last_mode

        return round(accepted_kw, 4)

    def release_emergency_reserve(self, release: bool = True) -> None:
        """Permit emergency discharge down to physical minimum SOC."""
        object.__setattr__(self.reserve_policy, "emergency_released", bool(release))

    def set_unavailable(self, unavailable: bool = True) -> None:
        """Simulate battery outage or maintenance derating."""
        self.operating_mode = BatteryOperatingMode.UNAVAILABLE if unavailable else BatteryOperatingMode.IDLE

    def set_derated_discharge_kw(self, max_discharge_kw: float | None) -> None:
        """Set or clear a temporary discharge-power limit."""
        if max_discharge_kw is not None:
            require_non_negative("max_discharge_kw", max_discharge_kw)
        self._derated_max_discharge_kw = max_discharge_kw

    def _generate_decision_trace(self, slot: TimeSlot, available_kw: float) -> str:
        """Algorithmic explanation of offer generation rationale."""
        reserve = self.reserve_policy.active_reserve_soc
        if self.operating_mode == BatteryOperatingMode.UNAVAILABLE:
            return "Battery is offline/unavailable; offered 0.0 kW."
        if self.soc <= reserve + 0.001:
            return f"Current SOC ({self.soc:.1%}) is at or below active reserve ({reserve:.1%}); offered 0.0 kW."
        return (
            f"Current SOC: {self.soc:.1%}, Reserve: {reserve:.1%}. "
            f"Offered {available_kw:.1f} kW based on electrochemical discharge limits "
            f"and duration {slot.duration_minutes}m."
        )

    def reset(self) -> None:
        """Reset SOC and operational telemetry for deterministic replay."""
        self.soc = self.initial_soc
        self.state = BatteryOperationalState()
        self.operating_mode = BatteryOperatingMode.IDLE
        object.__setattr__(self.reserve_policy, "emergency_released", False)
        self._last_dispatched_slot = None
        self._derated_max_discharge_kw = None
        self._energy_cost_basis = self.initial_energy_cost_per_kwh

    def snapshot(self) -> dict[str, Any]:
        """Telemetry snapshot for P4 coordinator and dashboard."""
        return {
            "source_id": self.source_id,
            "capacity_kwh": self.capacity_kwh,
            "soc": round(self.soc, 4),
            "stored_energy_kwh": round(self.stored_energy_kwh, 4),
            "operating_mode": self.operating_mode.value,
            "total_discharged_kwh": round(self.state.total_discharged_kwh, 4),
            "total_charged_kwh": round(self.state.total_charged_kwh, 4),
            "total_degradation_cost": round(self.state.total_degradation_cost, 4),
            "equivalent_full_cycles": round(self.state.equivalent_full_cycles, 4),
            "emergency_released": self.reserve_policy.emergency_released,
            "derated_max_discharge_kw": self._derated_max_discharge_kw,
            "dispatch_count": self.state.dispatch_count,
            "energy_cost_per_kwh": round(self.energy_cost_per_kwh, 4),
            "offer_price_per_kwh": self.offer_price_per_kwh,
        }
