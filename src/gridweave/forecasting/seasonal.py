"""Seasonal forecasters that exploit the strong daily cycle of campus demand."""
from __future__ import annotations

from gridweave.forecasting.base_forecaster import BaseForecaster
from gridweave.utils.validation import ValidationError, require_fraction


class SeasonalNaiveForecaster(BaseForecaster):
    """``y_hat(t+k) = y(t+k-S)``: same slot one season (default one day = 96
    slots) earlier. Requires at least one full season of history."""

    name = "seasonal_naive"

    def __init__(self, season_length: int = 96) -> None:
        if isinstance(season_length, bool) or not isinstance(season_length, int) or season_length < 1:
            raise ValidationError("season_length must be an int >= 1")
        self.season_length = season_length

    @property
    def min_history(self) -> int:
        return self.season_length

    def _predict(self, values: list[float], horizon: int) -> list[float]:
        n, s = len(values), self.season_length
        return [values[n - s + (k % s)] for k in range(horizon)]

    def __repr__(self) -> str:
        return f"SeasonalNaiveForecaster(season_length={self.season_length})"


class SeasonalEWMAForecaster(BaseForecaster):
    """Seasonal naive shape corrected by the recent level error.

    ``y_hat(t+k) = y(t+k-S) + beta * e_t`` where ``e_t`` is the EWMA (smoothing
    ``alpha``) of the recent differences ``y(t) - y(t-S)``. It follows the
    daily shape *and* adapts when today runs hotter/cooler than yesterday.
    Falls back to seasonal-naive behaviour when ``beta = 0``.
    """

    name = "seasonal_ewma"

    def __init__(self, season_length: int = 96, alpha: float = 0.3, beta: float = 0.8) -> None:
        if isinstance(season_length, bool) or not isinstance(season_length, int) or season_length < 1:
            raise ValidationError("season_length must be an int >= 1")
        if not 0.0 < alpha <= 1.0:
            raise ValidationError("alpha must be in (0, 1]")
        self.season_length = season_length
        self.alpha = float(alpha)
        self.beta = require_fraction("beta", beta)

    @property
    def min_history(self) -> int:
        return self.season_length + 1

    def _predict(self, values: list[float], horizon: int) -> list[float]:
        n, s = len(values), self.season_length
        err = 0.0
        start = max(s, n - 4 * s)  # recent differences only
        for i in range(start, n):
            err = self.alpha * (values[i] - values[i - s]) + (1 - self.alpha) * err
        return [values[n - s + (k % s)] + self.beta * err for k in range(horizon)]

    def __repr__(self) -> str:
        return f"SeasonalEWMAForecaster(season_length={self.season_length}, alpha={self.alpha}, beta={self.beta})"
