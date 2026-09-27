"""Forecast error metrics."""
from __future__ import annotations

import math
from typing import Sequence

from gridweave.utils.validation import ValidationError

#: Actual values below this (kW) are excluded from MAPE: percentage errors
#: explode, or are undefined, when the true demand is (near) zero.
MAPE_EPSILON_KW = 1.0


def _check(actual: Sequence[float], predicted: Sequence[float]) -> None:
    if len(actual) != len(predicted):
        raise ValidationError(f"length mismatch: {len(actual)} actual vs {len(predicted)} predicted")
    if not actual:
        raise ValidationError("cannot compute a metric on empty sequences")


def mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Mean absolute error (kW)."""
    _check(actual, predicted)
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def rmse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Root mean squared error (kW); penalises large misses more than MAE."""
    _check(actual, predicted)
    return math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))


def mape(actual: Sequence[float], predicted: Sequence[float], epsilon: float = MAPE_EPSILON_KW) -> float | None:
    """Mean absolute percentage error (%) over points where ``actual >= epsilon``.

    Returns ``None`` if no point qualifies (e.g. an all-zero series) rather
    than a misleading 0 % or infinity.
    """
    _check(actual, predicted)
    pairs = [(a, p) for a, p in zip(actual, predicted) if abs(a) >= epsilon]
    if not pairs:
        return None
    return 100.0 * sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs)


def bias(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Mean error ``predicted - actual`` (kW): positive = over-forecasting."""
    _check(actual, predicted)
    return sum(p - a for a, p in zip(actual, predicted)) / len(actual)
