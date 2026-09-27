"""Settlement against REALISED demand (audit F1) and the deferred-energy queue (audit F5)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from gridweave.agents import AgentPhase, AgentStateError, BuildingAgent
from gridweave.forecasting import BaseForecaster, MovingAverageForecaster
from gridweave.models import Allocation, AllocationStatus, BuildingSpec, BuildingType, Observation, TimeSlot
from gridweave.utils.validation import ValidationError

T0 = datetime(2026, 1, 5, 18, 0)
STEP = timedelta(minutes=15)


class Fixed(BaseForecaster):
    """Always predicts the same value: lets tests control forecast error exactly."""

    name = "fixed"

    def __init__(self, value):
        self.value = value

    def _predict(self, values, horizon):
        return [self.value] * horizon


def make_agent(forecast_kw, **spec_kw):
    spec = BuildingSpec("h", "H", BuildingType.HOSTEL, capacity_kw=100, critical_fraction=0.5, **spec_kw)
    agent = BuildingAgent(spec, forecaster=Fixed(forecast_kw))
    agent.observe(Observation("h", T0, forecast_kw))
    return agent


def cycle(agent, allocated_kw, actual_kw):
    bid = agent.generate_bid()
    s = agent.settle(Allocation(bid.bid_id, "h", bid.time_slot, allocated_kw), Observation("h", bid.time_slot.start,
                                                                                          actual_kw))
    return bid, s


# ------------------------------------------------ allocation vs realised demand
def test_allocation_equal_to_actual_is_full():
    _, s = cycle(make_agent(40), 40, 40)
    assert s.status is AllocationStatus.FULL and s.served_kw == 40 and s.unused_allocation_kw == 0


def test_allocation_above_actual_leaves_unused_energy():
    """Over-forecast: allocation granted for 40 kW but only 30 kW occurred."""
    bid, s = cycle(make_agent(40), 40, 30)
    assert bid.requested_power_kw == 40
    assert s.served_kw == 30 and s.unused_allocation_kw == 10 and s.forecast_error_kw == 10
    assert s.status is AllocationStatus.FULL


def test_forecast_error_causes_shortage_even_when_bid_is_fully_allocated():
    """The brief's example: forecast 30, actual 40. The bid is fully met, the building is not."""
    agent = make_agent(30)
    bid, s = cycle(agent, 30, 40)
    assert agent.receive_allocation is not None
    assert s.allocated_kw == bid.requested_power_kw == 30         # ex-ante: 'full'
    assert s.served_kw == 30 and s.actual_total_kw == 40          # ex-post: 10 kW short
    assert s.critical_shortfall_kw == 0 and s.deferred_kw == pytest.approx(10)
    assert s.status is AllocationStatus.PARTIAL and s.forecast_error_kw == -10


def test_under_forecast_can_create_a_critical_shortfall():
    _, s = cycle(make_agent(10), 10, 60)                            # critical actual = 30 kW
    assert s.actual_critical_kw == 30 and s.critical_served_kw == 10
    assert s.critical_shortfall_kw == 20 and s.has_critical_shortfall
    assert s.status is AllocationStatus.CRITICAL_SHORTFALL


def test_flexible_shortfall_split_into_deferred_and_curtailed():
    _, s = cycle(make_agent(40, deferrable_fraction=0.25), 28, 40)   # 20 critical, 8 of 20 flexible served
    assert s.new_flexible_served_kw == 8
    assert s.deferred_kw == pytest.approx(3) and s.curtailed_kw == pytest.approx(9)


def test_zero_allocation_settles_cleanly():
    agent = make_agent(40)
    _, s = cycle(agent, 0, 40)
    assert s.status is AllocationStatus.NONE and s.critical_shortfall_kw == 20
    assert agent.phase is AgentPhase.SETTLED


# ---------------------------------------------------- deferred-energy queue
def test_deferred_energy_is_counted_once_across_repeated_deferral():
    """Serving order is critical -> backlog (oldest first) -> new flexible.
    Each kWh is counted as 'deferred' only when it is first postponed."""
    agent = make_agent(40)
    _, s1 = cycle(agent, 30, 40)   # need 40: 10 kW new flexible deferred (A, 2.5 kWh)
    _, s2 = cycle(agent, 30, 40)   # need 50: A served (10 kW), 20 kW new flexible deferred (B, 5 kWh)
    _, s3 = cycle(agent, 30, 40)   # need 60: 10 kW left after critical -> 10 kW of B served; C = 5 kWh
    assert (s1.deferred_kw, s2.deferred_kw, s3.deferred_kw) == pytest.approx((10, 20, 20))
    assert (s2.backlog_served_kw, s3.backlog_served_kw) == pytest.approx((10, 10))
    st = agent.stats
    assert st.deferred_kwh == pytest.approx(2.5 + 5 + 5)
    assert st.backlog_served_kwh == pytest.approx(2.5 + 2.5)
    assert agent.backlog_energy_kwh == pytest.approx(2.5 + 5)      # rest of B, and C
    assert st.demand_kwh == pytest.approx(3 * 10)                  # realised demand, backlog never re-counted
    assert agent.energy_balance_kwh() == pytest.approx(0, abs=1e-9)


def test_deferred_energy_expires_after_deadline():
    agent = make_agent(40, max_deferral_slots=2)
    cycle(agent, 30, 40)                          # slot 18:15: A = 2.5 kWh, may be served until 18:45
    _, s = cycle(agent, 10, 20)                   # 18:30: only critical served, A waits (B deferred)
    assert s.backlog_served_kw == 0 and s.backlog_expired_kwh == 0
    _, s = cycle(agent, 10, 20)                   # 18:45: deadline slot, A still valid
    assert s.backlog_expired_kwh == 0
    _, s = cycle(agent, 10, 20)                   # 19:00: A expired
    assert s.backlog_expired_kwh == pytest.approx(2.5)
    assert agent.stats.expired_kwh == pytest.approx(2.5)
    assert agent.energy_balance_kwh() == pytest.approx(0, abs=1e-9)


def test_backlog_served_oldest_first():
    agent = make_agent(40)
    cycle(agent, 20, 40)                          # A: 20 kW (5 kWh) deferred at 18:15
    cycle(agent, 20, 40)                          # B: 20 kW deferred at 18:30; A waits (only critical served)
    assert [e.origin for e in agent.backlog] == [T0 + STEP, T0 + 2 * STEP]
    _, s = cycle(agent, 50, 40)                   # 30 kW for flexible: all of A (20), then 10 of B; C deferred
    assert s.backlog_served_kw == pytest.approx(30)
    assert [e.origin for e in agent.backlog] == [T0 + 2 * STEP, T0 + 3 * STEP]
    assert agent.backlog[0].energy_kwh == pytest.approx(2.5)       # half of B left


# --------------------------------------------------------------- validation
def test_settlement_requires_matching_slot_and_capacity():
    agent = make_agent(40)
    bid = agent.generate_bid()
    alloc = Allocation(bid.bid_id, "h", bid.time_slot, 40)
    with pytest.raises(ValidationError):
        agent.settle(alloc, Observation("h", bid.time_slot.start + STEP, 40))   # wrong slot
    with pytest.raises(ValidationError):
        agent.settle(alloc, Observation("h", bid.time_slot.start, 150))        # above capacity
    assert agent.phase is AgentPhase.BID_PENDING                               # nothing was committed
    agent.settle(alloc, Observation("h", bid.time_slot.start, 40))


def test_abort_bid_returns_agent_to_observed():
    agent = make_agent(40)
    bid = agent.generate_bid()
    assert agent.abort_bid("auction down") == bid
    assert agent.phase is AgentPhase.OBSERVED and agent.pending_bid is None
    with pytest.raises(AgentStateError):
        agent.abort_bid()
    agent.observe(Observation("h", bid.time_slot.start, 40))        # the slot can now be reported normally


def test_voluntary_reduction_never_cuts_below_minimum():
    from gridweave.bidding import BidContext

    agent = make_agent(40, min_flexible_fraction=0.5, scarcity_response=1.0)
    bid = agent.generate_bid(BidContext(TimeSlot(T0 + STEP), scarcity=1.0))
    assert bid.requested_power_kw == pytest.approx(bid.minimum_power_kw) == pytest.approx(30)


def test_deprivation_is_driven_by_realised_service():
    agent = make_agent(30)
    cycle(agent, 30, 30)
    assert agent.deprivation == 0                                   # fully served
    cycle(agent, 30, 60)                                            # bid fully met, but half the need unserved
    assert agent.deprivation == pytest.approx(0.3 * 0.5)


# -------------------------------------------------------- conservation law
def test_energy_conservation_on_random_settlements():
    rng = random.Random(2026)
    for trial in range(40):
        spec = BuildingSpec("h", "H", BuildingType.LAB, capacity_kw=100, minimum_operational_kw=rng.uniform(0, 20),
                            critical_fraction=rng.random(), min_flexible_fraction=rng.random(),
                            deferrable_fraction=rng.random(), max_deferral_slots=rng.randint(1, 6),
                            max_backlog_kw=rng.choice([None, rng.uniform(0, 50)]))
        agent = BuildingAgent(spec, forecaster=MovingAverageForecaster(3))
        agent.observe(Observation("h", T0, rng.uniform(0, 100)))
        for _ in range(30):
            bid = agent.generate_bid()
            actual = rng.uniform(0, 100)
            alloc = rng.uniform(0, 1.2) * bid.requested_power_kw
            s = agent.settle(Allocation(bid.bid_id, "h", bid.time_slot, alloc), Observation("h", bid.time_slot.start,
                                                                                            actual))
            assert s.served_kw <= min(alloc, s.actual_total_kw) + 1e-9
            assert s.served_kw <= actual + s.backlog_attempted_kw + 1e-9
        assert agent.energy_balance_kwh() == pytest.approx(0, abs=1e-6), trial
