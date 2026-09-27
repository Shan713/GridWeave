"""Forecasting experiment: statistical baselines on synthetic campus demand.

Protocol (identical for every method, so results are comparable):
* data: every building of the campus config, 7 days at 15-min resolution
  (Mon..Sun, so the weekday->weekend transition is included);
* DEVELOPMENT seeds 42, 43, 44 - these were used when the default forecaster
  and the EWMA fallback alpha were chosen;
* HELD-OUT seeds 101..105 - never used for any modelling decision; the
  held-out table is the one to quote;
* rolling-origin backtest, stride 1, warm-up 2 days (192 samples), so the
  forecaster only ever sees series[:t];
* horizons 1 (15 min, the next market slot) and 4 (1 hour);
* metrics are computed per (building, seed) series, then averaged with equal
  weight per series (so RMSE is a mean of per-series RMSEs, not a pooled RMSE);
* "Oracle (noise-free template)" predicts the generator's own noise-free
  expected demand. It is not a forecaster; it shows how much of the signal is
  a fixed daily template, i.e. how favourable this synthetic data is to
  seasonal methods.

Usage:  python scripts/run_forecast_experiment.py [--config PATH]
Writes: data/generated/forecast_experiment.json and .md
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import fmean

from gridweave.config import load_campus_config
from gridweave.forecasting import (
    EWMAForecaster,
    MovingAverageForecaster,
    SeasonalEWMAForecaster,
    SeasonalNaiveForecaster,
    backtest,
    bias,
    mae,
    mape,
    rmse,
)
from gridweave.simulation import DemandGenerator, derive_seed

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "generated"

FORECASTERS = [
    ("MA(4)", MovingAverageForecaster(4)),
    ("MA(8)", MovingAverageForecaster(8)),
    ("EWMA(a=0.3)", EWMAForecaster(0.3)),
    ("EWMA(a=0.6)", EWMAForecaster(0.6)),
    ("SeasonalNaive(96)", SeasonalNaiveForecaster(96)),
    ("SeasonalEWMA(96)", SeasonalEWMAForecaster(96, alpha=0.3, beta=0.8)),
]
ORACLE = "Oracle (noise-free template)"
DEV_SEEDS, HELDOUT_SEEDS = [42, 43, 44], [101, 102, 103, 104, 105]
HORIZONS = (1, 4)
DAYS, WARMUP = 7, 192


def oracle_metrics(gen, series, horizon):
    actual, pred = [], []
    for t in range(WARMUP, len(series) - horizon + 1):
        for s in series[t:t + horizon]:
            actual.append(s.demand_kw)
            pred.append(gen.expected_demand(s.timestamp))
    return {"mae": mae(actual, pred), "rmse": rmse(actual, pred), "mape": mape(actual, pred),
            "bias": bias(actual, pred), "origins": len(series) - horizon + 1 - WARMUP}


def evaluate(base, seeds):
    rows = []
    for seed in seeds:
        cfg = replace(base, simulation=replace(base.simulation, days=DAYS, seed=seed))
        for b in cfg.buildings:
            gen = DemandGenerator(b.profile, b.spec.capacity_kw, derive_seed(seed, b.spec.building_id),
                                  cfg.simulation.resolution_minutes)
            series = gen.generate(cfg.simulation.start, cfg.simulation.periods)
            for h in HORIZONS:
                for label, f in FORECASTERS:
                    r = backtest(f, series, horizon=h, warmup=WARMUP, series_name=b.spec.building_id).to_dict()
                    rows.append({"seed": seed, "label": label, **r})
                rows.append({"seed": seed, "label": ORACLE, "series_name": b.spec.building_id, "horizon": h,
                             **oracle_metrics(gen, series, h)})
    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for m in ("mae", "rmse", "mape", "bias", "origins"):
            agg[(r["label"], r["horizon"])][m].append(r[m])
    summary = [{"method": label, "horizon": h, "series": len(v["mae"]), "origins_per_series": v["origins"][0],
                **{m: round(fmean(v[m]), 3) for m in ("mae", "rmse", "mape", "bias") if None not in v[m]}}
               for (label, h), v in agg.items()]
    summary.sort(key=lambda s: (s["horizon"], s["method"] == ORACLE, s["mae"]))
    return rows, summary


def render(title, seeds, summary, n_buildings):
    lines = [f"#### {title}: seeds {seeds}", ""]
    for h in HORIZONS:
        sub = [s for s in summary if s["horizon"] == h]
        lines += [f"**Horizon {h} ({h * 15} min)**, {n_buildings} buildings x {len(seeds)} seeds = {sub[0]['series']} "
                  f"series, {sub[0]['origins_per_series']} origins per series", "",
                  "| Method | MAE (kW) | RMSE (kW) | MAPE (%) | Bias (kW) |", "|---|---:|---:|---:|---:|"]
        for s in sub:
            name = f"*{s['method']}*" if s["method"] == ORACLE else s["method"]
            lines.append(f"| {name} | {s['mae']:.2f} | {s['rmse']:.2f} | {s.get('mape', float('nan')):.1f} "
                         f"| {s['bias']:+.2f} |")
        lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    base = load_campus_config(args.config)
    n = len(base.buildings)
    dev_rows, dev = evaluate(base, DEV_SEEDS)
    held_rows, held = evaluate(base, HELDOUT_SEEDS)
    md = "\n".join(render("Held-out evaluation (quote these)", HELDOUT_SEEDS, held, n)
                   + render("Development seeds (used for model selection)", DEV_SEEDS, dev, n)) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "forecast_experiment.json").write_text(json.dumps(
        {"meta": {"days": DAYS, "warmup": WARMUP, "horizons": HORIZONS, "dev_seeds": DEV_SEEDS,
                  "heldout_seeds": HELDOUT_SEEDS, "buildings": [b.spec.building_id for b in base.buildings]},
         "heldout_summary": held, "dev_summary": dev, "raw": dev_rows + held_rows}, indent=2))
    (OUT / "forecast_experiment.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
