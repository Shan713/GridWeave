"""Build forecasters by name so the choice of model is pure configuration."""
from __future__ import annotations

from typing import Any, Callable

from gridweave.forecasting.base_forecaster import BaseForecaster
from gridweave.forecasting.ewma import EWMAForecaster
from gridweave.forecasting.linear_ar import LinearARForecaster
from gridweave.forecasting.moving_average import MovingAverageForecaster
from gridweave.forecasting.seasonal import SeasonalEWMAForecaster, SeasonalNaiveForecaster
from gridweave.utils.validation import ValidationError

FORECASTERS: dict[str, Callable[..., BaseForecaster]] = {
    MovingAverageForecaster.name: MovingAverageForecaster,
    EWMAForecaster.name: EWMAForecaster,
    SeasonalNaiveForecaster.name: SeasonalNaiveForecaster,
    SeasonalEWMAForecaster.name: SeasonalEWMAForecaster,
    LinearARForecaster.name: LinearARForecaster,
}


def create_forecaster(method: str, **params: Any) -> BaseForecaster:
    try:
        factory = FORECASTERS[method]
    except KeyError as exc:
        raise ValidationError(f"unknown forecaster {method!r}; known: {sorted(FORECASTERS)}") from exc
    return factory(**params)


def register_forecaster(name: str, factory: Callable[..., BaseForecaster]) -> None:
    """Extension point: register a custom forecaster (e.g. an ML model)."""
    FORECASTERS[name] = factory
