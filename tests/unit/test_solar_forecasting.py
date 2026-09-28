"""Unit tests for solar forecasting algorithms and evaluation."""
from datetime import datetime

import pytest

from gridweave.models.common import TimeSlot
from gridweave.supply.forecasting import (
    PersistenceMode,
    PersistenceSolarForecaster,
    TimeOfDaySolarForecaster,
    WeatherAwareSolarForecaster,
    calculate_mae,
    calculate_rmse,
    rolling_origin_evaluation,
)
from gridweave.supply.profiles.solar import SolarProfile


@pytest.fixture
def solar_profile():
    return SolarProfile(installed_capacity_kw=200.0, seed=42)


@pytest.fixture
def noon_slot():
    return TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)


def test_persistence_forecaster_naive(solar_profile, noon_slot):
    forecaster = PersistenceSolarForecaster(solar_profile=solar_profile, mode=PersistenceMode.NAIVE_PERSISTENCE)
    forecaster.observe(noon_slot, 120.0)

    fc = forecaster.forecast(noon_slot.next(), horizon=4)
    assert len(fc) == 4
    # All points must have valid predictions and confidences
    for pt in fc.points:
        assert pt.predicted_kw >= 0.0
        assert 0.0 <= pt.confidence <= 1.0


def test_weather_aware_forecaster(solar_profile, noon_slot):
    forecaster = WeatherAwareSolarForecaster(solar_profile=solar_profile)
    forecaster.observe(noon_slot, 130.0)

    fc = forecaster.forecast(noon_slot.next(), horizon=8)
    assert len(fc) == 8

    # Predictions must not exceed installed capacity
    for pt in fc.points:
        assert 0.0 <= pt.predicted_kw <= solar_profile.installed_capacity_kw
        assert pt.clear_sky_kw >= 0.0


def test_time_of_day_forecaster(solar_profile, noon_slot):
    forecaster = TimeOfDaySolarForecaster(solar_profile=solar_profile, alpha=0.5)
    # Feed several days of noon observation
    slot1 = noon_slot
    slot2 = TimeSlot(datetime(2026, 6, 16, 12, 0), duration_minutes=15)
    forecaster.observe(slot1, 100.0)
    forecaster.observe(slot2, 120.0)

    fc = forecaster.forecast(slot2, horizon=4)
    assert fc.points[0].predicted_kw == 110.0


def test_forecasting_night_invariant(solar_profile):
    night_slot = TimeSlot(datetime(2026, 6, 15, 1, 0), duration_minutes=15)
    forecaster = WeatherAwareSolarForecaster(solar_profile=solar_profile)

    fc = forecaster.forecast(night_slot, horizon=6)
    # At night, all predicted generation must be exactly 0.0
    for pt in fc.points:
        assert pt.predicted_kw == 0.0


def test_evaluation_metrics():
    actuals = [10.0, 20.0, 30.0, 40.0]
    preds = [12.0, 18.0, 33.0, 36.0]

    mae = calculate_mae(actuals, preds)
    rmse = calculate_rmse(actuals, preds)

    # MAE = (2 + 2 + 3 + 4) / 4 = 11 / 4 = 2.75
    assert mae == 2.75
    # MSE = (4 + 4 + 9 + 16) / 4 = 33 / 4 = 8.25; RMSE = sqrt(8.25) ~ 2.8723
    assert round(rmse, 4) == 2.8723


def test_rolling_origin_evaluation(solar_profile):
    forecaster = WeatherAwareSolarForecaster(solar_profile=solar_profile)
    start_dt = datetime(2026, 6, 15, 0, 0)
    series = []
    # Build 2-day (192 slots) synthetic series
    cur = TimeSlot(start_dt, duration_minutes=15)
    for _ in range(192):
        series.append((cur, solar_profile.generation_kw(cur)))
        cur = cur.next()

    results = rolling_origin_evaluation(forecaster, series, horizon=4, warmup_slots=48)
    assert "mae_total" in results
    assert "rmse_total" in results
    assert results["samples_evaluated"] > 0
    assert results["mae_total"] >= 0.0
