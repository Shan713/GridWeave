"""Scalability benchmark for GridWeave Person 2 Auction Subsystem.

Measures market clearing runtime and memory scaling across:
3, 10, 50, 100, 500 buildings.
Compares GreedyTieredAuction vs OptimizedWelfareAuction.
Saves results to data/results/p2_scaling_results.json.
"""
from __future__ import annotations

import json
import time
import tracemalloc
from pathlib import Path

from gridweave.auction import (
    AuctionEngine,
    GreedyAllocationStrategy,
    OptimizedAllocationStrategy,
)
from gridweave.config import synthetic_campus
from gridweave.factory import build_agents, build_simulators
from gridweave.models.bid import Bid
from gridweave.models.context import BidContext
from gridweave.models.supply import SourceType, SupplyOffer

ROOT_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT_DIR / "data" / "results"


def benchmark_size(n_buildings: int, trials: int = 5) -> dict:
    cfg = synthetic_campus(n_buildings, seed=42)
    agents = build_agents(cfg)
    sims = build_simulators(cfg, periods=4)

    # Observe initial slot
    for b_id, s in sims.items():
        agents[b_id].observe(s.step())

    slot = next(iter(agents.values())).next_slot()
    bids: list[Bid] = [a.generate_bid(BidContext(slot)) for a in agents.values()]
    total_requested = sum(b.requested_power_kw for b in bids)

    # Supply set to 80% of demand (scarcity stress test)
    offers = [
        SupplyOffer("grid", SourceType.GRID, slot, total_requested * 0.60, 10.0),
        SupplyOffer("battery", SourceType.BATTERY, slot, total_requested * 0.20, 7.0),
    ]

    greedy_engine = AuctionEngine(strategy=GreedyAllocationStrategy(), track_fairness=False)
    opt_engine = AuctionEngine(strategy=OptimizedAllocationStrategy(), track_fairness=False)

    # Benchmark Greedy
    tracemalloc.start()
    t_start = time.perf_counter()
    for _ in range(trials):
        greedy_engine.clear(slot, bids, offers)
    t_greedy = (time.perf_counter() - t_start) / trials * 1000.0
    _, peak_mem_greedy = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Benchmark Optimized
    tracemalloc.start()
    t_start = time.perf_counter()
    for _ in range(trials):
        opt_engine.clear(slot, bids, offers)
    t_opt = (time.perf_counter() - t_start) / trials * 1000.0
    _, peak_mem_opt = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "buildings": n_buildings,
        "total_requested_kw": round(total_requested, 2),
        "greedy_runtime_ms": round(t_greedy, 3),
        "greedy_us_per_bid": round((t_greedy * 1000.0) / n_buildings, 1),
        "greedy_peak_mem_kb": round(peak_mem_greedy / 1024.0, 1),
        "opt_runtime_ms": round(t_opt, 3),
        "opt_us_per_bid": round((t_opt * 1000.0) / n_buildings, 1),
        "opt_peak_mem_kb": round(peak_mem_opt / 1024.0, 1),
    }


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    building_counts = [3, 10, 50, 100, 500]
    records = []

    print("================================================================================")
    print(" GridWeave Person 2: Auction Engine Scalability Benchmark")
    print("================================================================================\n")
    print(f"{'Buildings':<12} {'Req (kW)':<12} {'Greedy (ms)':<14} {'µs/bid':<10} {'Opt (ms)':<14} {'µs/bid':<10}")
    print("-" * 75)

    for n in building_counts:
        rec = benchmark_size(n)
        records.append(rec)
        print(f"{rec['buildings']:<12} {rec['total_requested_kw']:<12.1f} {rec['greedy_runtime_ms']:<14.3f} "
              f"{rec['greedy_us_per_bid']:<10.1f} {rec['opt_runtime_ms']:<14.3f} {rec['opt_us_per_bid']:<10.1f}")

    out_file = RESULTS_DIR / "p2_scaling_results.json"
    with open(out_file, "w") as f:
        json.dump(records, f, indent=2)

    print(f"\n-> Benchmark complete. Saved scaling data to: {out_file}")


if __name__ == "__main__":
    main()
