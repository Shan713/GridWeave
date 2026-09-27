"""Rolling-origin backtesting of forecasters.

For each origin ``t`` (from ``warmup`` onwards, every ``stride`` samples) the
forecaster sees only ``series[:t]`` and predicts ``series[t:t+horizon]``.
No future information ever leaks into a forecast.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from gridweave.forecasting.base_forecaster import BaseForecaster
from gridweave.forecasting.metrics import bias, mae, mape, rmse
from gridweave.models.demand import DemandSample
from gridweave.utils.validation import ValidationError


@dataclass(frozen=True)
class BacktestResult:
    method: str
    series_name: str
    horizon: int
    origins: int
    mae: float
    rmse: float
    mape: float | None
    bias: float
    mae_by_step: tuple[float, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def backtest(
    forecaster: BaseForecaster,
    series: Sequence[DemandSample],
    horizon: int,
    warmup: int,
    stride: int = 1,
    series_name: str = "series",
) -> BacktestResult:
    if warmup < forecaster.min_history:
        raise ValidationError(f"warmup ({warmup}) < {forecaster.name}.min_history ({forecaster.min_history})")
    if stride < 1:
        raise ValidationError("stride must be >= 1")
    actual_all: list[float] = []
    pred_all: list[float] = []
    per_step: list[list[tuple[float, float]]] = [[] for _ in range(horizon)]
    origins = 0
    for t in range(warmup, len(series) - horizon + 1, stride):
        preds = forecaster.forecast(series[:t], horizon).values
        actual = [s.demand_kw for s in series[t:t + horizon]]
        actual_all.extend(actual)
        pred_all.extend(preds)
        for k in range(horizon):
            per_step[k].append((actual[k], preds[k]))
        origins += 1
    if origins == 0:
        raise ValidationError("series too short for the requested warmup + horizon")
    return BacktestResult(
        method=forecaster.name,
        series_name=series_name,
        horizon=horizon,
        origins=origins,
        mae=mae(actual_all, pred_all),
        rmse=rmse(actual_all, pred_all),
        mape=mape(actual_all, pred_all),
        bias=bias(actual_all, pred_all),
        mae_by_step=tuple(mae([a for a, _ in s], [p for _, p in s]) for s in per_step),
    )


def compare_forecasters(
    forecasters: Sequence[BaseForecaster],
    series_by_name: Mapping[str, Sequence[DemandSample]],
    horizon: int,
    warmup: int,
    stride: int = 1,
) -> list[BacktestResult]:
    """Backtest every forecaster on every series under identical conditions
    (same origins, horizon and data) so the numbers are directly comparable."""
    return [
        backtest(f, series, horizon, warmup, stride, name)
        for name, series in series_by_name.items()
        for f in forecasters
    ]
