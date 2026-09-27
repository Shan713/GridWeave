"""The Building Agent's local response to an allocation."""
from __future__ import annotations

import random
from datetime import datetime

import pytest

from gridweave.agents import AllocationMismatchError, respond_to_allocation
from gridweave.models import Allocation, AllocationStatus, Bid, TimeSlot

SLOT = TimeSlot(datetime(2026, 1, 5, 19, 0))


def bid(requested=40.0, minimum=28.0, critical=25.0):
    return Bid("b1", "hostel_a", SLOT, SLOT.start, requested, minimum, critical, requested - critical,
               priority_score=0.5, flexibility_score=0.3, willingness_to_pay=7, maximum_price=12)


def respond(allocated, deferrable=1.0, price=None, **bid_kw):
    b = bid(**bid_kw)
    return respond_to_allocation(b, Allocation("b1", "hostel_a", SLOT, allocated, clearing_price=price), deferrable)


def test_brief_example_partial_allocation():
    o = respond(32.0, minimum=25.0)
    assert o.status is AllocationStatus.PARTIAL
    assert o.critical_served_kw == 25 and o.critical_shortfall_kw == 0
    assert o.flexible_served_kw == pytest.approx(7)
    assert o.flexible_deferred_kw == pytest.approx(8)
    assert o.flexible_curtailed_kw == 0


def test_full_allocation():
    o = respond(40.0, price=8.0)
    assert o.status is AllocationStatus.FULL and o.satisfaction_ratio == 1
    assert o.flexible_deferred_kw == o.flexible_curtailed_kw == 0
    assert o.energy_cost == pytest.approx(40 * 0.25 * 8)


def test_over_allocation_is_left_unused():
    o = respond(55.0)
    assert o.accepted_kw == 40 and o.unused_allocation_kw == 15 and o.status is AllocationStatus.FULL


def test_critical_demand_only():
    o = respond(25.0, minimum=25.0)
    assert o.critical_served_kw == 25 and o.flexible_served_kw == 0
    assert o.flexible_deferred_kw == 15 and not o.has_critical_shortfall
    assert o.status is AllocationStatus.PARTIAL  # minimum == critical here, so minimum is met


def test_below_minimum_but_critical_safe():
    o = respond(26.0, minimum=28.0)
    assert o.status is AllocationStatus.BELOW_MINIMUM
    assert not o.minimum_met and not o.has_critical_shortfall


def test_allocation_below_critical_is_a_safety_event():
    o = respond(20.0)
    assert o.status is AllocationStatus.CRITICAL_SHORTFALL
    assert o.critical_served_kw == 20 and o.critical_shortfall_kw == 5
    assert o.flexible_served_kw == 0 and o.flexible_deferred_kw == 15
    assert o.has_critical_shortfall


def test_zero_allocation():
    o = respond(0.0)
    assert o.status is AllocationStatus.NONE
    assert o.accepted_kw == 0 and o.critical_shortfall_kw == 25 and o.satisfaction_ratio == 0


def test_zero_request_is_trivially_full():
    o = respond(0.0, requested=0.0, minimum=0.0, critical=0.0)
    assert o.status is AllocationStatus.FULL and o.satisfaction_ratio == 1


def test_deferrable_fraction_splits_unserved_flexible_load():
    o = respond(30.0, deferrable=0.25)
    assert o.flexible_served_kw == 5
    assert o.flexible_deferred_kw == pytest.approx(2.5) and o.flexible_curtailed_kw == pytest.approx(7.5)


@pytest.mark.parametrize("field, value", [("bid_id", "other"), ("building_id", "hostel_b"),
                                          ("time_slot", SLOT.next())])
def test_mismatched_allocation_rejected(field, value):
    kwargs = dict(bid_id="b1", building_id="hostel_a", time_slot=SLOT, allocated_power_kw=10)
    kwargs[field] = value
    with pytest.raises(AllocationMismatchError):
        respond_to_allocation(bid(), Allocation(**kwargs), 1.0)


def test_conservation_invariants_on_random_allocations():
    rng = random.Random(7)
    for _ in range(500):
        requested = rng.uniform(0, 100)
        critical = rng.uniform(0, requested)
        minimum = rng.uniform(critical, requested)
        allocated = rng.uniform(0, 1.3 * requested)
        o = respond(allocated, deferrable=rng.random(), requested=requested, minimum=minimum, critical=critical)
        assert o.accepted_kw <= o.requested_kw + 1e-9           # allocated_served <= requested
        assert o.critical_served_kw + o.flexible_served_kw == pytest.approx(o.accepted_kw)
        assert min(o.flexible_deferred_kw, o.flexible_curtailed_kw, o.critical_shortfall_kw) >= 0
        # critical load always gets served first
        assert o.flexible_served_kw == 0 or o.critical_shortfall_kw == 0
