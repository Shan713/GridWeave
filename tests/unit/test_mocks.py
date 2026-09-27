"""The mocks must themselves honour the contracts they stand in for."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.agents import BuildingAgent
from gridweave.contracts import validate_clearing, validate_dispatch
from gridweave.interfaces import Auctioneer, DemandAgent, EnvironmentStream, SupplyProvider
from gridweave.mocks import MockAuctioneer, MockBattery, MockGrid, MockSolar, MockSupply
from gridweave.models import Bid, DispatchRequest, SourceType, SupplyOffer, TimeSlot
from gridweave.simulation import BuildingSimulator
from gridweave.utils.validation import ValidationError

SLOT = TimeSlot(datetime(2026, 1, 5, 19, 0))
NOON = TimeSlot(datetime(2026, 1, 5, 12, 0))


def bid(building, requested, minimum, critical, priority=0.5, wtp=7.0, revision=0):
    return Bid(f"{building}:r{revision}", building, SLOT, SLOT.start, requested, minimum, critical,
               requested - critical, priority, 0.3, wtp, 12.0, revision=revision)


def grid(kw, price=10.0):
    return SupplyOffer("grid", SourceType.GRID, SLOT, kw, price)


def clear(bids, offers):
    result = MockAuctioneer().clear(SLOT, bids, offers)
    validate_clearing(result, bids, offers)                 # every mock clearing is contract-valid
    return result, {a.building_id: a.allocated_power_kw for a in result.allocations}


def test_mocks_satisfy_protocols(hostel_spec):
    assert isinstance(MockAuctioneer(), Auctioneer)
    assert isinstance(MockSupply(MockGrid(100)), SupplyProvider)
    assert isinstance(BuildingAgent(hostel_spec), DemandAgent)
    assert isinstance(BuildingSimulator(hostel_spec, []), EnvironmentStream)


def test_ample_supply_fills_every_bid():
    _, allocs = clear([bid("h1", 40, 30, 25), bid("h2", 20, 10, 5)], [grid(100)])
    assert allocs == {"h1": 40, "h2": 20}


def test_critical_tier_is_served_before_any_flexible_load():
    _, allocs = clear([bid("lab", 50, 40, 40, priority=0.9), bid("hostel", 60, 10, 10, priority=0.2)], [grid(55)])
    assert allocs["lab"] == pytest.approx(45) and allocs["hostel"] == pytest.approx(10)


def test_shortage_below_total_critical_is_shared_pro_rata():
    _, allocs = clear([bid("x", 40, 30, 30), bid("y", 20, 10, 10)], [grid(20)])
    assert allocs == {"x": pytest.approx(15), "y": pytest.approx(5)}


def test_allocations_never_exceed_supply_or_requests():
    bids = [bid(f"b{i}", 10 + i, 5 + i / 2, 2 + i / 4, priority=i / 10) for i in range(10)]
    result, allocs = clear(bids, [grid(80)])
    assert sum(allocs.values()) <= 80 + 1e-9
    for b in bids:
        assert allocs[b.building_id] <= b.requested_power_kw + 1e-9
    assert {a.clearing_price for a in result.allocations} == {b.willingness_to_pay for b in bids}


def test_merit_order_dispatch_cheapest_first_and_supply_mix_sums():
    offers = [grid(100, 10), SupplyOffer("solar", SourceType.SOLAR, SLOT, 25, 0),
              SupplyOffer("battery", SourceType.BATTERY, SLOT, 15, 7)]
    result, _ = clear([bid("h1", 30, 20, 20), bid("h2", 20, 10, 10)], offers)
    assert [(d.source_id, d.requested_kw) for d in result.dispatch] == [("solar", 25), ("battery", 15), ("grid", 10)]
    for a in result.allocations:
        assert sum(a.supply_mix.values()) == pytest.approx(a.allocated_power_kw)


def test_zero_supply():
    result, allocs = clear([bid("h1", 40, 30, 25)], [grid(0)])
    assert allocs == {"h1": 0} and result.dispatch == ()


def test_revised_bid_is_cleared_like_any_bid():
    """Replaces the old book-keeping revision test: the auction is now stateless
    (P4 passes the latest revision of each bid to clear())."""
    _, allocs = clear([bid("h", 32, 25, 25, revision=1)], [grid(100)])
    assert allocs == {"h": 32}


# ------------------------------------------------------------ supply mocks
def test_mock_grid_shortage_windows():
    g = MockGrid(400, [(19, 22, 0.8)])
    assert g.offer(SLOT).available_kw == pytest.approx(320)
    assert g.offer(NOON).available_kw == 400
    with pytest.raises(ValidationError):
        MockGrid(100, [(22, 19, 0.5)])


def test_mock_solar_is_zero_at_night_and_peaks_at_midday():
    s = MockSolar(60)
    assert s.offer(TimeSlot(datetime(2026, 1, 5, 2, 0))).available_kw == 0
    assert s.offer(NOON).available_kw > 55 and s.offer(NOON).marginal_price == 0


def test_mock_battery_soc_falls_with_dispatch_and_limits_delivery():
    b = MockBattery(capacity_kwh=100, max_power_kw=40, soc=0.8, min_soc=0.2)
    assert b.offer(SLOT).available_kw == 40 and b.offer(SLOT).constraints["soc"] == 0.8
    r = b.dispatch(DispatchRequest("battery", SLOT, 40))
    assert r.delivered_kw == 40 and r.state["soc"] == pytest.approx(0.7)       # 10 kWh out of 100 kWh
    b.soc = 0.21                                                               # 1 kWh usable left
    r = b.dispatch(DispatchRequest("battery", SLOT, 40))
    assert r.delivered_kw == pytest.approx(4) and r.shortfall_kw == pytest.approx(36)
    assert b.soc == pytest.approx(0.2)


def test_mock_supply_routes_dispatch_and_rejects_unknown_source():
    supply = MockSupply(MockGrid(100), MockSolar(50), MockBattery(50, 20))
    assert {o.source_id for o in supply.offers(NOON)} == {"grid", "solar", "battery"}
    reqs = [DispatchRequest("solar", NOON, 20), DispatchRequest("battery", NOON, 5)]
    validate_dispatch(reqs, supply.dispatch(reqs))
    with pytest.raises(ValidationError):
        supply.dispatch([DispatchRequest("wind", NOON, 1)])
    with pytest.raises(ValidationError):
        MockSupply(MockGrid(1), MockGrid(2))                                   # duplicate source ids


def test_mock_supply_from_config():
    supply = MockSupply.from_config({"sources": [{"type": "grid", "capacity_kw": 100},
                                                 {"type": "battery", "capacity_kwh": 50, "max_power_kw": 10}]})
    assert set(supply.sources) == {"grid", "battery"}
    with pytest.raises(ValidationError):
        MockSupply.from_config({"sources": [{"type": "wind"}]})
    with pytest.raises(ValidationError):
        MockSupply.from_config({})
