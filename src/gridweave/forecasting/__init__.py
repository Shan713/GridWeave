"""Demand forecasting: common interface, baselines, metrics and backtesting."""
from gridweave.forecasting.anomaly import SpikeDetector
from gridweave.forecasting.base_forecaster import (
    BaseForecaster,
    Forecast,
    ForecastPoint,
    InsufficientHistoryError,
)
from gridweave.forecasting.evaluation import BacktestResult, backtest, compare_forecasters
from gridweave.forecasting.ewma import EWMAForecaster, EWMAPredictor
from gridweave.forecasting.metrics import bias, mae, mape, rmse
from gridweave.forecasting.moving_average import MovingAverageForecaster
from gridweave.forecasting.registry import FORECASTERS, create_forecaster, register_forecaster
from gridweave.forecasting.seasonal import SeasonalEWMAForecaster, SeasonalNaiveForecaster

__all__ = [
    "BacktestResult",
    "BaseForecaster",
    "EWMAForecaster",
    "EWMAPredictor",
    "FORECASTERS",
    "Forecast",
    "ForecastPoint",
    "InsufficientHistoryError",
    "MovingAverageForecaster",
    "SeasonalEWMAForecaster",
    "SeasonalNaiveForecaster",
    "SpikeDetector",
    "backtest",
    "bias",
    "compare_forecasters",
    "create_forecaster",
    "mae",
    "mape",
    "register_forecaster",
    "rmse",
]
