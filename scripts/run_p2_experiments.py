"""Automated experiment runner for GridWeave Person 2 (Auction & Market Mechanism).

Executes reproducible experiments across 8 scenarios comparing 4 allocation strategies:
1. Proportional Baseline (uncoordinated brownout rationing)
2. Priority-Only Baseline (single-dimension priority ranking)
3. Greedy Tiered Auction (Person 2 Strategy A)
4. Optimized Welfare Auction (Person 2 Strategy B)

Saves machine-readable experimental data to data/results/p2_experiment_results.json
and prints formatted comparative performance tables for Review 1 & Review 2 evaluation.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from gridweave.auction import (
    AuctionEngine,
    GreedyAllocationStrategy,
    OptimizedAllocationStrategy,
    PriorityAllocationStrategy,
    ProportionalAllocationStrategy,
)
from gridweave.auction.fairness import FairnessTracker
from gridweave.auction.scoring import BidScorer
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import SourceType, SupplyOffer

ROOT_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT_DIR / "data" / "results"


def make_slot(hour: int = 12, minute: int = 0) -> TimeSlot:
    return TimeSlot(datetime(2026, 1, 5, hour, minute))


def create_standard_bids(slot: TimeSlot) -> list[Bid]:
    """Standard 4-building campus testbed:
    - Hostel A: 35 kW requested, 20 kW critical, priority 0.65, WTP 8.5
    - Hostel B: 30 kW requested, 18 kW critical, priority 0.70, WTP 9.0
    - Hostel C: 25 kW requested, 15 kW critical, priority 0.45, WTP 6.5
    - Eng Lab:  40 kW requested, 35 kW critical, priority 0.95, WTP 12.0
    Total Requested: 130 kW | Total Critical: 88 kW | Total Minimum: 100 kW
    """
    return [
        Bid(
            bid_id=f"hostel_a:{slot.start.strftime('%Y%m%dT%H%M')}:r0",
            building_id="hostel_a",
            time_slot=slot,
            created_at=datetime(2026, 1, 5, slot.start.hour - 1, 45),
            requested_power_kw=35.0,
            minimum_power_kw=25.0,
            critical_power_kw=20.0,
            flexible_power_kw=15.0,
            priority_score=0.65,
            flexibility_score=0.285,
            willingness_to_pay=8.5,
            maximum_price=12.0,
            capacity_kw=50.0,
        ),
        Bid(
            bid_id=f"hostel_b:{slot.start.strftime('%Y%m%dT%H%M')}:r0",
            building_id="hostel_b",
            time_slot=slot,
            created_at=datetime(2026, 1, 5, slot.start.hour - 1, 45),
            requested_power_kw=30.0,
            minimum_power_kw=22.0,
            critical_power_kw=18.0,
            flexible_power_kw=12.0,
            priority_score=0.70,
            flexibility_score=0.266,
            willingness_to_pay=9.0,
            maximum_price=12.0,
            capacity_kw=45.0,
        ),
        Bid(
            bid_id=f"hostel_c:{slot.start.strftime('%Y%m%dT%H%M')}:r0",
            building_id="hostel_c",
            time_slot=slot,
            created_at=datetime(2026, 1, 5, slot.start.hour - 1, 45),
            requested_power_kw=25.0,
            minimum_power_kw=18.0,
            critical_power_kw=15.0,
            flexible_power_kw=10.0,
            priority_score=0.45,
            flexibility_score=0.280,
            willingness_to_pay=6.5,
            maximum_price=10.0,
            capacity_kw=40.0,
        ),
        Bid(
            bid_id=f"eng_lab:{slot.start.strftime('%Y%m%dT%H%M')}:r0",
            building_id="eng_lab",
            time_slot=slot,
            created_at=datetime(2026, 1, 5, slot.start.hour - 1, 45),
            requested_power_kw=40.0,
            minimum_power_kw=35.0,
            critical_power_kw=35.0,
            flexible_power_kw=5.0,
            priority_score=0.95,
            flexibility_score=0.125,
            willingness_to_pay=12.0,
            maximum_price=15.0,
            capacity_kw=60.0,
        ),
    ]


def run_scenario(
    scenario_name: str,
    description: str,
    bids: Sequence[Bid],
    offers: Sequence[SupplyOffer],
    slot: TimeSlot,
    deprivation_map: dict[str, float] | None = None,
) -> dict[str, Any]:
    strategies = [
        ProportionalAllocationStrategy(),
        PriorityAllocationStrategy(),
        GreedyAllocationStrategy(),
        OptimizedAllocationStrategy(),
    ]
    results = {}

    for strat in strategies:
        engine = AuctionEngine(strategy=strat, track_fairness=False)
        if deprivation_map and engine.fairness_tracker:
            for b_id, d in deprivation_map.items():
                engine.fairness_tracker.records[b_id].consecutive_starved_slots = int(d * 5)

        engine.clear(slot, bids, offers)
        m_res = engine.get_last_result()
        assert m_res is not None

        results[strat.name] = {
            "total_allocated_kw": round(m_res.metrics.total_allocated_kw, 2),
            "total_unmet_kw": round(m_res.metrics.total_unmet_kw, 2),
            "critical_shortfall_kw": round(m_res.metrics.critical_shortfall_kw, 2),
            "supply_utilization": round(m_res.metrics.supply_utilization, 4),
            "service_ratio": round(m_res.metrics.service_ratio, 4),
            "jains_fairness_index": round(m_res.metrics.jains_fairness_index, 4),
            "total_cost": round(m_res.metrics.total_cost, 2),
            "average_price": round(m_res.metrics.average_price, 3),
            "runtime_ms": round(m_res.metrics.runtime_ms, 3),
            "allocations_by_building": {a.building_id: round(a.allocated_power_kw, 2) for a in m_res.allocations},
        }

    return {
        "scenario_name": scenario_name,
        "description": description,
        "total_requested_kw": sum(b.requested_power_kw for b in bids),
        "total_critical_kw": sum(b.critical_power_kw for b in bids),
        "total_supply_kw": sum(o.available_kw for o in offers),
        "strategies": results,
    }


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_experiments: list[dict[str, Any]] = []

    print("================================================================================")
    print(" GridWeave Person 2: Market Mechanism & Allocation Controlled Experiments")
    print("================================================================================\n")

    # -------------------------------------------------------------------------
    # Scenario 1: Supply > Demand (Abundant Power)
    # -------------------------------------------------------------------------
    slot1 = make_slot(10, 0)
    bids1 = create_standard_bids(slot1)  # 130 kW requested
    offers1 = [
        SupplyOffer("grid", SourceType.GRID, slot1, 100.0, 10.0),
        SupplyOffer("solar", SourceType.SOLAR, slot1, 50.0, 0.0),
        SupplyOffer("battery", SourceType.BATTERY, slot1, 20.0, 7.0),
    ]  # 170 kW supply
    exp1 = run_scenario("Scenario 1: Abundant Supply (Supply > Demand)",
                        "Supply (170 kW) exceeds demand (130 kW). All valid bids should be fully served.",
                        bids1, offers1, slot1)
    all_experiments.append(exp1)

    # -------------------------------------------------------------------------
    # Scenario 2: Moderate Shortage (Demand > Supply, Supply >= Critical)
    # -------------------------------------------------------------------------
    slot2 = make_slot(14, 0)
    bids2 = create_standard_bids(slot2)  # 130 kW requested, 88 kW critical
    offers2 = [
        SupplyOffer("grid", SourceType.GRID, slot2, 80.0, 10.0),
        SupplyOffer("solar", SourceType.SOLAR, slot2, 20.0, 0.0),
    ]  # 100 kW supply (30 kW shortage)
    exp2 = run_scenario("Scenario 2: Moderate Shortage (Supply 100 kW vs 130 kW)",
                        "Demand exceeds supply, but supply covers total critical load (88 kW). Critical loads must be protected.",
                        bids2, offers2, slot2)
    all_experiments.append(exp2)

    # -------------------------------------------------------------------------
    # Scenario 3: Severe Shortage (Supply < Critical Demand)
    # -------------------------------------------------------------------------
    slot3 = make_slot(16, 0)
    bids3 = create_standard_bids(slot3)  # 88 kW critical
    offers3 = [
        SupplyOffer("grid", SourceType.GRID, slot3, 60.0, 10.0),
    ]  # 60 kW supply (< 88 kW critical)
    exp3 = run_scenario("Scenario 3: Severe Emergency Shortage (Supply 60 kW < 88 kW Critical)",
                        "Grid capacity drop below total campus critical load. Emergency rationing policy triggers.",
                        bids3, offers3, slot3)
    all_experiments.append(exp3)

    # -------------------------------------------------------------------------
    # Scenario 4: Solar Intermittency Drop
    # -------------------------------------------------------------------------
    slot4 = make_slot(13, 0)
    bids4 = create_standard_bids(slot4)
    offers4 = [
        SupplyOffer("grid", SourceType.GRID, slot4, 70.0, 10.0),
        SupplyOffer("solar", SourceType.SOLAR, slot4, 15.0, 0.0),  # Sudden cloud cover drop
    ]  # 85 kW supply
    exp4 = run_scenario("Scenario 4: Solar Generation Drop (Cloud Intermittency)",
                        "Sudden drop in solar generation from 40 kW to 15 kW. Market clears dynamically.",
                        bids4, offers4, slot4)
    all_experiments.append(exp4)

    # -------------------------------------------------------------------------
    # Scenario 5: Battery Availability Change (Peak Discharge)
    # -------------------------------------------------------------------------
    slot5 = make_slot(19, 0)  # Evening peak
    bids5 = create_standard_bids(slot5)
    offers5 = [
        SupplyOffer("grid", SourceType.GRID, slot5, 75.0, 12.0),     # Peak grid tariff
        SupplyOffer("battery", SourceType.BATTERY, slot5, 35.0, 7.0), # Battery support
    ]  # 110 kW supply
    exp5 = run_scenario("Scenario 5: Battery Peak Shaving Support",
                        "Battery discharges at cheaper marginal price (7.0) to shave expensive peak grid tariff (12.0).",
                        bids5, offers5, slot5)
    all_experiments.append(exp5)

    # -------------------------------------------------------------------------
    # Scenario 6: High Willingness-To-Pay (Strategic Bidding)
    # -------------------------------------------------------------------------
    slot6 = make_slot(20, 0)
    bids6 = create_standard_bids(slot6)
    # Hostel A bids aggressively with WTP = 12.0 (equal to maximum_price)
    bids6[0] = Bid(
        bid_id=bids6[0].bid_id,
        building_id=bids6[0].building_id,
        time_slot=slot6,
        created_at=bids6[0].created_at,
        requested_power_kw=35.0,
        minimum_power_kw=25.0,
        critical_power_kw=20.0,
        flexible_power_kw=15.0,
        priority_score=0.65,
        flexibility_score=0.285,
        willingness_to_pay=12.0,  # Elevated price valuation
        maximum_price=12.0,
    )
    offers6 = [SupplyOffer("grid", SourceType.GRID, slot6, 100.0, 10.0)]
    exp6 = run_scenario("Scenario 6: Strategic Price Bidding (Hostel A WTP Raised)",
                        "Hostel A signals high economic valuation. Physical demand remains invariant.",
                        bids6, offers6, slot6)
    all_experiments.append(exp6)

    # -------------------------------------------------------------------------
    # Scenario 7: Fairness Deprivation Activation (Starvation Avoidance)
    # -------------------------------------------------------------------------
    slot7 = make_slot(21, 0)
    bids7 = create_standard_bids(slot7)
    # Hostel C has been starved for 4 consecutive slots, yielding high deprivation factor (0.80)
    dep_map = {"hostel_c": 0.80}
    offers7 = [SupplyOffer("grid", SourceType.GRID, slot7, 95.0, 10.0)]
    exp7 = run_scenario("Scenario 7: Starvation Mitigation via Deprivation Boost",
                        "Hostel C has experienced starvation; deprivation factor elevates its composite score.",
                        bids7, offers7, slot7, deprivation_map=dep_map)
    all_experiments.append(exp7)

    # -------------------------------------------------------------------------
    # Scenario 8: Dynamic Re-Auction Under Scarcity
    # -------------------------------------------------------------------------
    slot8 = make_slot(18, 0)
    bids8_round1 = create_standard_bids(slot8)
    offers8_round1 = [SupplyOffer("grid", SourceType.GRID, slot8, 100.0, 10.0)]
    # First auction clears with 100 kW supply. Then supply drops to 80 kW, buildings revise bids under demand response.
    # Eng lab voluntary reduction of 3 kW flexible load:
    revised_bids8 = [
        bids8_round1[0],
        bids8_round1[1],
        bids8_round1[2],
        Bid(
            bid_id="eng_lab:20260105T1800:r1",
            building_id="eng_lab",
            time_slot=slot8,
            created_at=datetime(2026, 1, 5, 17, 50),
            requested_power_kw=37.0,  # Reduced from 40 to 37
            minimum_power_kw=35.0,
            critical_power_kw=35.0,
            flexible_power_kw=2.0,
            priority_score=0.95,
            flexibility_score=0.054,
            willingness_to_pay=12.0,
            maximum_price=15.0,
            revision=1,
            voluntary_reduction_kw=3.0,
        ),
    ]
    offers8_round2 = [SupplyOffer("grid", SourceType.GRID, slot8, 80.0, 10.0)]
    exp8 = run_scenario("Scenario 8: Dynamic Re-Auction Round 2 (Demand Response Revised Bids)",
                        "Re-auction with reduced supply (80 kW) and revised demand-response bids with voluntary reduction.",
                        revised_bids8, offers8_round2, slot8)
    all_experiments.append(exp8)

    # Save results to file
    out_file = RESULTS_DIR / "p2_experiment_results.json"
    with open(out_file, "w") as f:
        json.dump(all_experiments, f, indent=2)

    print(f"-> Successfully executed 8 scenarios across 4 allocation strategies.")
    print(f"-> Saved complete machine-readable experimental dataset to: {out_file}\n")

    # Print summary comparative tables
    for exp in all_experiments:
        print(f"--------------------------------------------------------------------------------")
        print(f" {exp['scenario_name']}")
        print(f" {exp['description']}")
        print(f" Total Requested: {exp['total_requested_kw']} kW | Total Critical: {exp['total_critical_kw']} kW | Supply: {exp['total_supply_kw']} kW")
        print(f"--------------------------------------------------------------------------------")
        print(f"{'Strategy':<26} {'Alloc (kW)':<12} {'Unmet (kW)':<12} {'Crit Short':<12} {'Jain Index':<12} {'Cost':<10} {'Runtime (ms)'}")
        print("-" * 96)
        for s_name, data in exp["strategies"].items():
            print(f"{s_name:<26} {data['total_allocated_kw']:<12.2f} {data['total_unmet_kw']:<12.2f} "
                  f"{data['critical_shortfall_kw']:<12.2f} {data['jains_fairness_index']:<12.4f} "
                  f"{data['total_cost']:<10.2f} {data['runtime_ms']:<10.3f}")
        print()


if __name__ == "__main__":
    main()
