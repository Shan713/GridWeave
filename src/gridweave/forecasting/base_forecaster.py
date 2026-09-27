"""Common forecasting interface.

The Building Agent depends only on :class:`BaseForecaster`; any model that
implements ``_predict`` (moving average, EWMA, seasonal, or a future ML model)
can be swapped in via configuration without touching agent code.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import fmean, pstdev
from typing import Sequence

from gridweave.models.demand import DemandSample
from gridweave.utils.validation import ValidationError, require_fraction, require_non_negative


class InsufficientHistoryError(ValueError):
    """Raised when a forecaster is given fewer samples than it needs."""


@dataclass(frozen=True)
class ForecastPoint:
    timestamp: datetime
    predicted_demand_kw: float
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "predicted_demand_kw", require_non_negative("predicted_demand_kw",
                                                                            self.predicted_demand_kw))
        require_fraction("confidence", self.confidence)


@dataclass(frozen=True)
class Forecast:
    """An ordered multi-step forecast produced by one method."""

    method: str
    points: tuple[ForecastPoint, ...]

    @property
    def horizon(self) -> int:
        return len(self.points)

    @property
    def values(self) -> list[float]:
        return [p.predicted_demand_kw for p in self.points]

    @property
    def first(self) -> ForecastPoint:
        return self.points[0]

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "points": [
                {"timestamp": p.timestamp.isoformat(), "predicted_demand_kw": p.predicted_demand_kw,
                 "confidence": p.confidence}
                for p in self.points
            ],
        }


class BaseForecaster(ABC):
    """Template for all forecasters.

    Subclasses implement :meth:`_predict` on a plain list of kW values; this
    base class handles validation, timestamps, clipping and confidence.

    ``min_history`` is the hard minimum (fewer samples raise
    :class:`InsufficientHistoryError`). ``ideal_history`` is the amount of
    history the method is designed for; with less, it still predicts but its
    confidence is scaled down proportionally.
    """

    name: str = "base"

    @property
    def min_history(self) -> int:
        return 1

    @property
    def ideal_history(self) -> int:
        return self.min_history

    def forecast(
        self,
        history: Sequence[DemandSample],
        horizon: int,
        resolution_minutes: int | None = None,
    ) -> Forecast:
        """Predict the next ``horizon`` slots after the last history sample."""
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            raise ValidationError(f"horizon must be an int >= 1, got {horizon!r}")
        if len(history) < self.min_history:
            raise InsufficientHistoryError(
                f"{self.name} needs >= {self.min_history} samples, got {len(history)}"
            )
        step = self._step(history, resolution_minutes)
        values = [s.demand_kw for s in history]
        preds = [max(0.0, float(v)) for v in self._predict(values, horizon)]
        if len(preds) != horizon:
            raise RuntimeError(f"{self.name} returned {len(preds)} predictions for horizon {horizon}")
        last = history[-1].timestamp
        points = tuple(
            ForecastPoint(last + (k + 1) * step, preds[k], self._confidence(values, k + 1))
            for k in range(horizon)
        )
        return Forecast(self.name, points)

    @abstractmethod
    def _predict(self, values: list[float], horizon: int) -> list[float]:
        """Return exactly ``horizon`` predictions from the raw history values."""

    # ------------------------------------------------------------ internals
    def _confidence(self, values: list[float], steps_ahead: int) -> float:
        """Heuristic confidence in (0, 1]: lower for volatile recent demand,
        short history and far horizons. Deterministic and explainable; it is
        *not* a calibrated probability."""
        recent = values[-8:]
        mu = fmean(recent)
        sigma = pstdev(recent) if len(recent) > 1 else 0.0
        if mu < 1e-6:
            volatility = 0.0 if sigma < 1e-6 else 1.0
        else:
            volatility = sigma / mu
        history_factor = min(1.0, len(values) / max(1, self.ideal_history))
        return round(history_factor / (1.0 + volatility * math.sqrt(steps_ahead)), 6)

    @staticmethod
    def _step(history: Sequence[DemandSample], resolution_minutes: int | None) -> timedelta:
        if resolution_minutes is not None:
            if resolution_minutes <= 0:
                raise ValidationError("resolution_minutes must be > 0")
            return timedelta(minutes=resolution_minutes)
        if len(history) >= 2:
            step = history[-1].timestamp - history[-2].timestamp
            if step > timedelta(0):
                return step
        return timedelta(minutes=15)

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"
