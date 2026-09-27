"""Supply-side contracts: what P3 offers, what P2 clears, what P3 dispatches.

These types let P2 run a genuine market (several sources with different
prices and limits) and let P3 update physical state (e.g. battery state of
charge) after dispatch. They contain no market or battery *logic*.

Flow for one slot::

    P3  offers(slot)            -> [SupplyOffer]           (source limits + marginal prices)
    P2  clear(slot, bids, offers) -> ClearingResult         (allocations + DispatchRequests)
    P3  dispatch(requests)      -> [DispatchResult]        (delivered kW + new source state)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from gridweave.models.allocation import Allocation
from gridweave.models.common import TimeSlot
from gridweave.utils.validation import (
    ValidationError,
    require_le,
    require_non_empty,
    require_non_negative,
)


class SourceType(str, Enum):
    GRID = "grid"
    SOLAR = "solar"
    BATTERY = "battery"
    OTHER = "other"


@dataclass(frozen=True)
class SupplyOffer:
    """One source's offer for one slot.

    * ``available_kw`` — the most this source can deliver on average over the slot.
    * ``marginal_price`` — currency/kWh the source asks (e.g. grid tariff 10,
      solar 0, battery 7 reflecting wear/opportunity cost).
    * ``constraints`` — free-form, source-specific information P2 may use
      (e.g. ``{"soc": 0.8}``). The core never interprets it.
    """

    source_id: str
    source_type: SourceType
    time_slot: TimeSlot
    available_kw: float
    marginal_price: float
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty("source_id", self.source_id)
        try:
            object.__setattr__(self, "source_type", SourceType(self.source_type))
        except ValueError as exc:
            raise ValidationError(f"unknown source_type {self.source_type!r}") from exc
        if not isinstance(self.time_slot, TimeSlot):
            raise ValidationError("SupplyOffer.time_slot must be a TimeSlot")
        object.__setattr__(self, "available_kw", require_non_negative("available_kw", self.available_kw))
        object.__setattr__(self, "marginal_price", require_non_negative("marginal_price", self.marginal_price))
        object.__setattr__(self, "constraints", dict(self.constraints))

    def to_dict(self) -> dict:
        return {"source_id": self.source_id, "source_type": self.source_type.value,
                "time_slot": self.time_slot.to_dict(), "available_kw": self.available_kw,
                "marginal_price": self.marginal_price, "constraints": dict(self.constraints)}


@dataclass(frozen=True)
class DispatchRequest:
    """P2's instruction to a source: deliver ``requested_kw`` during ``time_slot``."""

    source_id: str
    time_slot: TimeSlot
    requested_kw: float

    def __post_init__(self) -> None:
        require_non_empty("source_id", self.source_id)
        if not isinstance(self.time_slot, TimeSlot):
            raise ValidationError("DispatchRequest.time_slot must be a TimeSlot")
        object.__setattr__(self, "requested_kw", require_non_negative("requested_kw", self.requested_kw))

    def to_dict(self) -> dict:
        return {"source_id": self.source_id, "time_slot": self.time_slot.to_dict(),
                "requested_kw": self.requested_kw}


@dataclass(frozen=True)
class DispatchResult:
    """What a source actually delivered, and its state afterwards (owned by P3).

    ``delivered_kw <= requested_kw`` always; a shortfall means the source
    could not honour the dispatch (e.g. battery hit its minimum SOC).
    ``state`` is source-specific (e.g. ``{"soc": 0.72}``).
    """

    source_id: str
    time_slot: TimeSlot
    requested_kw: float
    delivered_kw: float
    remaining_capacity_kw: float
    state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty("source_id", self.source_id)
        if not isinstance(self.time_slot, TimeSlot):
            raise ValidationError("DispatchResult.time_slot must be a TimeSlot")
        for name in ("requested_kw", "delivered_kw", "remaining_capacity_kw"):
            object.__setattr__(self, name, require_non_negative(name, getattr(self, name)))
        require_le("delivered_kw", self.delivered_kw, "requested_kw", self.requested_kw)
        object.__setattr__(self, "state", dict(self.state))

    @property
    def shortfall_kw(self) -> float:
        return self.requested_kw - self.delivered_kw

    def to_dict(self) -> dict:
        return {"source_id": self.source_id, "time_slot": self.time_slot.to_dict(),
                "requested_kw": self.requested_kw, "delivered_kw": self.delivered_kw,
                "remaining_capacity_kw": self.remaining_capacity_kw, "state": dict(self.state)}


@dataclass(frozen=True)
class ClearingResult:
    """Output of P2's market clearing for one slot."""

    time_slot: TimeSlot
    allocations: tuple[Allocation, ...]
    dispatch: tuple[DispatchRequest, ...]
    clearing_price: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.time_slot, TimeSlot):
            raise ValidationError("ClearingResult.time_slot must be a TimeSlot")
        object.__setattr__(self, "allocations", tuple(self.allocations))
        object.__setattr__(self, "dispatch", tuple(self.dispatch))
        for item in (*self.allocations, *self.dispatch):
            if item.time_slot != self.time_slot:
                raise ValidationError(f"{type(item).__name__} for {item.time_slot} in clearing for {self.time_slot}")
        bid_ids = [a.bid_id for a in self.allocations]
        if len(bid_ids) != len(set(bid_ids)):
            raise ValidationError("ClearingResult contains more than one allocation for the same bid")
        sources = [d.source_id for d in self.dispatch]
        if len(sources) != len(set(sources)):
            raise ValidationError("ClearingResult contains more than one dispatch request for the same source")
        if self.clearing_price is not None:
            object.__setattr__(self, "clearing_price", require_non_negative("clearing_price", self.clearing_price))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def total_allocated_kw(self) -> float:
        return sum(a.allocated_power_kw for a in self.allocations)

    @property
    def total_dispatched_kw(self) -> float:
        return sum(d.requested_kw for d in self.dispatch)

    def to_dict(self) -> dict:
        return {"time_slot": self.time_slot.to_dict(), "allocations": [a.to_dict() for a in self.allocations],
                "dispatch": [d.to_dict() for d in self.dispatch], "clearing_price": self.clearing_price,
                "metadata": dict(self.metadata)}
