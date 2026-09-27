"""Generate the committed sample datasets in data/sample/.

* campus_demand_7d.csv      - 7 days x 15-min base demand for every configured building (synthetic)
* sample_bids.json          - one evening-peak bid per building (for P2)
* sample_offers.json        - the mock supply offers for that slot (for P2/P3)
* sample_clearing.json      - the MockAuctioneer's ClearingResult for those bids and offers

Usage: python scripts/generate_sample_data.py [--config PATH] [--days 7]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from gridweave.config import load_campus_config
from gridweave.contracts import validate_clearing
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockSupply
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
    for building_id, sim in sims.items():
        for _ in range(20 * 4):
            agents[building_id].observe(sim.step())
    bids = [a.generate_bid() for a in agents.values()]
    slot = bids[0].time_slot
    offers = MockSupply.from_config(cfg.supply).offers(slot)
    clearing = MockAuctioneer().clear(slot, bids, offers)
    validate_clearing(clearing, bids, offers)
    (SAMPLE / "sample_allocations.json").unlink(missing_ok=True)  # superseded by sample_clearing.json
    (SAMPLE / "sample_bids.json").write_text(json.dumps([b.to_dict() for b in bids], indent=2))
    (SAMPLE / "sample_offers.json").write_text(json.dumps([o.to_dict() for o in offers], indent=2))
    (SAMPLE / "sample_clearing.json").write_text(json.dumps(clearing.to_dict(), indent=2))
    print(f"wrote sample_bids.json, sample_offers.json, sample_clearing.json for slot {slot}")


if __name__ == "__main__":
    main()
