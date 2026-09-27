"""The Bid: the stable contract between Building Agents (P1) and the auction (P2).

A bid says *what* a building wants for one time slot and *how much it values
it*. It deliberately says nothing about *how* bids compete — that is owned by
the auction mechanism. See ``docs/bid_contract.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from gridweave.models.common import TimeSlot
from gridweave.utils.validation import (
    ValidationError,
    require_close,
    require_fraction,
    require_le,
    require_non_empty,
    require_non_negative,
)

BID_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class Bid:
    """Immutable, self-validating power request for one market slot.

    Power fields are average kW over ``time_slot``; prices are currency/kWh.

    Guaranteed invariants (the auction may rely on all of these):

    * ``0 <= critical_power_kw <= minimum_power_kw <= requested_power_kw``
    * ``critical_power_kw + flexible_power_kw == requested_power_kw``
    * ``0 <= priority_score <= 1`` and ``0 <= flexibility_score <= 1``
    * ``0 <= willingness_to_pay <= maximum_price``
    """

    bid_id: str
    building_id: str
    time_slot: TimeSlot
    created_at: datetime
    requested_power_kw: float
    minimum_power_kw: float
    critical_power_kw: float
    flexible_power_kw: float
    priority_score: float
    flexibility_score: float
    willingness_to_pay: float
    maximum_price: float
    revision: int = 0
    explanation: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = BID_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_non_empty("bid_id", self.bid_id)
        require_non_empty("building_id", self.building_id)
        if not isinstance(self.time_slot, TimeSlot):
            raise ValidationError("Bid.time_slot must be a TimeSlot")
        if not isinstance(self.created_at, datetime):
            raise ValidationError("Bid.created_at must be a datetime")
        for name in (
            "requested_power_kw",
            "minimum_power_kw",
            "critical_power_kw",
            "flexible_power_kw",
            "willingness_to_pay",
            "maximum_price",
        ):
            object.__setattr__(self, name, require_non_negative(name, getattr(self, name)))
        require_fraction("priority_score", self.priority_score)
        require_fraction("flexibility_score", self.flexibility_score)
        require_le("critical_power_kw", self.critical_power_kw, "minimum_power_kw", self.minimum_power_kw)
        require_le("minimum_power_kw", self.minimum_power_kw, "requested_power_kw", self.requested_power_kw)
        require_close(
            "critical + flexible", self.critical_power_kw + self.flexible_power_kw,
            "requested_power_kw", self.requested_power_kw,
        )
        require_le("willingness_to_pay", self.willingness_to_pay, "maximum_price", self.maximum_price)
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValidationError("revision must be an int >= 0")
        object.__setattr__(self, "explanation", dict(self.explanation))

    # ------------------------------------------------------------------ helpers
    @property
    def curtailable_power_kw(self) -> float:
        """Power the building can live without this slot: ``requested - minimum``."""
        return self.requested_power_kw - self.minimum_power_kw

    @property
    def requested_energy_kwh(self) -> float:
        return self.requested_power_kw * self.time_slot.hours

    def to_dict(self) -> dict:
        """JSON-serialisable representation (stable field names)."""
        return {
            "schema_version": self.schema_version,
            "bid_id": self.bid_id,
            "building_id": self.building_id,
            "time_slot": self.time_slot.to_dict(),
            "created_at": self.created_at.isoformat(),
            "requested_power_kw": self.requested_power_kw,
            "minimum_power_kw": self.minimum_power_kw,
            "critical_power_kw": self.critical_power_kw,
            "flexible_power_kw": self.flexible_power_kw,
            "priority_score": self.priority_score,
            "flexibility_score": self.flexibility_score,
            "willingness_to_pay": self.willingness_to_pay,
            "maximum_price": self.maximum_price,
            "revision": self.revision,
            "explanation": dict(self.explanation),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Bid":
        return cls(
            bid_id=data["bid_id"],
            building_id=data["building_id"],
            time_slot=TimeSlot.from_dict(data["time_slot"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            requested_power_kw=data["requested_power_kw"],
            minimum_power_kw=data["minimum_power_kw"],
            critical_power_kw=data["critical_power_kw"],
            flexible_power_kw=data["flexible_power_kw"],
            priority_score=data["priority_score"],
            flexibility_score=data["flexibility_score"],
            willingness_to_pay=data["willingness_to_pay"],
            maximum_price=data["maximum_price"],
            revision=data.get("revision", 0),
            explanation=data.get("explanation", {}),
            schema_version=data.get("schema_version", BID_SCHEMA_VERSION),
        )
