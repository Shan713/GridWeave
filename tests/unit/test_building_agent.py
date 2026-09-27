"""BuildingAgent lifecycle, state management and learning behaviour."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from gridweave.agents import AgentPhase, AgentStateError, AllocationMismatchError, BuildingAgent
from gridweave.bidding import BidContext
from gridweave.forecasting import MovingAverageForecaster, SpikeDetector
from gridweave.models import Allocation, AllocationStatus, Observation, TimeSlot
from gridweave.utils.validation import ValidationError

T0 = datetime(2026, 1, 5, 18, 0)
STEP = timedelta(minutes=15)


def feed(agent, values, start=T0):
    for i, v in enumerate(values):
        agent.observe(Observation(agent.building_id, start + i * STEP, v))


def allocate(agent, bid, kw, price=None):
    return agent.apply_allocation(Allocation(bid.bid_id, bid.building_id, bid.time_slot, kw, clearing_price=price))


@pytest.fixture
def agent(hostel_spec):
    return BuildingAgent(hostel_spec, forecaster=MovingAverageForecaster(4))


def test_initial_state(agent):
    assert agent.phase is AgentPhase.IDLE and agent.backlog_kw == 0 and agent.pending_bid is None
    with pytest.raises(AgentStateError):
        agent.generate_bid()
    with pytest.raises(AgentStateError):
        agent.forecast_demand()


def test_observe_validates_building_and_time_order(agent):
    feed(agent, [10.0])
    assert agent.phase is AgentPhase.OBSERVED and len(agent.history) == 1
    with pytest.raises(ValidationError):
        agent.observe(Observation("someone_else", T0 + STEP, 5.0))
    with pytest.raises(ValidationError):
        agent.observe(Observation(agent.building_id, T0, 5.0))  # not after the last one


def test_history_is_bounded(hostel_spec):
    agent = BuildingAgent(hostel_spec, history_limit=10)
    feed(agent, [1.0] * 25)
    assert len(agent.history) == 10


def test_full_lifecycle(agent):
    feed(agent, [36.0, 40.0, 42.0, 42.0])        # MA(4) forecast = 40
    state = agent.update_state()
    assert state.timestamp == T0 + 4 * STEP
    assert state.predicted_demand_kw == pytest.approx(40)
    assert state.critical_demand_kw == pytest.approx(16) and state.flexible_demand_kw == pytest.approx(24)

    bid = agent.generate_bid()
    assert agent.phase is AgentPhase.BID_PENDING and agent.pending_bid == bid
    assert bid.time_slot == TimeSlot(T0 + 4 * STEP)
    assert bid.requested_power_kw == pytest.approx(40)
    assert bid.explanation["forecast"]["method"] == "moving_average"

    outcome = allocate(agent, bid, 30.0, price=8.0)
    assert outcome.status is AllocationStatus.PARTIAL
    assert outcome.flexible_deferred_kw == pytest.approx(10)
    assert agent.phase is AgentPhase.SETTLED and agent.pending_bid is None
    assert agent.backlog_kw == pytest.approx(10)
    assert agent.deprivation == pytest.approx(0.3 * 0.25)
    assert agent.stats.served_energy_kwh == pytest.approx(7.5)
    assert agent.stats.total_cost == pytest.approx(60.0)


def test_backlog_is_requested_next_slot_and_cleared_when_served(agent):
    feed(agent, [40.0] * 4)
    allocate(agent, agent.generate_bid(), 30.0)
    feed(agent, [40.0], start=T0 + 4 * STEP)
    bid = agent.generate_bid()
    assert bid.requested_power_kw == pytest.approx(50)            # 40 forecast + 10 backlog
    assert bid.critical_power_kw == pytest.approx(16)             # backlog is never critical
    allocate(agent, bid, 50.0)
    assert agent.backlog_kw == 0


def test_backlog_is_capped_and_overflow_counted(hostel_spec):
    agent = BuildingAgent(hostel_spec.with_overrides(max_backlog_kw=5.0), forecaster=MovingAverageForecaster(1))
    feed(agent, [40.0])
    allocate(agent, agent.generate_bid(), 16.0)                  # 24 kW flexible deferred
    assert agent.backlog_kw == 5.0
    assert agent.stats.dropped_backlog_energy_kwh == pytest.approx(19 * 0.25)


def test_repeated_shortage_raises_priority_and_price(agent):
    feed(agent, [40.0] * 4)
    first = agent.generate_bid(BidContext(agent.next_slot(), scarcity=0.5))
    allocate(agent, first, 16.0)
    t = T0 + 4 * STEP
    for _ in range(3):
        feed(agent, [40.0], start=t)
        t += STEP
        bid = agent.generate_bid(BidContext(agent.next_slot(), scarcity=0.5))
        allocate(agent, bid, 16.0)
    assert bid.priority_score > first.priority_score
    assert bid.willingness_to_pay > first.willingness_to_pay


def test_rebid_for_same_slot_creates_revision(agent):
    feed(agent, [40.0] * 4)
    b0 = agent.generate_bid()
    b1 = agent.generate_bid(BidContext(b0.time_slot, scarcity=0.9))
    assert b1.revision == 1 and b1.bid_id.endswith(":r1") and agent.pending_bid == b1
    with pytest.raises(AllocationMismatchError):  # stale revision
        allocate(agent, b0, 40.0)
    allocate(agent, b1, 40.0)


def test_cannot_bid_for_another_slot_while_one_is_pending(agent):
    feed(agent, [40.0] * 4)
    b0 = agent.generate_bid()
    with pytest.raises(AgentStateError):
        agent.generate_bid(BidContext(b0.time_slot.next()))


def test_allocation_without_bid_is_rejected(agent):
    feed(agent, [40.0])
    with pytest.raises(AgentStateError):
        agent.apply_allocation(Allocation("x", agent.building_id, TimeSlot(T0), 1.0))


def test_receive_allocation_is_side_effect_free(agent):
    feed(agent, [40.0] * 4)
    bid = agent.generate_bid()
    preview = agent.receive_allocation(Allocation(bid.bid_id, bid.building_id, bid.time_slot, 10.0))
    assert preview.status is AllocationStatus.CRITICAL_SHORTFALL
    assert agent.phase is AgentPhase.BID_PENDING and agent.backlog_kw == 0


def test_critical_shortfall_is_counted(agent):
    feed(agent, [40.0] * 4)
    allocate(agent, agent.generate_bid(), 5.0)
    assert agent.stats.critical_shortfall_events == 1
    assert agent.stats.critical_shortfall_energy_kwh == pytest.approx(11 * 0.25)


def test_bid_for_slot_further_ahead_uses_matching_forecast_step(hostel_spec):
    from gridweave.forecasting import SeasonalNaiveForecaster

    agent = BuildingAgent(hostel_spec, forecaster=SeasonalNaiveForecaster(4))
    feed(agent, [10.0, 20.0, 30.0, 40.0])
    slot = TimeSlot(T0 + 6 * STEP)                                # 3 steps ahead
    assert agent.generate_bid(BidContext(slot)).requested_power_kw == pytest.approx(30.0)


def test_observing_while_bid_pending_keeps_phase(agent):
    feed(agent, [40.0] * 4)
    agent.generate_bid()
    feed(agent, [41.0], start=T0 + 4 * STEP)
    assert agent.phase is AgentPhase.BID_PENDING


def test_demand_outlook_for_supply_planning(agent):
    feed(agent, [40.0] * 4)
    outlook = agent.demand_outlook(horizon=3)
    assert len(outlook) == 3
    assert [p.timestamp for p in outlook] == [T0 + k * STEP for k in (4, 5, 6)]
    assert all(p.classification.critical_kw == pytest.approx(16) for p in outlook)


def test_spike_detection(hostel_spec):
    agent = BuildingAgent(hostel_spec, spike_detector=SpikeDetector(window=8))
    feed(agent, [40, 41, 39, 40, 42, 40, 41, 40])
    assert agent.observe(Observation(agent.building_id, T0 + 8 * STEP, 110.0)) is True
    assert agent.stats.anomalies_detected == 1


def test_snapshot_is_json_serialisable(agent):
    import json

    feed(agent, [40.0] * 4)
    bid = agent.generate_bid()
    allocate(agent, bid, 35.0)
    snap = json.loads(json.dumps(agent.snapshot()))
    assert snap["phase"] == "settled" and snap["last_outcome"]["status"] == "partial"
    assert snap["stats"]["slots_settled"] == 1
    assert [e.kind for e in agent.events] == ["observe"] * 4 + ["bid", "allocation"]
