# Forecasting

## Interface

Every forecaster implements `BaseForecaster.forecast(history, horizon, resolution_minutes=None) -> Forecast`.
A `Forecast` is an ordered tuple of `ForecastPoint(timestamp, predicted_demand_kw, confidence)`. The
Building Agent only depends on this interface. Models are selected by name in the campus config
(`gridweave.forecasting.registry`), and custom models can be added with `register_forecaster`.

```
BaseForecaster
 ├── MovingAverageForecaster(window)          baseline 1: mean of the last w samples, flat forecast
 ├── EWMAForecaster(alpha)  (= EWMAPredictor) baseline 2: exponential smoothing L = a·y + (1−a)·L
 ├── SeasonalNaiveForecaster(season=96)       same slot yesterday
 ├── SeasonalEWMAForecaster(season, a, b)     yesterday's shape + b·EWMA of (today − yesterday)
 └── FallbackForecaster(primary, fallback)    use primary once it has enough history
```

Shared behaviour, implemented once in the base class:

* `horizon` must be an int ≥ 1. Predictions are clipped at 0. Timestamps continue the history.
* **Insufficient history:** fewer than `min_history` samples raises `InsufficientHistoryError`.
  Between `min_history` and `ideal_history` the model still predicts, but its confidence is scaled
  down proportionally. For example, MA(8) with 2 samples averages those 2 samples at a quarter of
  the confidence.
* **Confidence** ∈ (0, 1]: `history_factor / (1 + CV_recent · √k)`, where `CV_recent` is the
  coefficient of variation of the last 8 samples and `k` is the number of steps ahead. It is a
  deterministic heuristic ("how much do I trust this?"), **not** a calibrated probability.

`SpikeDetector` flags observations more than 3.5 robust σ (MAD-based) from the recent median. The
agent counts and logs these anomalies.

## Metrics

`mae`, `rmse`, `bias`, and `mape`. **MAPE caveat:** percentage error is undefined or explosive when
actual demand is near zero (academic blocks at night). `mape` therefore skips points where
`actual < 1 kW` (`MAPE_EPSILON_KW`) and returns `None` if no point qualifies. MAE and RMSE are the
primary metrics.

## Experiment

Reproduce with `python scripts/run_forecast_experiment.py`. It writes
`data/generated/forecast_experiment.{json,md}`.

* **Dataset:** synthetic demand for the 5 default-campus buildings (Hostel A/B/C, Engineering Lab,
  Academic Block). 7 days (Mon 2026-01-05 to Sun 2026-01-11, so the weekend is included), 15-minute
  slots, seeds 42, 43 and 44.
* **Protocol:** rolling-origin backtest with stride 1 and a 192-sample (2-day) warm-up. That gives
  480 forecast origins per building and seed, identical for every method. The forecaster only ever
  sees `series[:t]`; `test_backtest_uses_no_future_data` checks there is no leakage.
* **Horizons:** 1 slot (15 min: the next market slot, which is what the agent bids on) and 4 slots
  (1 hour: what `demand_outlook` gives P3).
* **Metrics:** mean over buildings and seeds.

### Results (actual output of the script)

**Horizon 1 (15 min)** - mean over buildings and seeds

| Method | MAE (kW) | RMSE (kW) | MAPE (%) | Bias (kW) |
|---|---:|---:|---:|---:|
| EWMA(a=0.6) | 4.68 | 7.27 | 8.8 | -0.01 |
| SeasonalEWMA(96) | 4.94 | 7.90 | 10.7 | +0.41 |
| MA(4) | 6.44 | 10.07 | 12.3 | -0.01 |
| EWMA(a=0.3) | 7.31 | 11.11 | 14.7 | -0.00 |
| SeasonalNaive(96) | 8.23 | 16.00 | 19.5 | +1.98 |
| MA(8) | 9.78 | 14.98 | 19.7 | -0.01 |

**Horizon 4 (60 min)** - mean over buildings and seeds

| Method | MAE (kW) | RMSE (kW) | MAPE (%) | Bias (kW) |
|---|---:|---:|---:|---:|
| SeasonalEWMA(96) | 5.85 | 9.50 | 12.8 | +0.46 |
| EWMA(a=0.6) | 7.74 | 12.64 | 15.1 | -0.06 |
| SeasonalNaive(96) | 8.25 | 16.04 | 19.6 | +1.99 |
| MA(4) | 9.27 | 14.87 | 18.4 | -0.08 |
| EWMA(a=0.3) | 10.11 | 15.70 | 20.8 | -0.07 |
| MA(8) | 12.46 | 18.98 | 25.8 | -0.08 |

**MAE (kW) per building, horizon 4**

| Building | MA(4) | MA(8) | EWMA(a=0.3) | EWMA(a=0.6) | SeasonalNaive(96) | SeasonalEWMA(96) |
|---|---:|---:|---:|---:|---:|---:|
| hostel_a | 9.44 | 12.80 | 10.34 | 7.86 | 6.42 | 5.50 |
| hostel_b | 7.74 | 10.63 | 8.52 | 6.43 | 5.35 | 4.63 |
| hostel_c | 11.59 | 15.53 | 12.64 | 9.80 | 9.38 | 8.03 |
| eng_lab | 8.43 | 10.70 | 8.89 | 7.16 | 9.13 | 5.96 |
| academic_block | 9.16 | 12.62 | 10.15 | 7.46 | 11.00 | 5.12 |

### Interpretation

* **Next slot (h = 1):** EWMA(α = 0.6) is best (MAE 4.68 kW), narrowly ahead of SeasonalEWMA (4.94).
  At 15 minutes the most recent reading is the strongest signal, so a fast-reacting smoother wins.
* **One hour ahead (h = 4):** SeasonalEWMA is clearly best (MAE 5.85 vs 7.74 kW, RMSE 9.50 vs
  12.64). Flat forecasts (MA, EWMA) cannot anticipate the evening ramp; the seasonal shape can.
* **Seasonal naive alone** is poor on its own (high RMSE, +2 kW bias). Copying yesterday fails on
  noisy days and at the Friday-to-Saturday transition. It is weakest for the lab and academic block,
  whose weekend demand collapses. Adding the EWMA level correction fixes most of this.
* **Longer windows hurt:** MA(8) and EWMA(0.3) lag the ramps and are worst at both horizons.

**Default choice** (`configs/campus_default.json`): `seasonal_ewma` with an `ewma(α=0.6)` fallback
for the first day, before 97 samples exist. It is within about 0.26 kW of the best method at h = 1
and much better for the multi-step outlook that P3 plans batteries with. Switching to pure EWMA for
next-slot bidding is a one-line config change.

### Limitations

* The data is synthetic and generated by the same family of profiles the seasonal models assume.
  Real meter data will have holidays, exams, weather effects and metering gaps, and all errors should
  be expected to be higher.
* Only daily seasonality (96 slots) is modelled. A weekly season (672 slots) would fix the
  weekday/weekend transition error but needs at least a week of history.
* Confidence is heuristic and not calibrated.
* No exogenous inputs such as temperature, timetable or occupancy. `Observation.metadata` is the
  extension point for them.
