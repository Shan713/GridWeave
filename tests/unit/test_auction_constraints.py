"""Unit tests for P2 ConstraintValidator and EmergencyPolicy."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.auction.constraints import (
    ConstraintValidator,
    EmergencyPolicy,
    EmergencyPolicyType,
)
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import SourceType, SupplyOffer


@pytest.fixture
def slot():
    return TimeSlot(datetime(2026, 1, 5, 16, 0))


@pytest.fixture
def sample_bids(slot):
    b1 = Bid(
        bid_id="b1",
        building_id="b1",
        time_slot=slot,
        created_at=datetime.now(),
        requested_power_kw=30.0,
        minimum_power_kw=20.0,
        critical_power_kw=10.0,
        flexible_power_kw=20.0,
        priority_score=0.8,
        flexibility_score=0.33,
        willingness_to_pay=8.0,
        maximum_price=10.0,
    )
    b2 = Bid(
        bid_id="b2",
        building_id="b2",
        time_slot=slot,
        created_at=datetime.now(),
        requested_power_kw=40.0,
        minimum_power_kw=25.0,
        critical_power_kw=20.0,
        flexible_power_kw=20.0,
        priority_score=0.6,
        flexibility_score=0.375,
        willingness_to_pay=7.0,
        maximum_price=10.0,
    )
    return [b1, b2]  # Total critical = 30 kW


def test_emergency_policy_pro_rata(sample_bids):
    # Only 15 kW supply available (< 30 kW critical)
    allocs = EmergencyPolicy.ration_critical(
        sample_bids, available_supply=15.0, policy=EmergencyPolicyType.PRO_RATA
    )
    # Ratio = 15 / 30 = 0.50
    assert abs(allocs["b1"] - 5.0) < 1e-4   # 10 * 0.5
    assert abs(allocs["b2"] - 10.0) < 1e-4  # 20 * 0.5
    assert abs(sum(allocs.values()) - 15.0) < 1e-4


def test_emergency_policy_priority_order(sample_bids):
    # Only 15 kW supply available (< 30 kW critical)
    # b1 priority = 0.8, b2 priority = 0.6
    allocs = EmergencyPolicy.ration_critical(
        sample_bids, available_supply=15.0, policy=EmergencyPolicyType.PRIORITY_ORDER
    )
    # b1 gets its full critical load of 10 kW.
    # b2 gets remaining 5 kW.
    assert abs(allocs["b1"] - 10.0) < 1e-4
    assert abs(allocs["b2"] - 5.0) < 1e-4
    assert abs(sum(allocs.values()) - 15.0) < 1e-4


def test_constraint_validator_catches_violations(sample_bids, slot):
    validator = ConstraintValidator()
    offers = [SupplyOffer("grid", SourceType.GRID, slot, 50.0, 10.0)]

    # 1. Allocation exceeds request
    bad_alloc = {"b1": 35.0, "b2": 15.0}  # b1 request is 30
    disp = {"grid": 50.0}
    violations = validator.check_invariants(sample_bids, offers, bad_alloc, disp)
    assert any("exceeds requested" in v for v in violations)

    # 2. Total allocation exceeds supply
    bad_alloc2 = {"b1": 30.0, "b2": 30.0}  # total 60 > 50 supply
    disp2 = {"grid": 60.0}
    violations2 = validator.check_invariants(sample_bids, offers, bad_alloc2, disp2)
    assert any("exceeds total supply" in v for v in violations2)

    # 3. Energy imbalance: total allocated != total dispatched
    alloc_valid = {"b1": 20.0, "b2": 20.0}
    disp_imbalance = {"grid": 35.0}  # 40 != 35
    violations3 = validator.check_invariants(sample_bids, offers, alloc_valid, disp_imbalance)
    assert any("Energy imbalance" in v for v in violations3)
