"""Solar forecasting algorithms, protocols, and evaluation suite."""
from gridweave.supply.forecasting.base import (
    SolarForecast,
    SolarForecaster,
    SolarForecastPoint,
)
from gridweave.supply.forecasting.evaluation import (
    calculate_mae,
    calculate_rmse,
    rolling_origin_evaluation,
)
from gridweave.supply.forecasting.persistence import (
    PersistenceMode,
    PersistenceSolarForecaster,
)
from gridweave.supply.forecasting.weather_aware import (
    TimeOfDaySolarForecaster,
    WeatherAwareSolarForecaster,
)

__all__ = [
    "PersistenceMode",
    "PersistenceSolarForecaster",
    "SolarForecast",
    "SolarForecastPoint",
    "SolarForecaster",
    "TimeOfDaySolarForecaster",
    "WeatherAwareSolarForecaster",
    "calculate_mae",
    "calculate_rmse",
    "rolling_origin_evaluation",
]
