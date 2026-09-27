"""Run the Building Agents in a closed loop against the mock auction and mock supply (grid + solar + battery).

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
from gridweave.mocks import MockAuctioneer, MockCoordinator, MockGrid, MockSupply, summarise
from gridweave.utils.logging import configure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="campus config JSON (default: the packaged campus_default.json)")
    parser.add_argument("--buildings", type=int, help="use a synthetic campus with N buildings instead")
    parser.add_argument("--steps", type=int, help="number of 15-min market cycles (default: whole horizon)")
    parser.add_argument("--supply-kw", type=float,
                        help="use a single mock grid of this size instead of the config's supply sources")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output", help="write per-step records + summary as JSON")
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()
    configure(args.log_level)

    cfg = synthetic_campus(args.buildings) if args.buildings else load_campus_config(args.config)
    if args.seed is not None:
        cfg = cfg.with_seed(args.seed)
    if args.supply_kw is not None:
        supply, label = MockSupply(MockGrid(args.supply_kw)), f"grid {args.supply_kw:.0f} kW"
    elif cfg.supply.get("sources"):
        supply = MockSupply.from_config(cfg.supply)
        label = ", ".join(f"{e['type']}" for e in cfg.supply["sources"])
    else:
        grid_kw = 0.55 * cfg.total_capacity_kw
        supply, label = MockSupply(MockGrid(grid_kw)), f"grid {grid_kw:.0f} kW (55% of capacity)"

    agents = build_agents(cfg)
    coordinator = MockCoordinator(agents, build_simulators(cfg), MockAuctioneer(), supply,
                                  resolution_minutes=cfg.simulation.resolution_minutes)
    t = time.perf_counter()
    records = coordinator.run(args.steps)
    elapsed = time.perf_counter() - t
    summary = summarise(records, agents)

    print(f"\n{cfg.name}: {len(agents)} buildings, {summary['steps']} closed-loop cycles in {elapsed:.2f}s "
          f"(mock supply: {label}; sequential, single process)")
    print(f"realised demand {summary['demand_kwh']:.0f} kWh, served {summary['served_kwh']:.0f} kWh, "
          f"deferred {summary['deferred_kwh']:.0f}, curtailed {summary['curtailed_kwh']:.0f}, "
          f"expired {summary['expired_kwh']:.0f}, unused allocation {summary['unused_allocation_kwh']:.0f} kWh")
    print(f"shortage cycles {summary['shortage_steps']}, revised-bid cycles {summary['revised_bid_steps']}, "
          f"market failures {summary['market_failures']}, critical shortfall events "
          f"{summary['critical_shortfall_events']} ({summary['critical_shortfall_kwh']:.2f} kWh)\n")
    rows = list(summary["buildings"].items())
    if len(rows) > 12:
        rows = rows[:12]
        print(f"(showing first 12 of {len(summary['buildings'])} buildings)")
    print(f"{'building':<18}{'service':>9}{'deferred':>11}{'curtailed':>11}{'expired':>10}{'crit.evts':>11}"
          f"{'fc MAE':>9}")
    for building_id, s in rows:
        print(f"{building_id:<18}{s['service_ratio']:>9.1%}{s['deferred_kwh']:>8.1f}kWh{s['curtailed_kwh']:>8.1f}kWh"
              f"{s['expired_kwh']:>7.1f}kWh{s['critical_shortfall_events']:>11}{s['forecast_mae_kw']:>7.2f}kW")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"summary": summary, "records": [r.to_dict() for r in records]}, indent=1))
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
