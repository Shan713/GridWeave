"""Allocation (auction -> building) and AllocationOutcome (ex-ante preview of an
allocation against its bid; service outcomes are in :class:`gridweave.models.Settlement`)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from gridweave.models.common import TimeSlot
from gridweave.utils.validation import (
    POWER_TOLERANCE_KW,
    ValidationError,
    require_close,
    require_non_empty,
    require_non_negative,
)


@dataclass(frozen=True)
class Allocation:
    """Power granted to one bid for one slot. Produced by the auction (P2).

    ``supply_mix`` optionally breaks the allocation down by source
    (e.g. ``{"grid": 20.0, "solar": 8.0, "battery": 4.0}``) — filled in by the
    supply side (P3/P4). If given, it must sum to ``allocated_power_kw``.
    ``clearing_price`` is currency/kWh; ``None`` means "not priced".

    An allocation larger than the request is legal (the building simply
    leaves the excess unused and reports it back).
    """

    bid_id: str
    building_id: str
    time_slot: TimeSlot
    allocated_power_kw: float
    clearing_price: float | None = None
    supply_mix: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty("bid_id", self.bid_id)
        require_non_empty("building_id", self.building_id)
        if not isinstance(self.time_slot, TimeSlot):
            raise ValidationError("Allocation.time_slot must be a TimeSlot")
        object.__setattr__(
            self, "allocated_power_kw", require_non_negative("allocated_power_kw", self.allocated_power_kw)
        )
        if self.clearing_price is not None:
            object.__setattr__(self, "clearing_price", require_non_negative("clearing_price", self.clearing_price))
        mix = {str(k): require_non_negative(f"supply_mix[{k}]", v) for k, v in dict(self.supply_mix).items()}
        if mix:
            require_close("sum(supply_mix)", sum(mix.values()), "allocated_power_kw", self.allocated_power_kw)
        object.__setattr__(self, "supply_mix", mix)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict:
        return {
            "bid_id": self.bid_id,
            "building_id": self.building_id,
            "time_slot": self.time_slot.to_dict(),
            "allocated_power_kw": self.allocated_power_kw,
            "clearing_price": self.clearing_price,
            "supply_mix": dict(self.supply_mix),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Allocation":
        return cls(
            bid_id=data["bid_id"],
            building_id=data["building_id"],
            time_slot=TimeSlot.from_dict(data["time_slot"]),
            allocated_power_kw=data["allocated_power_kw"],
            clearing_price=data.get("clearing_price"),
            supply_mix=data.get("supply_mix", {}),
            metadata=data.get("metadata", {}),
        )


class AllocationStatus(str, Enum):
    """How well an allocation covered a bid, from best to worst."""

    FULL = "full"                              # accepted == requested
    PARTIAL = "partial"                        # minimum <= accepted < requested
    BELOW_MINIMUM = "below_minimum"            # critical <= accepted < minimum
    CRITICAL_SHORTFALL = "critical_shortfall"  # 0 < accepted < critical
    NONE = "none"                              # accepted == 0 < requested


@dataclass(frozen=True)
class AllocationOutcome:
    """The Building Agent's local response to an allocation.

    Invariants:

    * ``accepted = min(allocated, requested)``; ``unused = allocated - accepted``
    * ``critical_served + critical_shortfall == critical_requested``
    * ``flexible_served + flexible_deferred + flexible_curtailed == flexible_requested``
    * ``critical_served + flexible_served == accepted``
    """

    bid_id: str
    building_id: str
    time_slot: TimeSlot
    status: AllocationStatus
    requested_kw: float
    minimum_kw: float
    allocated_kw: float
    accepted_kw: float
    unused_allocation_kw: float
    critical_requested_kw: float
    critical_served_kw: float
    critical_shortfall_kw: float
    flexible_requested_kw: float
    flexible_served_kw: float
    flexible_deferred_kw: float
    flexible_curtailed_kw: float
    energy_cost: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", AllocationStatus(self.status))
        for name in (
            "requested_kw", "minimum_kw", "allocated_kw", "accepted_kw", "unused_allocation_kw",
            "critical_requested_kw", "critical_served_kw", "critical_shortfall_kw",
            "flexible_requested_kw", "flexible_served_kw", "flexible_deferred_kw", "flexible_curtailed_kw",
        ):
            object.__setattr__(self, name, require_non_negative(name, getattr(self, name)))
        require_close("accepted_kw", self.accepted_kw, "min(allocated, requested)",
                      min(self.allocated_kw, self.requested_kw))
        require_close("accepted + unused", self.accepted_kw + self.unused_allocation_kw,
                      "allocated_kw", self.allocated_kw)
        require_close("critical served + shortfall", self.critical_served_kw + self.critical_shortfall_kw,
                      "critical_requested_kw", self.critical_requested_kw)
        require_close(
            "flexible served + deferred + curtailed",
            self.flexible_served_kw + self.flexible_deferred_kw + self.flexible_curtailed_kw,
            "flexible_requested_kw", self.flexible_requested_kw,
        )
        require_close("critical_served + flexible_served", self.critical_served_kw + self.flexible_served_kw,
                      "accepted_kw", self.accepted_kw)

    @property
    def has_critical_shortfall(self) -> bool:
        """Safety event: some critical load could not be served."""
        return self.critical_shortfall_kw > POWER_TOLERANCE_KW

    @property
    def minimum_met(self) -> bool:
        return self.accepted_kw + POWER_TOLERANCE_KW >= self.minimum_kw

    @property
    def satisfaction_ratio(self) -> float:
        """``accepted / requested`` (1.0 when nothing was requested)."""
        return 1.0 if self.requested_kw <= 0 else self.accepted_kw / self.requested_kw

    def to_dict(self) -> dict:
        data = {k: getattr(self, k) for k in self.__dataclass_fields__}
        data["time_slot"] = self.time_slot.to_dict()
        data["status"] = self.status.value
        data["minimum_met"] = self.minimum_met
        data["has_critical_shortfall"] = self.has_critical_shortfall
        data["satisfaction_ratio"] = self.satisfaction_ratio
        return data
