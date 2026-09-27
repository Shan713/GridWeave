"""Time-varying demand quantities: samples, observations, load split, state."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from gridweave.utils.validation import (
    ValidationError,
    require_close,
    require_le,
    require_non_negative,
)


@dataclass(frozen=True, order=True)
class DemandSample:
    """One point of a demand time series: average kW over the interval
    starting at ``timestamp``."""

    timestamp: datetime
    demand_kw: float

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise ValidationError("DemandSample.timestamp must be a datetime")
        object.__setattr__(self, "demand_kw", require_non_negative("demand_kw", self.demand_kw))


@dataclass(frozen=True)
class Observation:
    """What a Building Agent perceives from the environment at one step.

    The environment (simulator, or real meters later) produces observations;
    the agent never generates its own. ``metadata`` is an open extension point
    (e.g. temperature, occupancy) that the core logic ignores.
    """

    building_id: str
    timestamp: datetime
    measured_demand_kw: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise ValidationError("Observation.timestamp must be a datetime")
        object.__setattr__(
            self, "measured_demand_kw", require_non_negative("measured_demand_kw", self.measured_demand_kw)
        )
        object.__setattr__(self, "metadata", dict(self.metadata))

    def as_sample(self) -> DemandSample:
        return DemandSample(self.timestamp, self.measured_demand_kw)


@dataclass(frozen=True)
class LoadClassification:
    """Split of a desired demand into critical / flexible / minimum parts.

    Invariants: ``critical + flexible == total`` and
    ``0 <= critical <= minimum <= total``.
    """

    total_kw: float
    critical_kw: float
    flexible_kw: float
    minimum_kw: float

    def __post_init__(self) -> None:
        for name in ("total_kw", "critical_kw", "flexible_kw", "minimum_kw"):
            object.__setattr__(self, name, require_non_negative(name, getattr(self, name)))
        require_close("critical_kw + flexible_kw", self.critical_kw + self.flexible_kw, "total_kw", self.total_kw)
        require_le("critical_kw", self.critical_kw, "minimum_kw", self.minimum_kw)
        require_le("minimum_kw", self.minimum_kw, "total_kw", self.total_kw)

    @property
    def flexibility_ratio(self) -> float:
        """Share of the total that can be reduced: ``(total - minimum) / total``."""
        return 0.0 if self.total_kw <= 0 else (self.total_kw - self.minimum_kw) / self.total_kw


@dataclass(frozen=True)
class DemandState:
    """A building's demand picture for the slot it is about to bid on.

    * ``current_demand_kw`` — most recent measured demand.
    * ``predicted_demand_kw`` — forecast for the target slot.
    * ``backlog_kw`` — flexible demand deferred from earlier slots.
    * ``desired_demand_kw`` — ``min(capacity, predicted + backlog)``.
    * ``critical/flexible/minimum`` — the classification of ``desired``.
    * ``maximum_demand_kw`` — physical connection capacity.

    Invariants: ``critical + flexible == desired`` and
    ``critical <= minimum <= desired <= maximum``.
    """

    timestamp: datetime
    current_demand_kw: float
    predicted_demand_kw: float
    backlog_kw: float
    desired_demand_kw: float
    critical_demand_kw: float
    flexible_demand_kw: float
    minimum_demand_kw: float
    maximum_demand_kw: float

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise ValidationError("DemandState.timestamp must be a datetime")
        for name in (
            "current_demand_kw",
            "predicted_demand_kw",
            "backlog_kw",
            "desired_demand_kw",
            "critical_demand_kw",
            "flexible_demand_kw",
            "minimum_demand_kw",
            "maximum_demand_kw",
        ):
            object.__setattr__(self, name, require_non_negative(name, getattr(self, name)))
        require_close(
            "critical + flexible",
            self.critical_demand_kw + self.flexible_demand_kw,
            "desired_demand_kw",
            self.desired_demand_kw,
        )
        require_le("critical_demand_kw", self.critical_demand_kw, "minimum_demand_kw", self.minimum_demand_kw)
        require_le("minimum_demand_kw", self.minimum_demand_kw, "desired_demand_kw", self.desired_demand_kw)
        require_le("desired_demand_kw", self.desired_demand_kw, "maximum_demand_kw", self.maximum_demand_kw)

    @property
    def classification(self) -> LoadClassification:
        return LoadClassification(
            self.desired_demand_kw, self.critical_demand_kw, self.flexible_demand_kw, self.minimum_demand_kw
        )

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "current_demand_kw": self.current_demand_kw,
            "predicted_demand_kw": self.predicted_demand_kw,
            "backlog_kw": self.backlog_kw,
            "desired_demand_kw": self.desired_demand_kw,
            "critical_demand_kw": self.critical_demand_kw,
            "flexible_demand_kw": self.flexible_demand_kw,
            "minimum_demand_kw": self.minimum_demand_kw,
            "maximum_demand_kw": self.maximum_demand_kw,
        }
