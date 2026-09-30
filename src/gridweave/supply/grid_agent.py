"""Autonomous Grid Supply Agent representing external power grid imports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, DispatchResult, SourceType, SupplyOffer
from gridweave.supply.profiles.grid import GridProfile
from gridweave.supply.profiles.tariff import TariffSchedule
from gridweave.utils.validation import ValidationError, require_non_empty


@dataclass
class GridOperationalState:
    """Historical and instantaneous telemetry for the Grid Agent."""

    total_imported_kwh: float = 0.0
    total_cost: float = 0.0
    peak_imported_kw: float = 0.0
    last_dispatched_kw: float = 0.0
    outage_active: bool = False
    dispatch_count: int = 0


class GridSupplyAgent:
    """Autonomous agent modeling electricity imported from the external power grid.

    Responsibilities:
      - Enforces physical transmission import limits
      - Dynamically tracks time-of-use tariffs (off-peak, standard, peak)
      - Models scheduled maintenance windows and unexpected blackout events
      - Generates valid, non-mutating SupplyOffers for P2/P4
      - Safely executes accepted DispatchRequests within physical constraints
      - Accurately accumulates imported energy (kWh) and procurement costs ($)
    """

    def __init__(
        self,
        source_id: str = "grid",
        profile: GridProfile | None = None,
        tariff_schedule: TariffSchedule | None = None,
        nominal_capacity_kw: float = 500.0,
    ) -> None:
        self.source_id = require_non_empty("source_id", source_id)
        self.profile = profile or GridProfile(nominal_capacity_kw=nominal_capacity_kw)
        self.tariff_schedule = tariff_schedule or TariffSchedule()
        self.state = GridOperationalState()
        self._last_offer_slot: TimeSlot | None = None

    @property
    def nominal_capacity_kw(self) -> float:
        return self.profile.nominal_capacity_kw

    def available_kw(self, slot: TimeSlot) -> float:
        """Determine maximum deliverable power (kW) for the given TimeSlot."""
        return self.profile.get_available_capacity(slot)

    def current_tariff(self, slot: TimeSlot) -> float:
        """Determine applicable marginal electricity tariff ($/kWh)."""
        return self.tariff_schedule.get_rate(slot)

    def get_offer(self, slot: TimeSlot) -> SupplyOffer:
        """Generate a valid SupplyOffer for the auction engine.

        Pure query: Strictly does not mutate agent state.
        """
        available = self.available_kw(slot)
        tariff = self.current_tariff(slot)
        is_peak = self.tariff_schedule.is_peak(slot)

        constraints = {
            "nominal_capacity_kw": self.nominal_capacity_kw,
            "is_peak": is_peak,
            "outage": self.profile.emergency_outage or available <= 0.0,
        }

        self._last_offer_slot = slot
        return SupplyOffer(
            source_id=self.source_id,
            source_type=SourceType.GRID,
            time_slot=slot,
            available_kw=available,
            marginal_price=tariff,
            constraints=constraints,
        )

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        """Execute physical dispatch instruction from the market clearing engine.

        Verifies request matches source_id and does not exceed physical limits.
        Updates cumulative energy (kWh) and procurement financial ledger.
        """
        if request.source_id != self.source_id:
            raise ValidationError(
                f"Grid Agent {self.source_id!r} received dispatch for wrong source {request.source_id!r}"
            )

        slot = request.time_slot
        available = self.available_kw(slot)
        # Physical delivery cannot exceed physical availability
        delivered = min(request.requested_kw, available)
        energy_kwh = delivered * slot.hours
        cost = energy_kwh * self.current_tariff(slot)

        # Mutate operational state
        self.state.total_imported_kwh += energy_kwh
        self.state.total_cost += cost
        self.state.peak_imported_kw = max(self.state.peak_imported_kw, delivered)
        self.state.last_dispatched_kw = delivered
        self.state.outage_active = available <= 0.0
        self.state.dispatch_count += 1

        remaining = max(0.0, available - delivered)

        return DispatchResult(
            source_id=self.source_id,
            time_slot=slot,
            requested_kw=request.requested_kw,
            delivered_kw=min(request.requested_kw, round(delivered, 6)),
            remaining_capacity_kw=round(remaining, 6),
            state={
                "tariff_rate": self.current_tariff(slot),
                "energy_kwh": round(energy_kwh, 4),
                "total_cost": round(self.state.total_cost, 4),
                "outage": self.state.outage_active,
            },
        )

    def import_for_storage(self, power_kw: float, slot: TimeSlot, already_delivered_kw: float = 0.0) -> float:
        """Import power for on-site storage (battery charging), outside the market.

        Uses only spare capacity (``available - already_delivered_kw``) so campus
        supply is never displaced. The import and its cost are recorded like any
        other grid import. Returns the accepted power (kW).
        """
        if power_kw <= 0.0:
            return 0.0
        spare = max(0.0, self.available_kw(slot) - already_delivered_kw)
        accepted = min(power_kw, spare)
        if accepted <= 0.0:
            return 0.0
        energy_kwh = accepted * slot.hours
        self.state.total_imported_kwh += energy_kwh
        self.state.total_cost += energy_kwh * self.current_tariff(slot)
        self.state.peak_imported_kw = max(self.state.peak_imported_kw, already_delivered_kw + accepted)
        return round(accepted, 6)

    def trigger_outage(self) -> None:
        """Simulate unexpected grid blackout event."""
        self.profile.set_emergency_outage(True)
        self.state.outage_active = True

    def restore_grid(self) -> None:
        """Restore grid following an outage."""
        self.profile.set_emergency_outage(False)
        self.state.outage_active = False

    def set_tariff_multiplier(self, multiplier: float) -> None:
        """Trigger price spike or emergency tariff multiplier."""
        self.tariff_schedule.set_multiplier(multiplier)

    def reset(self) -> None:
        """Reset operational telemetry for reproducible simulation runs."""
        self.state = GridOperationalState()
        self.profile.clear_outages()
        self.tariff_schedule.reset_multiplier()
        self._last_offer_slot = None

    def snapshot(self) -> dict[str, Any]:
        """Telemetry snapshot for P4 coordinator and dashboard."""
        return {
            "source_id": self.source_id,
            "nominal_capacity_kw": self.nominal_capacity_kw,
            "total_imported_kwh": round(self.state.total_imported_kwh, 4),
            "total_cost": round(self.state.total_cost, 4),
            "peak_imported_kw": round(self.state.peak_imported_kw, 4),
            "outage_active": self.state.outage_active,
            "dispatch_count": self.state.dispatch_count,
        }
