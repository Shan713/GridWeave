"""Baseline 1: simple moving average (flat multi-step forecast)."""
from __future__ import annotations

from statistics import fmean

from gridweave.forecasting.base_forecaster import BaseForecaster
from gridweave.utils.validation import ValidationError


class MovingAverageForecaster(BaseForecaster):
    """``y_hat(t+k) = mean(y[t-w+1 .. t])`` for every ``k``.

    Robust to noise, but lags behind ramps and cannot anticipate daily peaks.
    With fewer than ``window`` samples it averages what is available.
    """

    name = "moving_average"

    def __init__(self, window: int = 4) -> None:
        if isinstance(window, bool) or not isinstance(window, int) or window < 1:
            raise ValidationError("window must be an int >= 1")
        self.window = window

    @property
    def ideal_history(self) -> int:
        return self.window

    def _predict(self, values: list[float], horizon: int) -> list[float]:
        level = fmean(values[-self.window:])
        return [level] * horizon

    def __repr__(self) -> str:
        return f"MovingAverageForecaster(window={self.window})"
