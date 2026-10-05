"""Generate the report / Review-2 figures from real simulation runs -> docs/figures/*.png.

Needs the optional plotting dependency:  pip install -e ".[viz]"

Usage:  python scripts/make_figures.py            (about 1-2 minutes)

Every number in every figure comes from a fresh, seeded run (or from the
forecast experiment's JSON, regenerated if missing), and is also written to
docs/figures/results.json so the design doc can quote it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from statistics import fmean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from gridweave.coordinator import MODES, MetricsAggregator, get_scenario, run_simulation  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures"

# Validated categorical slots 1-3 (dataviz reference palette, light mode; all-pairs CVD safe).
MODE_COLORS = {"equal_share": "#2a78d6", "critical_first": "#eb6834", "gridweave": "#1baf7a"}
NEUTRAL = "#8a8985"
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
SEEDS = (42, 43, 44)

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 12, "axes.titleweight": "bold", "legend.frameon": False,
})


def metrics_for(scenario, mode, **kw):
    out = run_simulation(scenario, mode, **kw)
    return MetricsAggregator.compute(out.result, out.agents), out


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote docs/figures/{name}")


# 1 -----------------------------------------------------------------------------------------
def fig_modes(results):
    scenarios = ["grid_outage", "mixed_stress", "scarcity"]
    data = {s: {m: metrics_for(s, m)[0] for m in MODES} for s in scenarios}
    results["modes"] = {s: {m: {"critical_shortfall_kwh": round(v.total_critical_shortfall_kwh, 1),
                               "critical_service_ratio": round(v.critical_service_ratio, 4),
                               "service_ratio": round(v.overall_service_ratio, 4),
                               "expired_kwh": round(v.total_expired_kwh, 1),
                               "fairness": round(v.fairness.jains_index_final, 4),
                               "cost": round(v.total_procurement_cost)}
                           for m, v in d.items()} for s, d in data.items()}
    fig, ax = plt.subplots(figsize=(9, 4.2))
    width = 0.26
    for i, (mode, spec) in enumerate(MODES.items()):
        xs = [k + (i - 1) * (width + 0.02) for k in range(len(scenarios))]
        ys = [data[s][mode].total_critical_shortfall_kwh for s in scenarios]
        bars = ax.bar(xs, ys, width, color=MODE_COLORS[mode], label=spec.label, zorder=3)
        ax.bar_label(bars, labels=[f"{y:.0f}" for y in ys], padding=2, fontsize=9, color=INK)
    ax.set_xticks(range(len(scenarios)), [s.replace("_", " ") for s in scenarios])
    top = max(v.total_critical_shortfall_kwh for d in data.values() for v in d.values())
    ax.set_ylim(0, top * 1.3)  # headroom so the legend never covers a bar label
    ax.set_ylabel("Critical load not served (kWh)")
    ax.set_title("Critical-load shortfall by decision mode (lower is better)", loc="left")
    ax.legend(loc="upper left", ncols=3)
    ax.grid(axis="x", visible=False)
    save(fig, "modes_critical_shortfall.png")


# 2 -----------------------------------------------------------------------------------------
def fig_supply_sweep(results):
    base = get_scenario("normal")
    levels = [250, 200, 150, 100, 50]
    rows = {m: [] for m in MODES}
    service = {m: [] for m in MODES}
    for grid_kw in levels:
        supply = json.loads(json.dumps(base.supply_config))
        supply["sources"][0]["nominal_capacity_kw"] = float(grid_kw)
        sc = replace(base, name=f"grid_{grid_kw}", days=1, supply_config=supply)
        for mode in MODES:
            ms = [metrics_for(sc, mode, seed=s)[0] for s in SEEDS]
            rows[mode].append(fmean(m.total_critical_shortfall_kwh for m in ms))
            service[mode].append(fmean(m.overall_service_ratio for m in ms))
    results["supply_sweep"] = {"grid_kw": levels, "seeds": list(SEEDS),
                               "critical_shortfall_kwh": {m: [round(v, 1) for v in r] for m, r in rows.items()},
                               "service_ratio": {m: [round(v, 4) for v in r] for m, r in service.items()}}
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    offsets = {"equal_share": 0, "critical_first": 7, "gridweave": -7}  # keep close end labels apart
    for mode, spec in MODES.items():
        ax.plot(levels, rows[mode], color=MODE_COLORS[mode], linewidth=2, marker="o", markersize=6,
                label=spec.label, zorder=3)
        ax.annotate(f"{rows[mode][-1]:.0f}", (levels[-1], rows[mode][-1]), xytext=(8, offsets[mode]),
                    textcoords="offset points", va="center", fontsize=9, color=INK)
    ax.invert_xaxis()
    ax.set_xlabel("Grid connection capacity (kW) - smaller means scarcer supply")
    ax.set_ylabel("Critical load not served (kWh/day)")
    ax.set_title(f"As supply gets scarcer, protecting critical load matters more (mean of {len(SEEDS)} seeds)",
                 loc="left")
    ax.legend(loc="upper left")
    save(fig, "supply_sweep.png")


# 3 -----------------------------------------------------------------------------------------
def forecast_summary():
    path = ROOT / "data" / "generated" / "forecast_experiment.json"
    if not path.exists():  # regenerate (about 45 s)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "run_forecast_experiment.py")], check=True,
                       stdout=subprocess.DEVNULL)
    return json.loads(path.read_text())["heldout_summary"]


def fig_forecast(results):
    summary = forecast_summary()
    results["forecast_heldout"] = summary
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharex=True)
    for ax, h in zip(axes, (1, 4)):
        rows = sorted((s for s in summary if s["horizon"] == h and not s["method"].startswith("Oracle")),
                      key=lambda s: s["mae"], reverse=True)
        oracle = next(s["mae"] for s in summary if s["horizon"] == h and s["method"].startswith("Oracle"))
        colors = [MODE_COLORS["gridweave"] if "learned" in r["method"] else MODE_COLORS["equal_share"] for r in rows]
        bars = ax.barh([r["method"] for r in rows], [r["mae"] for r in rows], color=colors, zorder=3)
        ax.bar_label(bars, labels=[f"{r['mae']:.2f}" for r in rows], padding=3, fontsize=9, color=INK)
        ax.axvline(oracle, color=NEUTRAL, linestyle="--", linewidth=1.5, zorder=4)
        ax.text(oracle, len(rows) - 0.35, f" noise floor {oracle:.2f}", fontsize=8, color=INK_2, va="bottom")
        ax.set_title(f"{h * 15} min ahead" + (" (what buildings bid on)" if h == 1 else ""), loc="left")
        ax.set_xlabel("Mean absolute error (kW), held-out seeds")
        ax.grid(axis="y", visible=False)
    fig.suptitle("Forecasting: the learned model (green) vs statistical baselines", x=0.01, y=1.04,
                 ha="left", fontweight="bold")
    save(fig, "forecast_mae.png")


# 4 -----------------------------------------------------------------------------------------
def fig_battery(results):
    metrics, out = metrics_for("normal", "gridweave")
    s = metrics.series_dict()
    x = list(range(len(s["battery_soc"])))
    results["battery_normal"] = {"discharge_kwh": round(metrics.total_battery_discharge_kwh, 1),
                                 "recharged_from_grid_kwh": round(metrics.total_grid_to_battery_kwh, 1),
                                 "cost": round(metrics.total_procurement_cost)}
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(10, 6.4), sharex=True, height_ratios=(1, 1, 1.1))
    a1.plot(x, [v * 100 if v is not None else None for v in s["battery_soc"]], color=MODE_COLORS["equal_share"],
            linewidth=2)
    a1.set_ylabel("Charge (%)")
    a1.set_ylim(0, 100)
    a1.set_title("Battery over three days (normal scenario): refills at night, discharges when needed or when "
                 "the grid is dear", loc="left")
    a2.bar(x, s["battery_discharge_kw"], width=1.0, color=MODE_COLORS["critical_first"], zorder=3)
    a2.set_ylabel("Discharge (kW)")
    a3.step(x, s["grid_price"], where="post", color=NEUTRAL, linewidth=2, label="Grid price")
    a3.step(x, s["battery_offer_price"], where="post", color=MODE_COLORS["gridweave"], linewidth=2,
            label="Battery offer price")
    a3.set_ylabel("Price per kWh")
    a3.legend(loc="upper right", ncols=2)
    ticks = list(range(0, len(x), 24))
    a3.set_xticks(ticks, [out.result.slots[i].time_slot.start.strftime("%a %H:%M") for i in ticks], rotation=30,
                  ha="right")
    save(fig, "battery_behaviour.png")


# 5 -----------------------------------------------------------------------------------------
def fig_outage_timeline(results):
    runs = {m: metrics_for("grid_outage", m, steps=96) for m in MODES}
    first = runs["gridweave"][1].result.slots
    x = list(range(len(first)))
    supply = [r.supply_kw for r in first]
    need = [sum(s.actual_total_kw for s in r.settlements.values()) for r in first]
    crit_need = [sum(s.actual_critical_kw for s in r.settlements.values()) for r in first]
    fig, ax = plt.subplots(figsize=(10, 4.4))
    ax.axvspan(43.5, 63.5, color=GRID, zorder=0)
    ax.text(53.5, max(need) * 1.02, "grid outage", ha="center", fontsize=9, color=INK_2)
    ax.plot(x, need, color=INK, linewidth=1.5, label="Demand (actual need)")
    ax.plot(x, crit_need, color=INK, linewidth=1.5, linestyle=":", label="of which critical")
    ax.plot(x, supply, color=NEUTRAL, linewidth=1.5, linestyle="--", label="Available supply")
    for mode, (metrics, out) in runs.items():
        crit_served = [sum(s.critical_served_kw for s in r.settlements.values()) for r in out.result.slots]
        ax.plot(x, crit_served, color=MODE_COLORS[mode], linewidth=2, label=f"Critical served: {MODES[mode].label}")
    ticks = list(range(0, len(x), 8))
    ax.set_xticks(ticks, [first[i].time_slot.start.strftime("%H:%M") for i in ticks])
    ax.set_ylabel("kW")
    ax.set_title("Grid outage (day 1): how much critical load each mode keeps on", loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), fontsize=8, ncols=3)
    save(fig, "outage_timeline.png")


# 6 -----------------------------------------------------------------------------------------
def fig_scaling(results):
    sizes, ms_per_slot = [5, 10, 25, 50, 100], []
    for n in sizes:
        t0 = time.perf_counter()
        out = run_simulation("normal", "gridweave", n_buildings=n, steps=24)
        ms_per_slot.append((time.perf_counter() - t0) / len(out.result.slots) * 1000)
    results["scaling"] = {"buildings": sizes, "ms_per_15min_slot": [round(v, 1) for v in ms_per_slot]}
    fig, ax = plt.subplots(figsize=(7.5, 4))
    ax.plot(sizes, ms_per_slot, color=MODE_COLORS["equal_share"], linewidth=2, marker="o", markersize=7, zorder=3)
    for n, v in zip(sizes, ms_per_slot):
        ax.annotate(f"{v:.0f} ms", (n, v), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=9)
    ax.set_xlabel("Buildings")
    ax.set_ylabel("Time per 15-minute slot (ms)")
    ax.set_title("Full system (P1-P4) runtime grows roughly linearly (sequential, one process)", loc="left")
    save(fig, "scaling.png")


def main() -> None:
    results: dict = {}
    steps = [fig_modes, fig_supply_sweep, fig_forecast, fig_battery, fig_outage_timeline, fig_scaling]
    for step in steps:
        print(f"{step.__name__} ...")
        step(results)
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    print("  wrote docs/figures/results.json")


if __name__ == "__main__":
    main()
