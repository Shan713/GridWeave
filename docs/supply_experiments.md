# Supply Experiments

Run the reproducible P3 experiments from the repository root:

```bash
python scripts/run_p3_experiments.py
python scripts/benchmark_p3_scaling.py
```

The first command evaluates persistence, seasonal persistence, time-of-day, and weather-aware solar forecasts with rolling-origin MAE/RMSE, then compares grid-only, grid+solar, rule-based battery, and forecast-aware battery scenarios. Results are written to `data/results/p3_experiment_results.json`. The scaling command writes `data/results/p3_scaling_results.json`.

All demand, solar, and tariff profiles are synthetic simulation assumptions with fixed seeds. Results must be regenerated after code or parameter changes; the committed JSON files are outputs, not claims about a real campus. The benchmark reports procurement cost, grid energy and peak, solar utilization/curtailment, battery throughput and degradation, SOC trajectory, shortages, and runtime where available.
