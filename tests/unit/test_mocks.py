"""The mocks must themselves honour the contracts they stand in for."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.agents import BuildingAgent
from gridweave.interfaces import Auctioneer, DemandAgent, EnvironmentStream, SupplyProvider
from gridweave.mocks import MockAuctioneer, MockGrid
from gridweave.models import Bid, TimeSlot
from gridweave.simulation import BuildingSimulator
from gridweave.utils.validation import ValidationError

SLOT = TimeSlot(datetime(2026, 1, 5, 19, 0))


def bid(building, requested, minimum, critical, priority=0.5, wtp=7.0, revision=0):
    return Bid(f"{building}:r{revision}", building, SLOT, SLOT.start, requested, minimum, critical,
               requested - critical, priority, 0.3, wtp, 12.0, revision=revision)


def test_mocks_satisfy_protocols(hostel_spec):
    assert isinstance(MockAuctioneer(), Auctioneer)
    assert isinstance(MockGrid(100), SupplyProvider)
    assert isinstance(BuildingAgent(hostel_spec), DemandAgent)
    assert isinstance(BuildingSimulator(hostel_spec, []), EnvironmentStream)


def test_ample_supply_fills_every_bid():
    a = MockAuctioneer()
    a.submit_bid(bid("h1", 40, 30, 25))
    a.submit_bid(bid("h2", 20, 10, 5))
    allocs = {x.building_id: x.allocated_power_kw for x in a.clear(SLOT, 100)}
    assert allocs == {"h1": 40, "h2": 20}


def test_critical_tier_is_served_before_any_flexible_load():
    a = MockAuctioneer()
    a.submit_bid(bid("lab", 50, 40, 40, priority=0.9))
    a.submit_bid(bid("hostel", 60, 10, 10, priority=0.2))
    allocs = {x.building_id: x.allocated_power_kw for x in a.clear(SLOT, 55)}
    assert allocs["lab"] == pytest.approx(45) and allocs["hostel"] == pytest.approx(10)


def test_shortage_below_total_critical_is_shared_pro_rata():
    a = MockAuctioneer()
    a.submit_bid(bid("x", 40, 30, 30))
    a.submit_bid(bid("y", 20, 10, 10))
    allocs = {x.building_id: x.allocated_power_kw for x in a.clear(SLOT, 20)}
    assert allocs == {"x": pytest.approx(15), "y": pytest.approx(5)}


def test_allocations_never_exceed_supply_or_requests():
    a = MockAuctioneer()
    bids = [bid(f"b{i}", 10 + i, 5 + i / 2, 2 + i / 4, priority=i / 10) for i in range(10)]
    for b in bids:
        a.submit_bid(b)
    allocs = a.clear(SLOT, 80)
    assert sum(x.allocated_power_kw for x in allocs) <= 80 + 1e-9
    for b, x in zip(sorted(bids, key=lambda b: b.building_id), allocs):
        assert x.bid_id == b.bid_id and x.allocated_power_kw <= b.requested_power_kw + 1e-9
        assert x.clearing_price == b.willingness_to_pay


def test_revisions_replace_and_stale_revisions_are_rejected():
    a = MockAuctioneer()
    a.submit_bid(bid("h", 40, 30, 25, revision=0))
    a.submit_bid(bid("h", 40, 30, 25, revision=1))
    assert [b.revision for b in a.pending_bids(SLOT)] == [1]
    with pytest.raises(ValidationError):
        a.submit_bid(bid("h", 40, 30, 25, revision=0))
    assert a.clear(SLOT, 100)[0].bid_id == "h:r1"
    assert a.pending_bids(SLOT) == []


def test_mock_grid_shortage_windows():
    g = MockGrid(400, [(19, 22, 0.8)])
    assert g.available_power_kw(SLOT) == pytest.approx(320)
    assert g.available_power_kw(TimeSlot(datetime(2026, 1, 5, 12, 0))) == 400
    with pytest.raises(ValidationError):
        MockGrid(100, [(22, 19, 0.5)])
