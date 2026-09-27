"""Static description of a building: what it is and how its load behaves.

A :class:`BuildingSpec` is configuration, not state. It never changes during
a simulation; everything that evolves over time lives in
:class:`gridweave.models.demand.DemandState` and in the agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from gridweave.utils.validation import (
    ValidationError,
    require_fraction,
    require_le,
    require_non_empty,
    require_non_negative,
    require_positive,
)


class BuildingType(str, Enum):
    """Campus building categories. Each maps to a default demand profile."""

    HOSTEL = "hostel"
    LAB = "lab"
    ACADEMIC = "academic"
    LIBRARY = "library"
    ADMIN = "admin"
    OTHER = "other"


@dataclass(frozen=True)
class BuildingSpec:
    """Immutable, validated building configuration.

    Load model (see ``docs/demand_model.md``): for a desired demand ``D`` in a
    slot the agent classifies

    * ``critical = min(D, max(minimum_operational_kw, critical_fraction * D))``
      — loads that must never be curtailed (servers, lab equipment, safety
      lighting, refrigeration);
    * ``flexible = D - critical`` — loads that may be reduced (HVAC set-points,
      water heating, charging, laundry);
    * ``minimum = critical + min_flexible_fraction * flexible`` — the lowest
      service level the building accepts without breaching comfort limits.

    When a flexible load is not served, ``deferrable_fraction`` of it is
    shifted to later slots (backlog) and the rest is curtailed (lost).
    Deferred energy expires if it is not served within ``max_deferral_slots``.

    Under a scarcity signal the building voluntarily trims its request by
    ``scarcity * scarcity_response * (requested - minimum)`` (0 = never).
    """

    building_id: str
    name: str
    building_type: BuildingType
    capacity_kw: float
    minimum_operational_kw: float = 0.0
    critical_fraction: float = 0.5
    min_flexible_fraction: float = 0.0
    deferrable_fraction: float = 1.0
    importance: float = 0.5
    forecast_horizon: int = 4
    base_price_per_kwh: float = 6.0
    max_price_per_kwh: float = 12.0
    max_backlog_kw: float | None = None
    max_deferral_slots: int = 8
    scarcity_response: float = 0.5
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty("building_id", self.building_id)
        require_non_empty("name", self.name)
        try:
            object.__setattr__(self, "building_type", BuildingType(self.building_type))
        except ValueError as exc:
            raise ValidationError(f"unknown building_type {self.building_type!r}") from exc
        require_positive("capacity_kw", self.capacity_kw)
        require_non_negative("minimum_operational_kw", self.minimum_operational_kw)
        require_le("minimum_operational_kw", self.minimum_operational_kw, "capacity_kw", self.capacity_kw)
        require_fraction("critical_fraction", self.critical_fraction)
        require_fraction("min_flexible_fraction", self.min_flexible_fraction)
        require_fraction("deferrable_fraction", self.deferrable_fraction)
        require_fraction("importance", self.importance)
        horizon = self.forecast_horizon
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            raise ValidationError(f"forecast_horizon must be an int >= 1, got {self.forecast_horizon!r}")
        require_non_negative("base_price_per_kwh", self.base_price_per_kwh)
        require_positive("max_price_per_kwh", self.max_price_per_kwh)
        require_le("base_price_per_kwh", self.base_price_per_kwh, "max_price_per_kwh", self.max_price_per_kwh)
        if isinstance(self.max_deferral_slots, bool) or not isinstance(self.max_deferral_slots, int) \
                or self.max_deferral_slots < 1:
            raise ValidationError("max_deferral_slots must be an int >= 1")
        require_fraction("scarcity_response", self.scarcity_response)
        if self.max_backlog_kw is not None:
            require_non_negative("max_backlog_kw", self.max_backlog_kw)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def flexible_fraction(self) -> float:
        return 1.0 - self.critical_fraction

    @property
    def backlog_limit_kw(self) -> float:
        """Maximum deferred demand the building will carry (defaults to capacity)."""
        return self.capacity_kw if self.max_backlog_kw is None else self.max_backlog_kw

    def with_overrides(self, **changes: Any) -> "BuildingSpec":
        """Return a validated copy with some fields replaced."""
        return replace(self, **changes)

    def to_dict(self) -> dict:
        return {
            "building_id": self.building_id,
            "name": self.name,
            "building_type": self.building_type.value,
            "capacity_kw": self.capacity_kw,
            "minimum_operational_kw": self.minimum_operational_kw,
            "critical_fraction": self.critical_fraction,
            "min_flexible_fraction": self.min_flexible_fraction,
            "deferrable_fraction": self.deferrable_fraction,
            "importance": self.importance,
            "forecast_horizon": self.forecast_horizon,
            "base_price_per_kwh": self.base_price_per_kwh,
            "max_price_per_kwh": self.max_price_per_kwh,
            "max_backlog_kw": self.max_backlog_kw,
            "max_deferral_slots": self.max_deferral_slots,
            "scarcity_response": self.scarcity_response,
            "metadata": dict(self.metadata),
        }
