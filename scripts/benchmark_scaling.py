"""Measure how the closed-loop simulation cost grows with the number of buildings.

The loop is sequential and single-process (MockCoordinator). Reported:
wall time per 15-min cycle, per agent-cycle, and peak traced memory.
Timing and memory are measured in separate runs (tracemalloc slows code down).

Usage: python scripts/benchmark_scaling.py [--sizes 3 5 10 50 100 500] [--steps 96]
"""
from __future__ import annotations

import argparse
import gc
import platform
import time
import tracemalloc

from gridweave.config import synthetic_campus
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockCoordinator, MockGrid, MockSupply


def run(n: int, steps: int, trace: bool) -> tuple[float, float]:
    cfg = synthetic_campus(n)
    agents, sims = build_agents(cfg), build_simulators(cfg, steps + 1)
    coord = MockCoordinator(agents, sims, MockAuctioneer(), MockSupply(MockGrid(cfg.total_capacity_kw * 0.5)),
                            keep_records=False)
    gc.collect()
    if trace:
        tracemalloc.start()
    t0 = time.perf_counter()
    coord.run(steps)
    elapsed = time.perf_counter() - t0
    peak = tracemalloc.get_traced_memory()[1] / 2**20 if trace else 0.0
    if trace:
        tracemalloc.stop()
    assert coord.steps_run == steps
    return elapsed, peak


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", type=int, nargs="+", default=[3, 5, 10, 50, 100, 500])
    parser.add_argument("--steps", type=int, default=96)
    args = parser.parse_args()
    print(f"Python {platform.python_version()} on {platform.machine()}, {args.steps} cycles, sequential single process")
    print(f"{'buildings':>9}{'total s':>9}{'ms/cycle':>10}{'us/agent-cycle':>16}{'peak MiB':>10}")
    for n in args.sizes:
        elapsed, _ = run(n, args.steps, trace=False)
        _, peak = run(n, args.steps, trace=True)
        print(f"{n:>9}{elapsed:>9.2f}{elapsed / args.steps * 1000:>10.1f}{elapsed / args.steps / n * 1e6:>16.0f}"
              f"{peak:>10.1f}")


if __name__ == "__main__":
    main()
