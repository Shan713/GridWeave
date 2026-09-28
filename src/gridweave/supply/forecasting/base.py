"""Base protocols and data types for solar generation forecasting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gridweave.models.common import TimeSlot
from gridweave.utils.validation import require_fraction, require_non_negative


@dataclass(frozen=True)
class SolarForecastPoint:
    """One forecasted future time slot's solar generation."""

    slot: TimeSlot
    predicted_kw: float
    confidence: float
    clear_sky_kw: float

    def __post_init__(self) -> None:
        require_non_negative("predicted_kw", self.predicted_kw)
        require_fraction("confidence", self.confidence)
        require_non_negative("clear_sky_kw", self.clear_sky_kw)

    def to_dict(self) -> dict:
        return {
            "slot": self.slot.to_dict(),
            "predicted_kw": self.predicted_kw,
            "confidence": self.confidence,
            "clear_sky_kw": self.clear_sky_kw,
        }


@dataclass(frozen=True)
class SolarForecast:
    """Multi-slot lookahead solar generation forecast."""

    generated_at: TimeSlot
    points: tuple[SolarForecastPoint, ...]

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.to_dict(),
            "points": [p.to_dict() for p in self.points],
        }

    def __iter__(self):
        return iter(self.points)

    def __len__(self) -> int:
        return len(self.points)

    def __getitem__(self, index: int) -> SolarForecastPoint:
        return self.points[index]


@runtime_checkable
class SolarForecaster(Protocol):
    """Protocol for interchangeable solar forecasting algorithms."""

    def observe(self, slot: TimeSlot, actual_kw: float, cloud_cover: float | None = None) -> None:
        """Feed the realized generation for slot t into the forecaster history."""
        ...

    def forecast(self, current_slot: TimeSlot, horizon: int = 8) -> SolarForecast:
        """Produce a multi-slot lookahead forecast starting from current_slot."""
        ...

    def reset(self) -> None:
        """Reset internal history and state."""
        ...
