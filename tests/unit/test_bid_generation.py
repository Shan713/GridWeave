"""Priority model, pricing policy and bid generation."""
from __future__ import annotations

import random
from datetime import datetime

import pytest

from gridweave.bidding import (
    BidContext,
    BidGenerator,
    PricingPolicy,
    PriorityFactors,
    PriorityModel,
    PriorityWeights,
    make_bid_id,
)
from gridweave.classification import LoadClassifier
from gridweave.models import DemandState, TimeSlot
from gridweave.utils.validation import ValidationError

SLOT = TimeSlot(datetime(2026, 1, 5, 19, 0))
NOW = datetime(2026, 1, 5, 18, 45)


def state_for(spec, forecast_kw, backlog_kw=0.0):
    c = LoadClassifier(spec).classify(forecast_kw, backlog_kw)
    return DemandState(SLOT.start, forecast_kw, forecast_kw, backlog_kw, c.total_kw, c.critical_kw,
                       c.flexible_kw, c.minimum_kw, spec.capacity_kw)


def make_bid(spec, forecast_kw, backlog_kw=0.0, scarcity=0.0, deprivation=0.0, revision=0):
    state = state_for(spec, forecast_kw, backlog_kw)
    model = PriorityModel()
    base_kw = min(forecast_kw, state.desired_demand_kw)
    priority = model.score(model.factors(state.critical_demand_kw, base_kw, spec.importance,
                                         backlog_kw, spec.backlog_limit_kw, deprivation))
    return BidGenerator().generate(spec, state, priority, BidContext(SLOT, scarcity=scarcity), NOW,
                                   deprivation=deprivation, revision=revision)


# ------------------------------------------------------------------ priority
class TestPriority:
    def test_hand_computed_score(self):
        model = PriorityModel(PriorityWeights(criticality=1, importance=1, urgency=0, deprivation=0))
        b = model.score(PriorityFactors(criticality=0.6, importance=0.8, urgency=1.0, deprivation=1.0))
        assert b.score == pytest.approx(0.7)
        assert b.contributions["urgency"] == 0
        assert sum(b.weights.values()) == pytest.approx(1.0)

    def test_score_is_bounded_for_random_inputs(self):
        rng = random.Random(9)
        for _ in range(500):
            w = PriorityWeights(*(rng.uniform(0, 5) for _ in range(4)))
            f = PriorityFactors(*(rng.random() for _ in range(4)))
            assert 0.0 <= PriorityModel(w).score(f).score <= 1.0

    def test_extremes(self):
        m = PriorityModel()
        assert m.score(PriorityFactors(0, 0, 0, 0)).score == 0
        assert m.score(PriorityFactors(1, 1, 1, 1)).score == pytest.approx(1)

    def test_factors_are_clamped_and_handle_zero_request(self):
        f = PriorityModel.factors(critical_kw=0, base_demand_kw=0, importance=0.5, backlog_kw=500,
                                  backlog_limit_kw=100, deprivation=0.2)
        assert f.criticality == 0 and f.urgency == 1.0

    def test_invalid_weights(self):
        with pytest.raises(ValidationError):
            PriorityWeights(-1, 1, 1, 1)
        with pytest.raises(ValidationError):
            PriorityWeights(0, 0, 0, 0)

    def test_more_critical_building_ranks_higher(self, hostel_spec, lab_spec):
        assert make_bid(lab_spec, 80).priority_score > make_bid(hostel_spec, 80).priority_score

    def test_backlog_raises_priority(self, hostel_spec):
        assert make_bid(hostel_spec, 40, backlog_kw=30).priority_score > make_bid(hostel_spec, 40).priority_score


# ------------------------------------------------------------ bid generation
class TestBidGeneration:
    def test_normal_demand(self, hostel_spec):
        bid = make_bid(hostel_spec, 50)
        assert bid.requested_power_kw == 50
        assert bid.critical_power_kw == pytest.approx(20)  # 0.4 * 50 > 10 kW floor
        assert bid.flexible_power_kw == pytest.approx(30)
        assert bid.bid_id == "hostel_a:20260105T1900:r0"
        assert bid.created_at == NOW and bid.time_slot == SLOT
        assert bid.explanation["priority"]["score"] == bid.priority_score

    def test_high_demand_is_capped_at_capacity(self, hostel_spec):
        bid = make_bid(hostel_spec, 500)
        assert bid.requested_power_kw == hostel_spec.capacity_kw

    def test_low_demand_is_all_critical_below_operational_floor(self, hostel_spec):
        bid = make_bid(hostel_spec, 6)
        assert bid.critical_power_kw == bid.requested_power_kw == 6
        assert bid.flexibility_score == 0

    def test_zero_demand_bid_is_valid(self, hostel_spec):
        bid = make_bid(hostel_spec, 0)
        assert bid.requested_power_kw == 0 and bid.flexibility_score == 0

    def test_critical_load_building(self, lab_spec):
        bid = make_bid(lab_spec, 100)
        assert bid.critical_power_kw == pytest.approx(70)
        assert bid.minimum_power_kw == pytest.approx(70 + 0.2 * 30)
        assert bid.flexibility_score == pytest.approx((100 - 76) / 100)

    def test_extreme_shortage_raises_price_but_never_above_max(self, lab_spec):
        calm = make_bid(lab_spec, 100, scarcity=0.0)
        crisis = make_bid(lab_spec, 100, scarcity=1.0, deprivation=1.0, backlog_kw=150)
        assert crisis.willingness_to_pay > calm.willingness_to_pay
        assert crisis.willingness_to_pay <= crisis.maximum_price == lab_spec.max_price_per_kwh
        # shortage changes the price, never the physical request
        assert crisis.critical_power_kw == calm.critical_power_kw

    def test_wtp_is_monotonic_in_scarcity(self, hostel_spec):
        prices = [make_bid(hostel_spec, 50, scarcity=s / 10).willingness_to_pay for s in range(11)]
        assert prices == sorted(prices)
        assert prices[0] >= hostel_spec.base_price_per_kwh

    def test_revision_changes_bid_id(self, hostel_spec):
        assert make_bid(hostel_spec, 50, revision=2).bid_id.endswith(":r2")
        assert make_bid_id("x", SLOT, 0) == "x:20260105T1900:r0"

    def test_deterministic(self, hostel_spec):
        assert make_bid(hostel_spec, 42.5, 3.0, 0.3, 0.1) == make_bid(hostel_spec, 42.5, 3.0, 0.3, 0.1)

    def test_context_validation(self):
        with pytest.raises(ValidationError):
            BidContext(SLOT, scarcity=1.5)
        with pytest.raises(ValidationError):
            PricingPolicy(0, 0, 0)

    def test_invariants_on_random_inputs(self, lab_spec, hostel_spec):
        rng = random.Random(4)
        for _ in range(300):
            spec = rng.choice([lab_spec, hostel_spec])
            bid = make_bid(spec, rng.uniform(0, 200), rng.uniform(0, 100), rng.random(), rng.random())
            assert 0 <= bid.critical_power_kw <= bid.minimum_power_kw <= bid.requested_power_kw <= spec.capacity_kw
            assert bid.critical_power_kw + bid.flexible_power_kw == pytest.approx(bid.requested_power_kw)
            assert 0 <= bid.priority_score <= 1 and 0 <= bid.flexibility_score <= 1
            assert spec.base_price_per_kwh <= bid.willingness_to_pay <= bid.maximum_price
