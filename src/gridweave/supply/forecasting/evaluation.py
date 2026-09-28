"""Evaluation metrics and rolling-origin backtesting for solar forecasters."""
from __future__ import annotations

import math
from typing import Any, Sequence

from gridweave.models.common import TimeSlot
from gridweave.supply.forecasting.base import SolarForecaster


def calculate_mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Mean Absolute Error (kW)."""
    if len(actual) != len(predicted) or not actual:
        raise ValueError("actual and predicted sequences must be non-empty and equal length")
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def calculate_rmse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Root Mean Square Error (kW)."""
    if len(actual) != len(predicted) or not actual:
        raise ValueError("actual and predicted sequences must be non-empty and equal length")
    mse = sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual)
    return math.sqrt(mse)


def rolling_origin_evaluation(
    forecaster: SolarForecaster,
    series: Sequence[tuple[TimeSlot, float]],
    horizon: int = 4,
    warmup_slots: int = 48,
) -> dict[str, Any]:
    """Execute rolling-origin (walk-forward) backtest evaluation.

    Parameters
    ----------
    forecaster : SolarForecaster
        The forecaster under evaluation.
    series : Sequence[tuple[TimeSlot, float]]
        Chronological list of (TimeSlot, actual_generation_kw).
    horizon : int
        Number of steps ahead to evaluate.
    warmup_slots : int
        Number of initial slots for forecaster burn-in before computing errors.

    Returns
    -------
    dict[str, Any]
        Dictionary of MAE, RMSE, and step-by-step errors.
    """
    forecaster.reset()
    total_slots = len(series)
    if total_slots <= warmup_slots + horizon:
        raise ValueError(f"Series length {total_slots} too short for warmup {warmup_slots} + horizon {horizon}")

    actual_by_slot = {slot: kw for slot, kw in series}
    [s for s, _ in series]

    # Warmup
    for i in range(warmup_slots):
        slot, kw = series[i]
        forecaster.observe(slot, kw)

    errors_per_step: dict[int, list[float]] = {h: [] for h in range(1, horizon + 1)}
    all_actuals: list[float] = []
    all_predicted: list[float] = []

    # Rolling walk-forward
    for i in range(warmup_slots, total_slots - horizon):
        current_slot, current_kw = series[i]
        # Generate multi-step forecast starting from current_slot
        fc = forecaster.forecast(current_slot, horizon=horizon)

        for step_idx, pt in enumerate(fc.points):
            h = step_idx + 1
            if pt.slot in actual_by_slot:
                act = actual_by_slot[pt.slot]
                pred = pt.predicted_kw
                errors_per_step[h].append(act - pred)
                all_actuals.append(act)
                all_predicted.append(pred)

        # Reveal current truth to forecaster for next step
        forecaster.observe(current_slot, current_kw)

    mae_total = calculate_mae(all_actuals, all_predicted)
    rmse_total = calculate_rmse(all_actuals, all_predicted)

    mae_by_step = {
        h: sum(abs(e) for e in errs) / len(errs) if errs else 0.0
        for h, errs in errors_per_step.items()
    }
    rmse_by_step = {
        h: math.sqrt(sum(e ** 2 for e in errs) / len(errs)) if errs else 0.0
        for h, errs in errors_per_step.items()
    }

    return {
        "mae_total": round(mae_total, 4),
        "rmse_total": round(rmse_total, 4),
        "mae_by_step": {h: round(v, 4) for h, v in mae_by_step.items()},
        "rmse_by_step": {h: round(v, 4) for h, v in rmse_by_step.items()},
        "samples_evaluated": len(all_actuals),
    }
