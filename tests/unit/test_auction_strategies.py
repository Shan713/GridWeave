"""Unit tests for P2 Allocation Strategies: Greedy, Optimized, Proportional, Priority."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.auction.strategies import (
    AllocationStrategy,
    GreedyAllocationStrategy,
    OptimizedAllocationStrategy,
    PriorityAllocationStrategy,
    ProportionalAllocationStrategy,
)
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import SourceType, SupplyOffer


@pytest.fixture
def slot():
    return TimeSlot(datetime(2026, 1, 5, 14, 0))


@pytest.fixture
def sample_bids(slot):
    b1 = Bid(
        bid_id="hostel_a:20260105T1400:r0",
        building_id="hostel_a",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 13, 45),
        requested_power_kw=35.0,
        minimum_power_kw=20.0,
        critical_power_kw=15.0,
        flexible_power_kw=20.0,
        priority_score=0.60,
        flexibility_score=0.428,
        willingness_to_pay=8.0,
        maximum_price=12.0,
    )
    b2 = Bid(
        bid_id="lab_eng:20260105T1400:r0",
        building_id="lab_eng",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 13, 45),
        requested_power_kw=40.0,
        minimum_power_kw=35.0,
        critical_power_kw=30.0,
        flexible_power_kw=10.0,
        priority_score=0.90,
        flexibility_score=0.125,
        willingness_to_pay=11.0,
        maximum_price=14.0,
    )
    b3 = Bid(
        bid_id="library:20260105T1400:r0",
        building_id="library",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 13, 45),
        requested_power_kw=25.0,
        minimum_power_kw=15.0,
        critical_power_kw=5.0,
        flexible_power_kw=20.0,
        priority_score=0.40,
        flexibility_score=0.40,
        willingness_to_pay=6.0,
        maximum_price=10.0,
    )
    return [b1, b2, b3]  # Total requested: 100 kW. Critical: 50 kW. Minimum: 70 kW.


@pytest.fixture
def supply_offers(slot):
    return [
        SupplyOffer("grid", SourceType.GRID, slot, 60.0, 10.0),
        SupplyOffer("solar", SourceType.SOLAR, slot, 20.0, 0.0),
    ]  # Total supply: 80 kW (Shortage: 20 kW unmet)


def test_strategies_satisfy_protocol():
    assert isinstance(GreedyAllocationStrategy(), AllocationStrategy)
    assert isinstance(OptimizedAllocationStrategy(), AllocationStrategy)
    assert isinstance(ProportionalAllocationStrategy(), AllocationStrategy)
    assert isinstance(PriorityAllocationStrategy(), AllocationStrategy)


def test_greedy_moderate_shortage(sample_bids, supply_offers, slot):
    strategy = GreedyAllocationStrategy()
    result = strategy.allocate(slot, sample_bids, supply_offers)

    alloc_map = {a.bid_id: a.allocated_power_kw for a in result.allocations}

    # Total allocated should be exactly total supply = 80 kW
    assert abs(sum(alloc_map.values()) - 80.0) < 1e-4

    # Critical loads must be 100% satisfied:
    # lab_eng: critical 30 <= alloc
    assert alloc_map["lab_eng:20260105T1400:r0"] >= 30.0
    # hostel_a: critical 15 <= alloc
    assert alloc_map["hostel_a:20260105T1400:r0"] >= 15.0
    # library: critical 5 <= alloc
    assert alloc_map["library:20260105T1400:r0"] >= 5.0

    # Minimum operational loads (70 kW total) should be satisfied with 80 kW supply
    assert alloc_map["lab_eng:20260105T1400:r0"] >= 35.0
    assert alloc_map["hostel_a:20260105T1400:r0"] >= 20.0
    assert alloc_map["library:20260105T1400:r0"] >= 15.0

    # Merit order dispatch check: solar (0.0 price) dispatched first (20 kW), grid covers rest (60 kW)
    disp_map = {d.source_id: d.requested_kw for d in result.dispatch}
    assert abs(disp_map["solar"] - 20.0) < 1e-4
    assert abs(disp_map["grid"] - 60.0) < 1e-4


def test_optimized_strategy_satisfies_critical_and_optimizes(sample_bids, supply_offers, slot):
    strategy = OptimizedAllocationStrategy()
    result = strategy.allocate(slot, sample_bids, supply_offers)

    alloc_map = {a.bid_id: a.allocated_power_kw for a in result.allocations}
    assert abs(sum(alloc_map.values()) - 80.0) < 1e-4

    # Lab has highest priority and WTP, should get full 40 kW request
    assert abs(alloc_map["lab_eng:20260105T1400:r0"] - 40.0) < 1e-4


def test_proportional_baseline_cuts_all_equally(sample_bids, supply_offers, slot):
    strategy = ProportionalAllocationStrategy()
    result = strategy.allocate(slot, sample_bids, supply_offers)

    alloc_map = {a.bid_id: a.allocated_power_kw for a in result.allocations}
    # 80 kW supply / 100 kW requested = 80% for all
    assert abs(alloc_map["hostel_a:20260105T1400:r0"] - 28.0) < 1e-4  # 35 * 0.8
    assert abs(alloc_map["lab_eng:20260105T1400:r0"] - 32.0) < 1e-4   # 40 * 0.8
    assert abs(alloc_map["library:20260105T1400:r0"] - 20.0) < 1e-4   # 25 * 0.8


def test_priority_only_baseline_starves_low_priority(sample_bids, slot):
    # Only 50 kW supply available
    limited_offers = [SupplyOffer("grid", SourceType.GRID, slot, 50.0, 10.0)]
    strategy = PriorityAllocationStrategy()
    result = strategy.allocate(slot, sample_bids, limited_offers)

    alloc_map = {a.bid_id: a.allocated_power_kw for a in result.allocations}
    # Priority order: lab_eng (0.90) gets 40 kW. Remaining = 10 kW.
    # hostel_a (0.60) gets 10 kW.
    # library (0.40) gets 0 kW, even though library had 5 kW critical demand!
    assert abs(alloc_map["lab_eng:20260105T1400:r0"] - 40.0) < 1e-4
    assert abs(alloc_map["hostel_a:20260105T1400:r0"] - 10.0) < 1e-4
    assert alloc_map["library:20260105T1400:r0"] == 0.0
