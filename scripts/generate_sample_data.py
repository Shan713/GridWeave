"""Generate the committed sample datasets in data/sample/.

* campus_demand_7d.csv  - 7 days x 15-min demand for every configured building
* sample_bids.json      - one realistic evening-peak bid per building (for P2)
* sample_allocations.json - the MockAuctioneer's allocations for those bids

Usage: python scripts/generate_sample_data.py [--config PATH] [--days 7]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from gridweave.config import load_campus_config
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockGrid
from gridweave.simulation import write_demand_csv

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    cfg = load_campus_config(args.config)
    cfg = replace(cfg, simulation=replace(cfg.simulation, days=args.days))
    sims = build_simulators(cfg)
    path = write_demand_csv(SAMPLE / f"campus_demand_{args.days}d.csv", {k: s.series for k, s in sims.items()})
    print(f"wrote {path.relative_to(ROOT)} ({sum(len(s.series) for s in sims.values())} rows)")

    # Replay Monday up to 19:45 and bid for the 20:00 evening-peak slot.
    agents = build_agents(cfg)
    steps = 20 * 4
    for building_id, sim in sims.items():
        for _ in range(steps):
            agents[building_id].observe(sim.step())
    bids = [a.generate_bid() for a in agents.values()]
    auction = MockAuctioneer()
    for b in bids:
        auction.submit_bid(b)
    grid = MockGrid(cfg.supply["mock_grid_capacity_kw"], cfg.supply.get("shortage_windows", []))
    allocations = auction.clear(bids[0].time_slot, grid.available_power_kw(bids[0].time_slot))
    (SAMPLE / "sample_bids.json").write_text(json.dumps([b.to_dict() for b in bids], indent=2))
    (SAMPLE / "sample_allocations.json").write_text(json.dumps([a.to_dict() for a in allocations], indent=2))
    print(f"wrote data/sample/sample_bids.json and sample_allocations.json for slot {bids[0].time_slot}")


if __name__ == "__main__":
    main()
