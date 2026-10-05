"""A small *learned* forecaster: seasonal linear autoregression fitted by ridge regression.

This is the one trained model in the forecasting module (classical machine
learning: least-squares regression on the building's own history). It exists
to answer "would a learned model beat the statistical baselines?" with
evidence rather than assumption; see ``docs/forecasting.md``.

Model for the next slot (S = 96 slots = one day)::

    y(t+1) = w0 + w1*y(t) + w2*y(t-1) + w3*y(t-2) + w4*y(t+1-S) + w5*(y(t+1-S) - y(t-S))

The weights are fitted by ridge regression on the most recent ``train_window``
samples. Multi-step forecasts are recursive: predictions are fed back in as
lags. The fit is cached and refreshed every ``refit_every`` new samples, so
the per-slot cost is small.
"""
from __future__ import annotations

from gridweave.forecasting.base_forecaster import BaseForecaster
from gridweave.utils.validation import ValidationError

_N_FEATURES = 6


def _features(values: list[float], t: int, season: int) -> list[float]:
    """Feature vector for predicting ``values[t + 1]`` (``values`` may include predictions)."""
    same_slot_yesterday = values[t + 1 - season]
    return [1.0, values[t], values[t - 1], values[t - 2], same_slot_yesterday,
            same_slot_yesterday - values[t - season]]


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve a small dense linear system by Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValidationError("singular normal equations")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            factor = m[r][col] / m[col][col]
            if factor:
                for c in range(col, n + 1):
                    m[r][c] -= factor * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


class LinearARForecaster(BaseForecaster):
    """Seasonal linear autoregression fitted by ridge regression (a trained model)."""

    name = "linear_ar"

    def __init__(self, season_length: int = 96, train_window: int = 672, ridge: float = 1.0,
                 refit_every: int = 16) -> None:
        for label, value in (("season_length", season_length), ("train_window", train_window),
                             ("refit_every", refit_every)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValidationError(f"{label} must be an int >= 1")
        if ridge < 0:
            raise ValidationError("ridge must be >= 0")
        self.season_length = season_length
        self.train_window = train_window
        self.ridge = float(ridge)
        self.refit_every = refit_every
        self._cache_key: tuple | None = None
        self._weights: list[float] | None = None

    @property
    def min_history(self) -> int:
        # one season for the seasonal lags, plus at least half a day of training rows
        return self.season_length + 48

    @property
    def ideal_history(self) -> int:
        return self.season_length + self.train_window

    def fit(self, values: list[float]) -> list[float]:
        """Fit the weights on ``values`` (ridge regression, intercept not penalised)."""
        s = self.season_length
        start = max(s, len(values) - 1 - self.train_window)
        xtx = [[0.0] * _N_FEATURES for _ in range(_N_FEATURES)]
        xty = [0.0] * _N_FEATURES
        for t in range(start, len(values) - 1):
            x = _features(values, t, s)
            y = values[t + 1]
            for i in range(_N_FEATURES):
                xty[i] += x[i] * y
                for j in range(i, _N_FEATURES):
                    xtx[i][j] += x[i] * x[j]
        for i in range(_N_FEATURES):
            for j in range(i):
                xtx[i][j] = xtx[j][i]
            if i > 0:
                xtx[i][i] += self.ridge
        return _solve(xtx, xty)

    def _weights_for(self, values: list[float]) -> list[float]:
        # Refit only when enough new data has arrived (the key identifies the series by its start)
        key = (values[0], len(values) // self.refit_every)
        if key != self._cache_key or self._weights is None:
            self._weights = self.fit(values)
            self._cache_key = key
        return self._weights

    def _predict(self, values: list[float], horizon: int) -> list[float]:
        w = self._weights_for(values)
        extended = list(values)
        preds = []
        for _ in range(horizon):
            x = _features(extended, len(extended) - 1, self.season_length)
            y = max(0.0, sum(wi * xi for wi, xi in zip(w, x)))
            preds.append(y)
            extended.append(y)
        return preds

    def __repr__(self) -> str:
        return (f"LinearARForecaster(season_length={self.season_length}, train_window={self.train_window}, "
                f"ridge={self.ridge}, refit_every={self.refit_every})")
