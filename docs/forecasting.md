# Forecasting

All forecasters here are **statistical time-series baselines** (moving average, exponential
smoothing, seasonal repetition). None of them is trained, and none is machine learning.

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

Reproduce with `python scripts/run_forecast_experiment.py` (takes about 20 s). It writes
`data/generated/forecast_experiment.{json,md}`.

* **Dataset:** synthetic demand for the 5 default-campus buildings (Hostel A/B/C, Engineering Lab,
  Academic Block). 7 days (Mon 2026-01-05 to Sun 2026-01-11, so the weekend is included), 15-minute slots.
* **Seeds:**
  * **Development seeds 42–44.** These were used when the default forecaster and the EWMA fallback
    α = 0.6 were chosen.
  * **Held-out seeds 101–105.** These were never used for any modelling decision. **Quote the held-out table.**
* **Protocol:** rolling-origin backtest with stride 1 and a 192-sample (2-day) warm-up, identical for
  every method. The forecaster only ever sees `series[:t]`. This is verified by
  `test_backtest_uses_no_future_data`, and was checked adversarially during the audit: perturbing all
  future values leaves every forecast bit-identical.
* **Horizons:** 1 slot (15 min: the next market slot, which the agent bids on) and 4 slots (1 hour:
  what `demand_outlook` gives P3). There are 480 origins per series at h = 1 and 477 at h = 4.
* **Averaging:** metrics are computed per (building, seed) series, then averaged with equal weight.
  RMSE is therefore a mean of per-series RMSEs, not a pooled RMSE.
* **Oracle row:** the generator's own noise-free expected demand. It is not a forecaster. It shows how
  much of the signal is a fixed daily template, i.e. how favourable this synthetic data is to seasonal
  methods. It is horizon-independent, which is why the h=1 and h=4 rows are identical.

### Results (actual output of the script)

##### Held-out evaluation (quote these): seeds [101, 102, 103, 104, 105]

**Horizon 1 (15 min)**, 5 buildings x 5 seeds = 25 series, 480 origins per series

| Method | MAE (kW) | RMSE (kW) | MAPE (%) | Bias (kW) |
|---|---:|---:|---:|---:|
| EWMA(a=0.6) | 4.74 | 7.35 | 8.9 | -0.01 |
| SeasonalEWMA(96) | 5.04 | 7.86 | 10.9 | +0.36 |
| MA(4) | 6.50 | 10.11 | 12.4 | -0.02 |
| EWMA(a=0.3) | 7.34 | 11.11 | 14.8 | -0.02 |
| SeasonalNaive(96) | 8.16 | 15.77 | 19.4 | +1.77 |
| MA(8) | 9.79 | 14.89 | 19.8 | -0.03 |
| *Oracle (noise-free template)* | 3.01 | 4.66 | 5.1 | -0.64 |

**Horizon 4 (60 min)**, 5 buildings x 5 seeds = 25 series, 477 origins per series

| Method | MAE (kW) | RMSE (kW) | MAPE (%) | Bias (kW) |
|---|---:|---:|---:|---:|
| SeasonalEWMA(96) | 5.89 | 9.31 | 12.9 | +0.40 |
| EWMA(a=0.6) | 7.81 | 12.64 | 15.2 | -0.08 |
| SeasonalNaive(96) | 8.19 | 15.81 | 19.5 | +1.78 |
| MA(4) | 9.33 | 14.82 | 18.5 | -0.09 |
| EWMA(a=0.3) | 10.14 | 15.64 | 20.9 | -0.09 |
| MA(8) | 12.43 | 18.85 | 25.8 | -0.10 |
| *Oracle (noise-free template)* | 3.01 | 4.66 | 5.1 | -0.64 |

##### Development seeds (used for model selection): seeds [42, 43, 44]

**Horizon 1 (15 min)**, 5 buildings x 3 seeds = 15 series, 480 origins per series

| Method | MAE (kW) | RMSE (kW) | MAPE (%) | Bias (kW) |
|---|---:|---:|---:|---:|
| EWMA(a=0.6) | 4.68 | 7.27 | 8.8 | -0.01 |
| SeasonalEWMA(96) | 4.94 | 7.90 | 10.7 | +0.41 |
| MA(4) | 6.44 | 10.07 | 12.3 | -0.01 |
| EWMA(a=0.3) | 7.31 | 11.11 | 14.7 | -0.00 |
| SeasonalNaive(96) | 8.23 | 16.00 | 19.5 | +1.98 |
| MA(8) | 9.78 | 14.98 | 19.7 | -0.01 |
| *Oracle (noise-free template)* | 2.95 | 4.47 | 5.2 | -0.25 |

**Horizon 4 (60 min)**, 5 buildings x 3 seeds = 15 series, 477 origins per series

| Method | MAE (kW) | RMSE (kW) | MAPE (%) | Bias (kW) |
|---|---:|---:|---:|---:|
| SeasonalEWMA(96) | 5.85 | 9.50 | 12.8 | +0.46 |
| EWMA(a=0.6) | 7.74 | 12.64 | 15.1 | -0.06 |
| SeasonalNaive(96) | 8.25 | 16.04 | 19.6 | +1.99 |
| MA(4) | 9.27 | 14.87 | 18.4 | -0.08 |
| EWMA(a=0.3) | 10.11 | 15.70 | 20.8 | -0.07 |
| MA(8) | 12.46 | 18.98 | 25.8 | -0.08 |
| *Oracle (noise-free template)* | 2.95 | 4.47 | 5.2 | -0.25 |


### Interpretation (limited to this synthetic dataset)

* **Next slot (h = 1):** EWMA (α = 0.6) had the lowest error on the held-out seeds (MAE 4.74 kW),
  ahead of Seasonal EWMA (5.04). The AR(1) noise in the generator makes the latest reading the
  strongest short-term signal.
* **One hour ahead (h = 4):** Seasonal EWMA performed best on the held-out seeds (MAE 5.89 vs
  7.81 kW, RMSE 9.31 vs 12.64). Flat forecasts cannot anticipate the evening ramp.
* The held-out ranking matches the development ranking, so the choice made on development seeds
  transferred to unseen seeds of the **same generator**. That is all it shows.
* All methods remain well above the noise-free oracle (MAE about 3 kW), which is the floor set by the
  generator's noise.

**Correct wording for reports:** "Seasonal EWMA performed best one hour ahead, and EWMA (α = 0.6) one
slot ahead, on held-out seeds of our synthetic campus demand." Do **not** say "Seasonal EWMA is the best
forecasting model": the data was generated from a repeating daily template, which structurally
favours seasonal methods (see [demand_model.md](demand_model.md)).

**Default choice** (packaged `campus_default.json`): `seasonal_ewma`, with an `ewma(α=0.6)` fallback
for the first day, before 97 samples exist. The agent bids one slot ahead, where EWMA was slightly
better. Seasonal EWMA was chosen for its robustness across horizons and the multi-step outlook P3
needs. Switching is a one-line config change.

### Forecast errors now have consequences

Since settlements are judged against realised demand, forecast error changes outcomes. The mock
comparison in `examples/closed_loop_demo.py` (synthetic Tuesday, 5 buildings, mock grid + solar +
battery) contrasts the default forecaster with a deliberately poor one that predicts yesterday's
daily mean:

| | forecast MAE | critical-shortfall slots | unused allocation |
|---|---:|---:|---:|
| default (Seasonal EWMA) | 3.73 kW | 1 | 167 kWh |
| poor (flat daily mean) | 31.50 kW | 11 | 1,629 kWh |

### Limitations

* The data is synthetic, and generated by the same family of profiles the seasonal models assume.
  Real meter data will have holidays, exams, weather effects and metering gaps; expect higher errors.
* Only daily seasonality (96 slots). A weekly season would fix the weekday/weekend transition error
  but needs at least a week of history.
* The confidence value is a heuristic and is not used in bidding decisions.
* No exogenous inputs such as temperature, timetable or occupancy. `Observation.metadata` is the
  extension point for them.
* Irregular input (gaps, wrong resolution) is rejected with `MissingSlotError` rather than repaired.
