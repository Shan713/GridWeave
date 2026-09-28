"""Closed-loop integration tests combining P1 Building Agents, P2 Auction Engine, and P3 Supply Subsystem."""
from datetime import datetime, timedelta

from gridweave.agents.building_agent import BuildingAgent
from gridweave.auction.engine import AuctionEngine
from gridweave.contracts import validate_clearing, validate_dispatch
from gridweave.models import BuildingSpec, BuildingType
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.demand import Observation
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.provider import CampusSupplyProvider
from gridweave.supply.solar_agent import SolarEnergyAgent


def test_p2_auction_clearing_with_p3_production_offers():
    """Verify that P2 AuctionEngine clears bids against P3 CampusSupplyProvider offers and P3 executes dispatch."""
    slot = TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)

    # 1. P3 Supply Subsystem
    grid = GridSupplyAgent("grid", nominal_capacity_kw=300.0)
    solar = SolarEnergyAgent("solar", installed_capacity_kw=150.0)
    battery = BatteryStorageAgent("battery", capacity_kwh=100.0, initial_soc=0.80)
    provider = CampusSupplyProvider([grid], [solar], [battery])

    offers = provider.offers(slot)
    assert len(offers) == 3

    # 2. P1 Bids
    bids = [
        Bid(
            bid_id="bid_lab",
            building_id="lab_biotech",
            time_slot=slot,
            created_at=slot.start,
            requested_power_kw=80.0,
            minimum_power_kw=40.0,
            critical_power_kw=40.0,
            flexible_power_kw=40.0,
            priority_score=0.9,
            flexibility_score=0.5,
            willingness_to_pay=15.0,
            maximum_price=20.0,
        ),
        Bid(
            bid_id="bid_hostel",
            building_id="hostel_block_a",
            time_slot=slot,
            created_at=slot.start,
            requested_power_kw=60.0,
            minimum_power_kw=20.0,
            critical_power_kw=20.0,
            flexible_power_kw=40.0,
            priority_score=0.6,
            flexibility_score=0.5,
            willingness_to_pay=10.0,
            maximum_price=15.0,
        ),
    ]

    # 3. P2 Auction Engine Clears Market
    engine = AuctionEngine()
    clearing = engine.clear(slot, bids, offers)

    # Cross-workstream contract validation
    validate_clearing(clearing, bids, offers)

    assert clearing.total_allocated_kw == 140.0
    assert len(clearing.dispatch) > 0

    # 4. P3 Physical Dispatch Execution
    results = provider.dispatch(clearing.dispatch)
    validate_dispatch(clearing.dispatch, results)

    # Total dispatched power must balance total allocated
    sum_delivered = sum(r.delivered_kw for r in results)
    assert round(sum_delivered, 3) == round(clearing.total_allocated_kw, 3)

    # Accounting check
    summary = provider.accountant.summary()
    assert summary["cumulative_delivered_kwh"] == sum_delivered * slot.hours


def test_full_p1_p2_p3_closed_loop_cycle():
    """Verify one complete end-to-end slot: P1 bid -> P3 offer -> P2 clear -> P3 dispatch -> P1 settle."""
    slot = TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)

    # P1 Agent
    spec = BuildingSpec(
        building_id="lab_1",
        name="Research Lab",
        building_type=BuildingType.LAB,
        capacity_kw=100.0,
        critical_fraction=0.4,
    )
    b_agent = BuildingAgent(spec)
    # Warm up with observation at t-1 (11:45)
    prev_time = slot.start - timedelta(minutes=15)
    b_agent.observe(Observation("lab_1", prev_time, 50.0))
    bid = b_agent.generate_bid()
    assert bid.time_slot == slot

    # P3 Provider
    provider = CampusSupplyProvider()
    offers = provider.offers(slot)

    # P2 Auctioneer
    engine = AuctionEngine()
    clearing = engine.clear(slot, [bid], offers)
    validate_clearing(clearing, [bid], offers)

    # P3 Dispatch
    results = provider.dispatch(clearing.dispatch)
    validate_dispatch(clearing.dispatch, results)

    # P1 Settlement
    alloc = clearing.allocations[0]
    realised = Observation("lab_1", slot.start, 55.0)
    settlement = b_agent.settle(alloc, realised)

    assert settlement.building_id == "lab_1"
    assert settlement.allocated_kw == alloc.allocated_power_kw
    assert settlement.critical_shortfall_kw == 0.0


def test_dynamic_event_mid_simulation_re_clearing():
    """Verify system response when a grid blackout occurs mid-operation."""
    slot = TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)

    grid = GridSupplyAgent("grid", nominal_capacity_kw=300.0)
    solar = SolarEnergyAgent("solar", installed_capacity_kw=100.0)
    battery = BatteryStorageAgent("battery", capacity_kwh=100.0, initial_soc=0.80)
    provider = CampusSupplyProvider([grid], [solar], [battery])

    engine = AuctionEngine()

    # High demand (200 kW) that exceeds solar + battery capacity, requiring grid import
    bid = Bid(
        bid_id="bid_crit",
        building_id="hospital_center",
        time_slot=slot,
        created_at=slot.start,
        requested_power_kw=200.0,
        minimum_power_kw=100.0,
        critical_power_kw=80.0,
        flexible_power_kw=120.0,
        priority_score=1.0,
        flexibility_score=0.2,
        willingness_to_pay=25.0,
        maximum_price=30.0,
    )

    # Before event: grid is available and dispatched for residual load
    offers1 = provider.offers(slot)
    clearing1 = engine.clear(slot, [bid], offers1)
    assert any(d.source_id == "grid" and d.requested_kw > 0 for d in clearing1.dispatch)

    # Trigger Grid Outage
    provider.events.trigger_grid_outage()

    # Regenerate offers
    offers2 = provider.offers(slot)
    grid_offer = next(o for o in offers2 if o.source_id == "grid")
    assert grid_offer.available_kw == 0.0

    # Re-auction under outage: Solar + Battery must cover the load!
    clearing2 = engine.clear(slot, [bid], offers2)
    assert not any(d.source_id == "grid" and d.requested_kw > 0 for d in clearing2.dispatch)
    assert clearing2.allocations[0].allocated_power_kw > 0.0

    # Execute dispatch
    results2 = provider.dispatch(clearing2.dispatch)
    validate_dispatch(clearing2.dispatch, results2)
