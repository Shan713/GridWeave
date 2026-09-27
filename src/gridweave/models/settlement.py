"""Settlement: what actually happened in a slot, measured against *realised* demand.

The bid is a forecast-based request; the allocation is what the market
granted; the settlement compares the allocation with the demand that
actually occurred. All service metrics come from settlements, never from
bids, so forecast errors have real consequences:

    forecast 30 kW -> bid 30 -> allocated 30 -> actual 40 -> 10 kW unserved

Deferred energy (the backlog) is tracked as a queue of energy (kWh) with
deadlines, see :class:`DeferredEnergy`. A kWh is counted as "deferred"
once, when it is first postponed; serving it later reduces the queue, and
if its deadline passes it is counted once as "expired".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from gridweave.models.allocation import AllocationStatus
from gridweave.models.common import TimeSlot
from gridweave.utils.validation import (
    POWER_TOLERANCE_KW,
    ValidationError,
    require_close,
    require_le,
    require_non_negative,
)


@dataclass(frozen=True)
class DeferredEnergy:
    """One deferred block of flexible energy waiting to be served.

    It was postponed in the slot starting at ``origin`` and may be served in
    any slot whose start is ``<= deadline``; after that it expires.
    """

    origin: datetime
    energy_kwh: float
    deadline: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "energy_kwh", require_non_negative("energy_kwh", self.energy_kwh))
        if self.deadline <= self.origin:
            raise ValidationError("DeferredEnergy.deadline must be after its origin")


@dataclass(frozen=True)
class Settlement:
    """Ex-post outcome of one slot for one building (all power values are slot-average kW).

    Quantities:

    * ``actual_demand_kw`` — realised *new* demand this slot (from the meter / environment).
    * ``backlog_available_kw`` — deferred energy waiting at the start of the slot (as kW over the slot).
    * ``backlog_attempted_kw`` — the part of it that fits under capacity this slot.
    * ``actual_total_kw = actual_demand_kw + backlog_attempted_kw`` — what the building needed.
    * ``actual_critical_kw`` / ``actual_minimum_kw`` — the load model applied to ``actual_demand_kw``.

    Conservation invariants (enforced):

    * ``served = min(allocated, actual_total)``;  ``served + unused = allocated``
    * ``critical_served + critical_shortfall = actual_critical``
    * ``served = critical_served + backlog_served + new_flexible_served``
    * ``actual_demand - actual_critical = new_flexible_served + deferred + curtailed``
    """

    bid_id: str
    building_id: str
    time_slot: TimeSlot
    status: AllocationStatus
    forecast_demand_kw: float
    actual_demand_kw: float
    backlog_available_kw: float
    backlog_attempted_kw: float
    actual_total_kw: float
    actual_critical_kw: float
    actual_minimum_kw: float
    requested_kw: float
    allocated_kw: float
    served_kw: float
    unused_allocation_kw: float
    critical_served_kw: float
    critical_shortfall_kw: float
    backlog_served_kw: float
    new_flexible_served_kw: float
    deferred_kw: float
    curtailed_kw: float
    backlog_expired_kwh: float
    backlog_after_kwh: float
    clearing_price: float | None = None
    energy_cost: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", AllocationStatus(self.status))
        for name in (
            "forecast_demand_kw", "actual_demand_kw", "backlog_available_kw", "backlog_attempted_kw",
            "actual_total_kw", "actual_critical_kw", "actual_minimum_kw", "requested_kw", "allocated_kw",
            "served_kw", "unused_allocation_kw", "critical_served_kw", "critical_shortfall_kw",
            "backlog_served_kw", "new_flexible_served_kw", "deferred_kw", "curtailed_kw",
            "backlog_expired_kwh", "backlog_after_kwh",
        ):
            object.__setattr__(self, name, require_non_negative(name, getattr(self, name)))
        require_close("actual_total", self.actual_total_kw, "actual_demand + backlog_attempted",
                      self.actual_demand_kw + self.backlog_attempted_kw)
        require_le("backlog_attempted_kw", self.backlog_attempted_kw, "backlog_available_kw",
                   self.backlog_available_kw)
        require_le("actual_critical_kw", self.actual_critical_kw, "actual_minimum_kw", self.actual_minimum_kw)
        require_le("actual_minimum_kw", self.actual_minimum_kw, "actual_total_kw", self.actual_total_kw)
        require_close("served_kw", self.served_kw, "min(allocated, actual_total)",
                      min(self.allocated_kw, self.actual_total_kw))
        require_close("served + unused", self.served_kw + self.unused_allocation_kw, "allocated_kw",
                      self.allocated_kw)
        require_close("critical served + shortfall", self.critical_served_kw + self.critical_shortfall_kw,
                      "actual_critical_kw", self.actual_critical_kw)
        require_close("critical + backlog + new flexible served",
                      self.critical_served_kw + self.backlog_served_kw + self.new_flexible_served_kw,
                      "served_kw", self.served_kw)
        require_le("backlog_served_kw", self.backlog_served_kw, "backlog_attempted_kw", self.backlog_attempted_kw)
        require_close("new flexible served + deferred + curtailed",
                      self.new_flexible_served_kw + self.deferred_kw + self.curtailed_kw,
                      "actual_demand - actual_critical", self.actual_demand_kw - self.actual_critical_kw)

    # ------------------------------------------------------------ views
    @property
    def forecast_error_kw(self) -> float:
        """``forecast - actual``: positive = over-forecast (allocation likely unused)."""
        return self.forecast_demand_kw - self.actual_demand_kw

    @property
    def has_critical_shortfall(self) -> bool:
        return self.critical_shortfall_kw > POWER_TOLERANCE_KW

    @property
    def minimum_met(self) -> bool:
        return self.served_kw + POWER_TOLERANCE_KW >= self.actual_minimum_kw

    @property
    def satisfaction_ratio(self) -> float:
        """``served / actual_total`` (1.0 when nothing was needed)."""
        return 1.0 if self.actual_total_kw <= 0 else self.served_kw / self.actual_total_kw

    def to_dict(self) -> dict:
        data = {k: getattr(self, k) for k in self.__dataclass_fields__}
        data["time_slot"] = self.time_slot.to_dict()
        data["status"] = self.status.value
        data["forecast_error_kw"] = self.forecast_error_kw
        data["has_critical_shortfall"] = self.has_critical_shortfall
        data["satisfaction_ratio"] = self.satisfaction_ratio
        return data


def settlement_status(served: float, total: float, minimum: float, critical: float) -> AllocationStatus:
    """Status of a settlement, judged against realised need."""
    tol = POWER_TOLERANCE_KW
    if total <= tol or served >= total - tol:
        return AllocationStatus.FULL
    if served <= tol:
        return AllocationStatus.NONE
    if served >= minimum - tol:
        return AllocationStatus.PARTIAL
    if served >= critical - tol:
        return AllocationStatus.BELOW_MINIMUM
    return AllocationStatus.CRITICAL_SHORTFALL
