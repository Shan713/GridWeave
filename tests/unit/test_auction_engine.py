"""Unit tests for P2 AuctionEngine lifecycle, re-auction, and clearing execution."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.auction.engine import AuctionEngine, MarketPhase
from gridweave.auction.strategies import (
    GreedyAllocationStrategy,
    OptimizedAllocationStrategy,
)
from gridweave.contracts import validate_clearing
from gridweave.interfaces import Auctioneer
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import SourceType, SupplyOffer
from gridweave.utils.validation import ValidationError


@pytest.fixture
def slot():
    return TimeSlot(datetime(2026, 1, 5, 18, 0))


@pytest.fixture
def sample_bids(slot):
    bids = [
        Bid(
            bid_id="hostel_a:20260105T1800:r0",
            building_id="hostel_a",
            time_slot=slot,
            created_at=datetime(2026, 1, 5, 17, 45),
            requested_power_kw=35.0,
            minimum_power_kw=20.0,
            critical_power_kw=20.0,
            flexible_power_kw=15.0,
            priority_score=0.65,
            flexibility_score=0.428,
            willingness_to_pay=8.5,
            maximum_price=12.0,
            revision=0,
        ),
        Bid(
            bid_id="hostel_b:20260105T1800:r0",
            building_id="hostel_b",
            time_slot=slot,
            created_at=datetime(2026, 1, 5, 17, 45),
            requested_power_kw=30.0,
            minimum_power_kw=18.0,
            critical_power_kw=18.0,
            flexible_power_kw=12.0,
            priority_score=0.70,
            flexibility_score=0.40,
            willingness_to_pay=9.0,
            maximum_price=12.0,
            revision=0,
        ),
        Bid(
            bid_id="lab_sci:20260105T1800:r0",
            building_id="lab_sci",
            time_slot=slot,
            created_at=datetime(2026, 1, 5, 17, 45),
            requested_power_kw=40.0,
            minimum_power_kw=35.0,
            critical_power_kw=35.0,
            flexible_power_kw=5.0,
            priority_score=0.95,
            flexibility_score=0.125,
            willingness_to_pay=12.0,
            maximum_price=15.0,
            revision=0,
        ),
    ]  # Total requested: 105 kW. Critical: 73 kW.
    return bids


@pytest.fixture
def sample_offers(slot):
    return [
        SupplyOffer("grid", SourceType.GRID, slot, 80.0, 10.0),
        SupplyOffer("battery", SourceType.BATTERY, slot, 20.0, 7.0),
    ]  # Total supply: 100 kW. Shortage = 5 kW.


def test_engine_satisfies_auctioneer_protocol():
    engine = AuctionEngine()
    assert isinstance(engine, Auctioneer)


def test_stateless_clearing(sample_bids, sample_offers, slot):
    engine = AuctionEngine(strategy=GreedyAllocationStrategy())
    clearing = engine.clear(slot, sample_bids, sample_offers)

    # Cross-party contract verification
    validate_clearing(clearing, sample_bids, sample_offers)

    assert clearing.time_slot == slot
    assert len(clearing.allocations) == 3
    assert abs(clearing.total_allocated_kw - 100.0) < 1e-4
    assert abs(clearing.total_dispatched_kw - 100.0) < 1e-4

    # Result inspection
    market_res = engine.get_last_result()
    assert market_res is not None
    assert len(market_res.decision_traces) == 3

    # All critical loads must be served (total critical 73 kW < 100 kW supply)
    for trace in market_res.decision_traces:
        assert trace.critical_shortfall_kw == 0.0
        assert trace.reason != ""


def test_stateful_lifecycle_and_re_auction(sample_bids, sample_offers, slot):
    engine = AuctionEngine(strategy=OptimizedAllocationStrategy())

    # 1. Open market
    engine.open_market(slot)
    assert engine.phase == MarketPhase.COLLECTING_BIDS

    # 2. Submit bids and offers
    for b in sample_bids:
        engine.submit_bid(b)
    for o in sample_offers:
        engine.submit_offer(o)

    # 3. Clear initial market
    res1 = engine.clear_market()
    assert engine.phase == MarketPhase.SETTLED
    assert abs(res1.total_allocated_kw - 100.0) < 1e-4

    # 4. Trigger Re-Auction: Solar/Grid supply drops from 100 kW to 70 kW (< 73 kW critical!)
    reduced_offers = [SupplyOffer("grid", SourceType.GRID, slot, 70.0, 10.0)]
    res2 = engine.re_auction(new_offers=reduced_offers)

    assert engine.phase == MarketPhase.SETTLED
    assert abs(res2.total_allocated_kw - 70.0) < 1e-4
    # Because supply 70 kW < total critical 73 kW, emergency rationing policy triggers
    assert res2.metrics.critical_shortfall_kw > 0.0


def test_re_auction_with_revised_bids(sample_bids, sample_offers, slot):
    engine = AuctionEngine()
    engine.open_market(slot)
    for b in sample_bids:
        engine.submit_bid(b)
    for o in sample_offers:
        engine.submit_offer(o)
    engine.clear_market()

    # Create a revised bid for hostel_a under demand response
    revised_b1 = Bid(
        bid_id="hostel_a:20260105T1800:r1",
        building_id="hostel_a",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 17, 50),
        requested_power_kw=25.0,  # Trimmed 10 kW flexible
        minimum_power_kw=20.0,
        critical_power_kw=20.0,
        flexible_power_kw=5.0,
        priority_score=0.65,
        flexibility_score=0.20,
        willingness_to_pay=8.5,
        maximum_price=12.0,
        revision=1,
    )

    re_res = engine.re_auction(revised_bids=[revised_b1])
    assert engine.phase == MarketPhase.SETTLED

    # Check that the allocation now targets revision 1
    alloc_ids = {a.bid_id for a in re_res.allocations}
    assert "hostel_a:20260105T1800:r1" in alloc_ids
    assert "hostel_a:20260105T1800:r0" not in alloc_ids


def test_edge_cases(slot):
    engine = AuctionEngine()

    # 1. Zero bids
    offers = [SupplyOffer("grid", SourceType.GRID, slot, 100.0, 10.0)]
    res = engine.clear(slot, [], offers)
    assert len(res.allocations) == 0
    assert res.total_allocated_kw == 0.0

    # 2. Zero supply
    bid = Bid(
        bid_id="b1:20260105T1800:r0",
        building_id="b1",
        time_slot=slot,
        created_at=datetime.now(),
        requested_power_kw=20.0,
        minimum_power_kw=10.0,
        critical_power_kw=10.0,
        flexible_power_kw=10.0,
        priority_score=0.5,
        flexibility_score=0.5,
        willingness_to_pay=5.0,
        maximum_price=10.0,
    )
    zero_offers = [SupplyOffer("grid", SourceType.GRID, slot, 0.0, 10.0)]
    res_zero = engine.clear(slot, [bid], zero_offers)
    assert res_zero.total_allocated_kw == 0.0
    assert len(res_zero.allocations) == 1
    assert res_zero.allocations[0].allocated_power_kw == 0.0
