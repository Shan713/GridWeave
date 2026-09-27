"""Composite forecaster: use a data-hungry model once enough history exists."""
from __future__ import annotations

from typing import Sequence

from gridweave.forecasting.base_forecaster import BaseForecaster, Forecast
from gridweave.models.demand import DemandSample


class FallbackForecaster(BaseForecaster):
    """Delegates to ``primary`` when it has enough history, else ``fallback``.

    Typical use: a seasonal model needs a full day of data; during the first
    day of a simulation the agent falls back to EWMA instead of failing.
    The returned forecast's ``method`` names the model actually used.
    """

    def __init__(self, primary: BaseForecaster, fallback: BaseForecaster) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}|{fallback.name}"

    @property
    def min_history(self) -> int:
        return min(self.primary.min_history, self.fallback.min_history)

    def forecast(
        self, history: Sequence[DemandSample], horizon: int, resolution_minutes: int | None = None
    ) -> Forecast:
        model = self.primary if len(history) >= self.primary.min_history else self.fallback
        return model.forecast(history, horizon, resolution_minutes)

    def _predict(self, values: list[float], horizon: int) -> list[float]:  # pragma: no cover - forecast() overridden
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"FallbackForecaster({self.primary!r}, {self.fallback!r})"
