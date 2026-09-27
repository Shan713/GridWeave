"""Run the Building Agents against the mock auction and mock grid.

Examples:
  python scripts/run_building_simulation.py                     # default 5-building campus, 3 days
  python scripts/run_building_simulation.py --steps 96          # first day only
  python scripts/run_building_simulation.py --buildings 100     # synthetic 100-building campus
  python scripts/run_building_simulation.py --supply-kw 300 --output data/generated/run.json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from gridweave.config import load_campus_config, synthetic_campus
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockCoordinator, MockGrid, summarise
from gridweave.utils.logging import configure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="campus config JSON (default: configs/campus_default.json)")
    parser.add_argument("--buildings", type=int, help="use a synthetic campus with N buildings instead")
    parser.add_argument("--steps", type=int, help="number of 15-min market cycles (default: whole horizon)")
    parser.add_argument("--supply-kw", type=float, help="mock grid capacity (default: from config, else 55%% of capacity)")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output", help="write per-step records + summary as JSON")
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()
    configure(args.log_level)

    cfg = synthetic_campus(args.buildings) if args.buildings else load_campus_config(args.config)
    if args.seed is not None:
        cfg = cfg.with_seed(args.seed)
    supply_kw = args.supply_kw or cfg.supply.get("mock_grid_capacity_kw") or 0.55 * cfg.total_capacity_kw
    windows = cfg.supply.get("shortage_windows", [])

    agents = build_agents(cfg)
    coordinator = MockCoordinator(agents, build_simulators(cfg), MockAuctioneer(), MockGrid(supply_kw, windows),
                                  resolution_minutes=cfg.simulation.resolution_minutes)
    t = time.perf_counter()
    records = coordinator.run(args.steps)
    elapsed = time.perf_counter() - t
    summary = summarise(records, agents)

    print(f"\n{cfg.name}: {len(agents)} buildings, {summary['steps']} cycles in {elapsed:.2f}s "
          f"(supply {supply_kw:.0f} kW, shortage windows {windows or 'none'})")
    print(f"requested {summary['requested_kwh']:.0f} kWh, served {summary['served_kwh']:.0f} kWh, "
          f"shortage cycles {summary['shortage_steps']}, re-negotiated {summary['renegotiated_steps']}, "
          f"critical shortfall events {summary['critical_shortfall_events']}\n")
    rows = list(summary["buildings"].items())
    if len(rows) > 12:
        rows = rows[:12]
        print(f"(showing first 12 of {len(summary['buildings'])} buildings)")
    print(f"{'building':<18}{'service':>9}{'deferred':>11}{'curtailed':>11}{'crit.evts':>11}{'backlog':>10}")
    for building_id, s in rows:
        print(f"{building_id:<18}{s['service_ratio']:>9.1%}{s['deferred_kwh']:>9.1f}kWh{s['curtailed_kwh']:>9.1f}kWh"
              f"{s['critical_shortfall_events']:>11}{s['final_backlog_kw']:>8.1f}kW")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"summary": summary, "records": [r.to_dict() for r in records]}, indent=1))
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
