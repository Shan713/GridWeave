"""Scalability benchmark for CampusSupplyProvider across expanding source fleets.

Evaluates:
  1. Fleet Size Scaling (1, 5, 10, 25, 50, 100 sources)
  2. Simulation Duration Scaling (1 day / 96 slots, 4 days / 384 slots, 7 days / 672 slots)
  3. Offer generation latency (ms) and batch dispatch latency (ms)

Outputs JSON results to: data/results/p3_scaling_results.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.provider import CampusSupplyProvider
from gridweave.supply.solar_agent import SolarEnergyAgent


def benchmark_fleet_scaling() -> dict[str, Any]:
    print("=" * 70)
    print("BENCHMARK 1: FLEET SIZE SCALING")
    print("=" * 70)

    fleet_sizes = [1, 3, 9, 21, 45, 99]  # (1 grid + N solar + N battery)
    slot = TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)
    results: dict[str, Any] = {}

    for size in fleet_sizes:
        n_pairs = (size - 1) // 2
        grids = [GridSupplyAgent("grid_0", nominal_capacity_kw=500.0)]
        solars = [SolarEnergyAgent(f"solar_{i}", installed_capacity_kw=50.0) for i in range(n_pairs)]
        batteries = [BatteryStorageAgent(f"battery_{i}", capacity_kwh=100.0, initial_soc=0.8) for i in range(n_pairs)]

        provider = CampusSupplyProvider(grids, solars, batteries)
        total_sources = len(provider.sources)

        # 1. Benchmark offer generation
        t0 = time.perf_counter()
        n_trials = 100
        for _ in range(n_trials):
            offers = provider.offers(slot)
            slot = slot.next()
        offer_latency_ms = ((time.perf_counter() - t0) / n_trials) * 1000.0

        # 2. Benchmark dispatch execution
        t1 = time.perf_counter()
        for _ in range(n_trials):
            offers = provider.offers(slot)
            requests = [DispatchRequest(o.source_id, slot, min(10.0, o.available_kw)) for o in offers]
            provider.dispatch(requests)
            slot = slot.next()
        dispatch_latency_ms = ((time.perf_counter() - t1) / n_trials) * 1000.0

        results[f"fleet_{total_sources}"] = {
            "source_count": total_sources,
            "offer_latency_ms": round(offer_latency_ms, 3),
            "dispatch_latency_ms": round(dispatch_latency_ms, 3),
            "throughput_offers_per_sec": round(total_sources / (offer_latency_ms / 1000.0), 1),
        }

        print(
            f"  Fleet {total_sources:3d} sources -> "
            f"Offer Latency: {offer_latency_ms:6.3f} ms | "
            f"Dispatch Latency: {dispatch_latency_ms:6.3f} ms | "
            f"Throughput: {results[f'fleet_{total_sources}']['throughput_offers_per_sec']:8.1f} offers/s"
        )

    return results


def benchmark_horizon_scaling() -> dict[str, Any]:
    print("\n" + "=" * 70)
    print("BENCHMARK 2: SIMULATION HORIZON SCALING")
    print("=" * 70)

    durations_days = [1, 3, 7]  # 96, 288, 672 slots
    start_dt = datetime(2026, 6, 15, 0, 0)
    results: dict[str, Any] = {}

    for days in durations_days:
        total_slots = days * 96
        provider = CampusSupplyProvider()
        cur_slot = TimeSlot(start_dt, duration_minutes=15)

        t0 = time.perf_counter()
        for _ in range(total_slots):
            offers = provider.offers(cur_slot)
            reqs = [DispatchRequest(o.source_id, cur_slot, min(20.0, o.available_kw)) for o in offers]
            provider.dispatch(reqs)
            cur_slot = cur_slot.next()

        total_time_ms = (time.perf_counter() - t0) * 1000.0
        time_per_slot_ms = total_time_ms / total_slots

        results[f"{days}_days_{total_slots}_slots"] = {
            "total_slots": total_slots,
            "total_time_ms": round(total_time_ms, 2),
            "time_per_slot_ms": round(time_per_slot_ms, 3),
            "slots_per_second": round(total_slots / (total_time_ms / 1000.0), 1),
        }

        print(
            f"  Duration {days} days ({total_slots:4d} slots) -> "
            f"Total Time: {total_time_ms:7.2f} ms | "
            f"Per Slot: {time_per_slot_ms:5.3f} ms | "
            f"Throughput: {results[f'{days}_days_{total_slots}_slots']['slots_per_second']:6.1f} slots/s"
        )

    return results


def main() -> None:
    os.makedirs("data/results", exist_ok=True)
    b1 = benchmark_fleet_scaling()
    b2 = benchmark_horizon_scaling()

    combined = {
        "timestamp": datetime.now().isoformat(),
        "benchmark": "Person 3 — Scalability and Throughput",
        "fleet_scaling": b1,
        "horizon_scaling": b2,
    }

    out_path = "data/results/p3_scaling_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2)

    print("\n" + "=" * 70)
    print(f"BENCHMARK COMPLETE. Saved results to: {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
