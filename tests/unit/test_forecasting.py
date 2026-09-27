"""Forecasters, metrics, backtesting and spike detection."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

import pytest

from gridweave.forecasting import (
    EWMAForecaster,
    EWMAPredictor,
    InsufficientHistoryError,
    MovingAverageForecaster,
    SeasonalEWMAForecaster,
    SeasonalNaiveForecaster,
    SpikeDetector,
    backtest,
    bias,
    compare_forecasters,
    create_forecaster,
    mae,
    mape,
    rmse,
)
from gridweave.models import DemandSample
from gridweave.simulation import DemandGenerator, get_profile
from gridweave.utils.validation import ValidationError

T0 = datetime(2026, 1, 5)
STEP = timedelta(minutes=15)


def series(values):
    return [DemandSample(T0 + i * STEP, v) for i, v in enumerate(values)]


ALL = [
    MovingAverageForecaster(4),
    EWMAForecaster(0.5),
    SeasonalNaiveForecaster(4),
    SeasonalEWMAForecaster(4),
]


# ----------------------------------------------------------- common contract
@pytest.mark.parametrize("f", ALL, ids=lambda f: f.name)
class TestContract:
    def test_constant_demand_is_forecast_exactly(self, f):
        fc = f.forecast(series([30.0] * 12), horizon=6)
        assert fc.values == pytest.approx([30.0] * 6)
        assert fc.horizon == 6 and fc.method == f.name

    def test_timestamps_continue_the_history(self, f):
        fc = f.forecast(series([10.0] * 12), horizon=3)
        assert [p.timestamp for p in fc.points] == [T0 + i * STEP for i in (12, 13, 14)]

    def test_zero_demand(self, f):
        fc = f.forecast(series([0.0] * 12), horizon=4)
        assert fc.values == [0.0] * 4
        assert all(0 <= p.confidence <= 1 for p in fc.points)

    def test_predictions_are_never_negative(self, f):
        fc = f.forecast(series([50, 40, 30, 20, 10, 5, 1, 0, 0, 0, 0, 0]), horizon=8)
        assert all(v >= 0 for v in fc.values)

    @pytest.mark.parametrize("horizon", [0, -1, 1.5, True])
    def test_invalid_horizon(self, f, horizon):
        with pytest.raises(ValidationError):
            f.forecast(series([1.0] * 12), horizon)

    def test_empty_history_raises(self, f):
        with pytest.raises(InsufficientHistoryError):
            f.forecast([], 1)

    def test_noisy_demand_forecast_stays_near_level(self, f):
        rng = random.Random(0)
        fc = f.forecast(series([50 + rng.gauss(0, 3) for _ in range(40)]), 4)
        assert all(40 < v < 60 for v in fc.values)

    def test_confidence_decreases_with_steps_ahead_on_noisy_data(self, f):
        rng = random.Random(1)
        conf = [p.confidence for p in f.forecast(series([50 + rng.gauss(0, 8) for _ in range(40)]), 6).points]
        assert conf == sorted(conf, reverse=True) and conf[0] > conf[-1]


# ----------------------------------------------------------- method specifics
def test_moving_average_uses_last_window():
    assert MovingAverageForecaster(3).forecast(series([100, 1, 2, 3]), 1).values == [2.0]


def test_moving_average_with_insufficient_history_uses_available_and_lowers_confidence():
    f = MovingAverageForecaster(8)
    short = f.forecast(series([10.0, 20.0]), 1)
    full = f.forecast(series([15.0] * 8), 1)
    assert short.values == [15.0]
    assert short.first.confidence < full.first.confidence


def test_ewma_hand_computed():
    # L0=10, L1=.5*20+.5*10=15, L2=.5*40+.5*15=27.5
    assert EWMAForecaster(0.5).forecast(series([10, 20, 40]), 2).values == [27.5, 27.5]
    assert EWMAPredictor is EWMAForecaster


def test_ewma_tracks_changing_demand_faster_than_long_moving_average():
    history = series([20.0] * 20 + [60.0] * 4)  # step change
    ewma = EWMAForecaster(0.6).forecast(history, 1).values[0]
    assert ewma > MovingAverageForecaster(16).forecast(history, 1).values[0]


def test_seasonal_naive_repeats_last_season():
    f = SeasonalNaiveForecaster(season_length=4)
    assert f.forecast(series([1, 2, 3, 4, 5, 6, 7, 8]), 6).values == [5, 6, 7, 8, 5, 6]
    with pytest.raises(InsufficientHistoryError):
        f.forecast(series([1, 2, 3]), 1)


def test_seasonal_ewma_adds_level_correction():
    # every value today is +10 vs yesterday -> forecast should be shifted upwards
    f = SeasonalEWMAForecaster(season_length=4, alpha=1.0, beta=1.0)
    assert f.forecast(series([1, 2, 3, 4, 11, 12, 13, 14]), 4).values == [21, 22, 23, 24]


@pytest.mark.parametrize("bad", [lambda: MovingAverageForecaster(0), lambda: EWMAForecaster(0),
                                 lambda: EWMAForecaster(1.5), lambda: SeasonalNaiveForecaster(0),
                                 lambda: SeasonalEWMAForecaster(4, beta=2)])
def test_invalid_parameters(bad):
    with pytest.raises(ValidationError):
        bad()


def test_registry():
    assert isinstance(create_forecaster("ewma", alpha=0.2), EWMAForecaster)
    with pytest.raises(ValidationError):
        create_forecaster("prophet")


# ------------------------------------------------------------------- metrics
def test_metrics_hand_computed():
    a, p = [10.0, 20.0, 30.0], [12.0, 18.0, 33.0]
    assert mae(a, p) == pytest.approx(7 / 3)
    assert rmse(a, p) == pytest.approx(math.sqrt(17 / 3))
    assert mape(a, p) == pytest.approx(100 * (0.2 + 0.1 + 0.1) / 3)
    assert bias(a, p) == pytest.approx(1.0)


def test_mape_ignores_near_zero_actuals_and_returns_none_if_all_zero():
    assert mape([0.0, 10.0], [5.0, 11.0]) == pytest.approx(10.0)
    assert mape([0.0, 0.0], [1.0, 2.0]) is None


def test_metrics_validate_input():
    with pytest.raises(ValidationError):
        mae([1.0], [1.0, 2.0])
    with pytest.raises(ValidationError):
        rmse([], [])


def test_rmse_never_below_mae():
    rng = random.Random(3)
    a = [rng.uniform(0, 100) for _ in range(50)]
    p = [rng.uniform(0, 100) for _ in range(50)]
    assert rmse(a, p) >= mae(a, p)


# --------------------------------------------------------------- backtesting
def test_backtest_on_perfectly_periodic_series_seasonal_is_exact():
    s = series([10, 20, 30, 40] * 10)
    res = backtest(SeasonalNaiveForecaster(4), s, horizon=4, warmup=4)
    assert res.mae == 0 and res.rmse == 0 and res.origins == 33


def test_backtest_uses_no_future_data():
    class Spy(MovingAverageForecaster):
        seen = []

        def _predict(self, values, horizon):
            Spy.seen.append(len(values))
            return super()._predict(values, horizon)

    backtest(Spy(2), series(list(range(10))), horizon=2, warmup=3)
    assert Spy.seen == [3, 4, 5, 6, 7, 8]


def test_backtest_rejects_warmup_below_min_history():
    with pytest.raises(ValidationError):
        backtest(SeasonalNaiveForecaster(96), series([1.0] * 200), 4, warmup=10)


def test_seasonal_beats_flat_baselines_on_realistic_multi_step_hostel_data():
    s = DemandGenerator(get_profile("hostel"), 120, seed=11).generate(T0, 96 * 5)
    results = {r.method: r for r in compare_forecasters(
        [MovingAverageForecaster(4), EWMAForecaster(0.5), SeasonalNaiveForecaster(96)],
        {"hostel": s}, horizon=8, warmup=96, stride=4)}
    assert results["seasonal_naive"].mae < results["moving_average"].mae
    assert results["seasonal_naive"].mae < results["ewma"].mae


# ------------------------------------------------------------------ anomaly
def test_spike_detector():
    d = SpikeDetector(window=8, z_threshold=3.5)
    history = [50, 51, 49, 50, 52, 48, 50, 51]
    assert d.is_anomaly(history, 90)
    assert not d.is_anomaly(history, 52)
    assert not d.is_anomaly([50, 50], 500)  # too little history to judge
    assert not d.is_anomaly([0.0] * 8, 0.5)  # flat history + tiny wobble


def test_fallback_forecaster_switches_when_history_suffices():
    from gridweave.forecasting import FallbackForecaster

    f = FallbackForecaster(SeasonalNaiveForecaster(4), EWMAForecaster(1.0))
    assert f.forecast(series([1, 2, 3]), 1).method == "ewma"
    fc = f.forecast(series([1, 2, 3, 4, 5]), 1)
    assert fc.method == "seasonal_naive" and fc.values == [2]
    assert f.min_history == 1
