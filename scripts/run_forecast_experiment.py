"""Forecasting experiment: compare forecasters on synthetic campus demand.

Protocol (identical for every method, so results are comparable):
* data: every building of the campus config, 7 days at 15-min resolution
  (Mon..Sun, so the weekday->weekend transition is included), 3 seeds;
* rolling-origin backtest, stride 1, warm-up 2 days (192 samples) so even
  the seasonal models have a full day of history at the first origin;
* horizons 1 (15 min, the next market slot) and 4 (1 hour).

Usage:  python scripts/run_forecast_experiment.py [--config PATH] [--seeds 42 43 44]
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
from gridweave.factory import build_simulators
from gridweave.forecasting import (
    EWMAForecaster,
    MovingAverageForecaster,
    SeasonalEWMAForecaster,
    SeasonalNaiveForecaster,
    backtest,
)

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
HORIZONS = (1, 4)
DAYS, WARMUP = 7, 192


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    args = parser.parse_args()

    base = load_campus_config(args.config)
    rows = []
    for seed in args.seeds:
        cfg = replace(base, simulation=replace(base.simulation, days=DAYS, seed=seed))
        series = {bid: sim.series for bid, sim in build_simulators(cfg).items()}
        for h in HORIZONS:
            for building_id, s in series.items():
                for label, f in FORECASTERS:
                    res = backtest(f, s, horizon=h, warmup=WARMUP, series_name=building_id)
                    rows.append({"seed": seed, "label": label, **res.to_dict()})

    # aggregate: mean over buildings and seeds, per (method, horizon)
    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        key = (r["label"], r["horizon"])
        for m in ("mae", "rmse", "mape", "bias"):
            agg[key][m].append(r[m])
    summary = []
    for (label, h), metrics in agg.items():
        summary.append({"method": label, "horizon": h,
                        **{m: round(fmean(v), 3) for m, v in metrics.items() if None not in v}})
    summary.sort(key=lambda s: (s["horizon"], s["mae"]))

    per_building = defaultdict(dict)
    for r in rows:
        if r["horizon"] == 4:
            per_building[r["series_name"]].setdefault(r["label"], []).append(r["mae"])
    per_building = {b: {m: round(fmean(v), 3) for m, v in d.items()} for b, d in per_building.items()}

    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"days": DAYS, "warmup": WARMUP, "horizons": HORIZONS, "seeds": args.seeds,
            "buildings": list(per_building), "resolution_minutes": base.simulation.resolution_minutes,
            "origins_per_series": rows[0]["origins"] if rows else 0}
    (OUT / "forecast_experiment.json").write_text(json.dumps(
        {"meta": meta, "summary": summary, "per_building_mae_h4": per_building, "raw": rows}, indent=2))
    md = render_markdown(meta, summary, per_building)
    (OUT / "forecast_experiment.md").write_text(md)
    print(md)


def render_markdown(meta, summary, per_building) -> str:
    lines = [f"Data: {len(meta['buildings'])} buildings x {meta['days']} days x seeds {meta['seeds']}, "
             f"{meta['resolution_minutes']}-min slots, warm-up {meta['warmup']} samples.", ""]
    for h in meta["horizons"]:
        lines += [f"**Horizon {h} ({h * meta['resolution_minutes']} min)** - mean over buildings and seeds", "",
                  "| Method | MAE (kW) | RMSE (kW) | MAPE (%) | Bias (kW) |", "|---|---:|---:|---:|---:|"]
        for s in (s for s in summary if s["horizon"] == h):
            lines.append(f"| {s['method']} | {s['mae']:.2f} | {s['rmse']:.2f} | {s.get('mape', float('nan')):.1f} "
                         f"| {s['bias']:+.2f} |")
        lines.append("")
    methods = list(next(iter(per_building.values())))
    lines += ["**MAE (kW) per building, horizon 4**", "", "| Building | " + " | ".join(methods) + " |",
              "|---|" + "---:|" * len(methods)]
    for b, d in per_building.items():
        lines.append(f"| {b} | " + " | ".join(f"{d[m]:.2f}" for m in methods) + " |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
