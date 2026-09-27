"""BuildingAgent lifecycle, state management and adaptive behaviour.

Migrated from apply_allocation (bid-based outcome) to settle (realised
demand) after the audit (F1). Each original behaviour is still tested; the
realised demand passed to settle() is stated explicitly in every test.
"""
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


def settle(agent, bid, allocated_kw, actual_kw, price=None):
    allocation = Allocation(bid.bid_id, bid.building_id, bid.time_slot, allocated_kw, clearing_price=price)
    return agent.settle(allocation, Observation(bid.building_id, bid.time_slot.start, actual_kw))


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
    assert bid.requested_power_kw == pytest.approx(40) and bid.capacity_kw == 120
    assert bid.explanation["forecast"]["method"] == "moving_average"

    s = settle(agent, bid, 30.0, actual_kw=40.0, price=8.0)
    assert s.status is AllocationStatus.PARTIAL
    assert (s.served_kw, s.critical_served_kw, s.new_flexible_served_kw) == pytest.approx((30, 16, 14))
    assert s.deferred_kw == pytest.approx(10) and s.curtailed_kw == 0
    assert agent.phase is AgentPhase.SETTLED and agent.pending_bid is None
    assert agent.backlog_energy_kwh == pytest.approx(2.5) and agent.backlog_kw == pytest.approx(10)
    assert agent.deprivation == pytest.approx(0.3 * 0.25)
    assert agent.stats.demand_kwh == pytest.approx(10.0) and agent.stats.served_kwh == pytest.approx(7.5)
    assert agent.stats.total_cost == pytest.approx(60.0)
    assert agent.history[-1].timestamp == bid.time_slot.start  # realised demand joins the history


def test_backlog_is_requested_next_slot_and_cleared_when_served(agent):
    feed(agent, [40.0] * 4)
    settle(agent, agent.generate_bid(), 30.0, actual_kw=40.0)     # 10 kW deferred
    bid = agent.generate_bid()
    assert bid.requested_power_kw == pytest.approx(50)            # 40 forecast + 10 backlog
    assert bid.critical_power_kw == pytest.approx(16)             # backlog is never critical
    s = settle(agent, bid, 50.0, actual_kw=40.0)
    assert s.backlog_served_kw == pytest.approx(10) and agent.backlog_kw == 0
    # the deferred energy was counted once, when it was deferred (audit F5)
    assert agent.stats.deferred_kwh == pytest.approx(2.5) and agent.stats.backlog_served_kwh == pytest.approx(2.5)
    assert agent.energy_balance_kwh() == pytest.approx(0, abs=1e-9)


def test_backlog_is_capped_and_overflow_counted(hostel_spec):
    agent = BuildingAgent(hostel_spec.with_overrides(max_backlog_kw=5.0), forecaster=MovingAverageForecaster(1))
    feed(agent, [40.0])
    s = settle(agent, agent.generate_bid(), 16.0, actual_kw=40.0)  # 24 kW (6 kWh) deferred
    assert agent.backlog_kw == pytest.approx(5.0)
    assert s.backlog_expired_kwh == pytest.approx(19 * 0.25)
    assert agent.stats.expired_kwh == pytest.approx(19 * 0.25)


def test_repeated_shortage_raises_priority_and_price(agent):
    feed(agent, [40.0] * 4)
    first = agent.generate_bid(BidContext(agent.next_slot(), scarcity=0.5))
    settle(agent, first, 16.0, actual_kw=40.0)
    for _ in range(3):
        bid = agent.generate_bid(BidContext(agent.next_slot(), scarcity=0.5))
        settle(agent, bid, 16.0, actual_kw=40.0)
    assert bid.priority_score > first.priority_score
    assert bid.willingness_to_pay > first.willingness_to_pay


def test_rebid_for_same_slot_creates_revision(agent):
    feed(agent, [40.0] * 4)
    b0 = agent.generate_bid()
    b1 = agent.generate_bid(BidContext(b0.time_slot, scarcity=0.9))
    assert b1.revision == 1 and b1.bid_id.endswith(":r1") and agent.pending_bid == b1
    # the revision is a real demand-response offer, not just a price change (audit F6)
    assert b1.voluntary_reduction_kw == pytest.approx(0.9 * 0.5 * (b0.requested_power_kw - b0.minimum_power_kw))
    assert b1.requested_power_kw == pytest.approx(b0.requested_power_kw - b1.voluntary_reduction_kw)
    assert b1.minimum_power_kw == b0.minimum_power_kw and b1.critical_power_kw == b0.critical_power_kw
    with pytest.raises(AllocationMismatchError):  # stale revision
        settle(agent, b0, 40.0, actual_kw=40.0)
    settle(agent, b1, b1.requested_power_kw, actual_kw=40.0)


def test_cannot_bid_for_another_slot_while_one_is_pending(agent):
    feed(agent, [40.0] * 4)
    b0 = agent.generate_bid()
    with pytest.raises(AgentStateError):
        agent.generate_bid(BidContext(b0.time_slot.next()))


def test_cannot_bid_for_an_already_observed_slot(agent):
    feed(agent, [40.0] * 4)
    with pytest.raises(ValidationError):
        agent.generate_bid(BidContext(TimeSlot(T0 + 3 * STEP)))


def test_allocation_without_bid_is_rejected(agent):
    feed(agent, [40.0])
    with pytest.raises(AgentStateError):
        agent.settle(Allocation("x", agent.building_id, TimeSlot(T0 + STEP), 1.0),
                     Observation(agent.building_id, T0 + STEP, 40.0))


def test_receive_allocation_is_side_effect_free(agent):
    feed(agent, [40.0] * 4)
    bid = agent.generate_bid()
    preview = agent.receive_allocation(Allocation(bid.bid_id, bid.building_id, bid.time_slot, 10.0))
    assert preview.status is AllocationStatus.CRITICAL_SHORTFALL
    assert agent.phase is AgentPhase.BID_PENDING and agent.backlog_kw == 0


def test_critical_shortfall_is_counted(agent):
    feed(agent, [40.0] * 4)
    s = settle(agent, agent.generate_bid(), 5.0, actual_kw=40.0)
    assert s.has_critical_shortfall and s.critical_shortfall_kw == pytest.approx(11)
    assert agent.stats.critical_shortfall_events == 1
    assert agent.stats.critical_shortfall_kwh == pytest.approx(11 * 0.25)


def test_bid_for_slot_further_ahead_uses_matching_forecast_step(hostel_spec):
    from gridweave.forecasting import SeasonalNaiveForecaster

    agent = BuildingAgent(hostel_spec, forecaster=SeasonalNaiveForecaster(4))
    feed(agent, [10.0, 20.0, 30.0, 40.0])
    slot = TimeSlot(T0 + 6 * STEP)                                # 3 steps ahead
    assert agent.generate_bid(BidContext(slot)).requested_power_kw == pytest.approx(30.0)


def test_observing_while_bid_pending(agent):
    """Replaces test_observing_while_bid_pending_keeps_phase: with settlement,
    the pending slot itself must be closed by settle(); earlier slots can still
    be observed (bid placed several slots ahead)."""
    feed(agent, [40.0] * 4)
    bid = agent.generate_bid(BidContext(TimeSlot(T0 + 5 * STEP)))
    agent.observe(Observation(agent.building_id, T0 + 4 * STEP, 41.0))
    assert agent.phase is AgentPhase.BID_PENDING
    with pytest.raises(AgentStateError):
        agent.observe(Observation(agent.building_id, T0 + 5 * STEP, 41.0))
    settle(agent, bid, 40.0, actual_kw=41.0)
    assert agent.phase is AgentPhase.SETTLED


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
    settle(agent, bid, 35.0, actual_kw=40.0)
    snap = json.loads(json.dumps(agent.snapshot()))
    assert snap["phase"] == "settled" and snap["last_settlement"]["status"] == "partial"
    assert snap["stats"]["slots_settled"] == 1 and len(snap["backlog"]) == 1
    assert [e.kind for e in agent.events] == ["observe"] * 4 + ["bid", "observe", "settle"]


def test_explicit_zero_horizon_is_rejected_not_defaulted(agent):
    feed(agent, [40.0] * 4)
    assert agent.forecast_demand().horizon == agent.spec.forecast_horizon
    with pytest.raises(ValidationError):
        agent.forecast_demand(0)
