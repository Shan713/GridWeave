"""Baseline 2: exponentially weighted moving average (flat multi-step forecast)."""
from __future__ import annotations

import math

from gridweave.forecasting.base_forecaster import BaseForecaster
from gridweave.utils.validation import ValidationError


class EWMAForecaster(BaseForecaster):
    """Simple exponential smoothing: ``L_t = a*y_t + (1-a)*L_{t-1}``, ``L_0 = y_0``.

    Forecast ``y_hat(t+k) = L_t``. Higher ``alpha`` reacts faster to change;
    lower ``alpha`` smooths noise more.
    """

    name = "ewma"

    def __init__(self, alpha: float = 0.5) -> None:
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0.0 < alpha <= 1.0:
            raise ValidationError("alpha must be in (0, 1]")
        self.alpha = float(alpha)

    @property
    def ideal_history(self) -> int:
        # samples after which the initial value's weight falls below ~5 %
        return max(1, math.ceil(math.log(0.05) / math.log(1 - self.alpha))) if self.alpha < 1 else 1

    def _predict(self, values: list[float], horizon: int) -> list[float]:
        level = values[0]
        for y in values[1:]:
            level = self.alpha * y + (1.0 - self.alpha) * level
        return [level] * horizon

    def __repr__(self) -> str:
        return f"EWMAForecaster(alpha={self.alpha})"


#: Alias matching the naming used in the project brief.
EWMAPredictor = EWMAForecaster
