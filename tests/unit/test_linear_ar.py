"""The learned forecaster (seasonal linear autoregression, ridge regression)."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

import pytest

from gridweave.forecasting import (
    EWMAForecaster,
    InsufficientHistoryError,
    LinearARForecaster,
    SeasonalEWMAForecaster,
    backtest,
    create_forecaster,
)
from gridweave.models import DemandSample
from gridweave.simulation import DemandGenerator, get_profile
from gridweave.utils.validation import ValidationError

T0 = datetime(2026, 1, 5)
STEP = timedelta(minutes=15)


def series(values):
    return [DemandSample(T0 + i * STEP, v) for i, v in enumerate(values)]


def daily(n, noise=0.0, seed=0):
    rng = random.Random(seed)
    return [50 + 30 * math.sin(2 * math.pi * i / 96) + rng.gauss(0, noise) for i in range(n)]


def test_registered_and_configurable():
    f = create_forecaster("linear_ar", train_window=200, ridge=0.5)
    assert isinstance(f, LinearARForecaster) and f.train_window == 200 and f.ridge == 0.5


@pytest.mark.parametrize("kwargs", [dict(season_length=0), dict(train_window=0), dict(refit_every=0),
                                    dict(ridge=-1.0), dict(season_length=True)])
def test_invalid_parameters(kwargs):
    with pytest.raises(ValidationError):
        LinearARForecaster(**kwargs)


def test_needs_a_day_and_a_half_of_history():
    f = LinearARForecaster()
    assert f.min_history == 144
    with pytest.raises(InsufficientHistoryError):
        f.forecast(series(daily(143)), 1)
    f.forecast(series(daily(144)), 1)


def test_learns_a_clean_daily_pattern_almost_exactly():
    history = series(daily(96 * 4))
    truth = daily(96 * 4 + 8)[-8:]
    preds = LinearARForecaster(ridge=1e-6).forecast(history, 8).values
    assert max(abs(p - t) for p, t in zip(preds, truth)) < 0.5


def test_constant_demand_is_forecast_exactly():
    assert LinearARForecaster().forecast(series([42.0] * 200), 4).values == pytest.approx([42.0] * 4, abs=0.05)


def test_zero_demand_and_never_negative():
    assert LinearARForecaster().forecast(series([0.0] * 200), 3).values == [0.0, 0.0, 0.0]
    falling = series([max(0.0, 100 - i) for i in range(200)])
    assert all(v >= 0 for v in LinearARForecaster().forecast(falling, 8).values)


def test_refits_only_every_n_new_samples():
    f = LinearARForecaster(refit_every=16)
    values = daily(220, noise=2.0)
    f.forecast(series(values[:192]), 1)
    first = f._weights
    f.forecast(series(values[:193]), 1)          # same refit window: cached weights reused
    assert f._weights is first
    f.forecast(series(values[:208]), 1)          # 16 more samples: refitted
    assert f._weights is not first


def test_uses_no_future_data():
    values = daily(300, noise=3.0, seed=1)
    poisoned = values[:250] + [9_999.0] * 50
    a = LinearARForecaster().forecast(series(values[:250]), 4).values
    b = LinearARForecaster().forecast(series(poisoned[:250]), 4).values
    assert a == b


def test_beats_the_statistical_baselines_on_synthetic_campus_demand():
    """Evidence for the default choice (held-out seed, not used for model selection)."""
    s = DemandGenerator(get_profile("hostel"), 120, seed=101).generate(T0, 96 * 5)
    learned = backtest(LinearARForecaster(), s, 1, 192, stride=2).mae
    assert learned < backtest(EWMAForecaster(0.6), s, 1, 192, stride=2).mae
    assert learned < backtest(SeasonalEWMAForecaster(96), s, 1, 192, stride=2).mae
