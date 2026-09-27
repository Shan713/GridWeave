"""Domain model validation: impossible values must be unconstructable."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.models import (
    Allocation,
    AllocationOutcome,
    AllocationStatus,
    Bid,
    BuildingSpec,
    BuildingType,
    DemandSample,
    DemandState,
    LoadClassification,
    Observation,
    TimeSlot,
)
from gridweave.utils.validation import ValidationError


def make_bid(slot: TimeSlot, **overrides) -> Bid:
    fields = dict(
        bid_id="b1", building_id="hostel_a", time_slot=slot, created_at=slot.start,
        requested_power_kw=40.0, minimum_power_kw=25.0, critical_power_kw=25.0, flexible_power_kw=15.0,
        priority_score=0.5, flexibility_score=0.375, willingness_to_pay=7.0, maximum_price=12.0,
    )
    fields.update(overrides)
    return Bid(**fields)


# ----------------------------------------------------------------- Building
class TestBuildingSpec:
    def test_valid_building(self, hostel_spec):
        assert hostel_spec.building_type is BuildingType.HOSTEL
        assert hostel_spec.flexible_fraction == pytest.approx(0.6)
        assert hostel_spec.backlog_limit_kw == hostel_spec.capacity_kw

    def test_building_type_accepts_string(self):
        spec = BuildingSpec("x", "X", "lab", capacity_kw=10)
        assert spec.building_type is BuildingType.LAB

    @pytest.mark.parametrize("capacity", [0, -5, float("nan"), float("inf")])
    def test_invalid_capacity(self, capacity):
        with pytest.raises(ValidationError):
            BuildingSpec("x", "X", BuildingType.LAB, capacity_kw=capacity)

    def test_minimum_operational_cannot_exceed_capacity(self):
        with pytest.raises(ValidationError):
            BuildingSpec("x", "X", BuildingType.LAB, capacity_kw=10, minimum_operational_kw=11)

    @pytest.mark.parametrize(
        "field", ["importance", "critical_fraction", "deferrable_fraction", "min_flexible_fraction"]
    )
    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_fractions_must_be_in_unit_interval(self, field, value):
        with pytest.raises(ValidationError):
            BuildingSpec("x", "X", BuildingType.LAB, capacity_kw=10, **{field: value})

    @pytest.mark.parametrize("horizon", [0, -1, 1.5, True])
    def test_invalid_forecast_horizon(self, horizon):
        with pytest.raises(ValidationError):
            BuildingSpec("x", "X", BuildingType.LAB, capacity_kw=10, forecast_horizon=horizon)

    def test_base_price_cannot_exceed_max_price(self):
        with pytest.raises(ValidationError):
            BuildingSpec("x", "X", BuildingType.LAB, capacity_kw=10, base_price_per_kwh=20, max_price_per_kwh=10)

    def test_unknown_type_and_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            BuildingSpec("x", "X", "stadium", capacity_kw=10)
        with pytest.raises(ValidationError):
            BuildingSpec("  ", "X", BuildingType.LAB, capacity_kw=10)

    def test_with_overrides_revalidates(self, hostel_spec):
        assert hostel_spec.with_overrides(capacity_kw=200).capacity_kw == 200
        with pytest.raises(ValidationError):
            hostel_spec.with_overrides(capacity_kw=-1)


# ------------------------------------------------------------------- Demand
class TestDemandModels:
    def test_negative_demand_rejected(self, t0):
        with pytest.raises(ValidationError):
            DemandSample(t0, -1.0)
        with pytest.raises(ValidationError):
            Observation("b", t0, -0.5)

    def test_tiny_negative_rounding_error_is_clamped(self, t0):
        assert DemandSample(t0, -1e-9).demand_kw == 0.0

    def test_classification_invariants(self):
        c = LoadClassification(total_kw=40, critical_kw=25, flexible_kw=15, minimum_kw=28)
        assert c.flexibility_ratio == pytest.approx(12 / 40)
        with pytest.raises(ValidationError):  # parts don't add up
            LoadClassification(40, 25, 10, 25)
        with pytest.raises(ValidationError):  # minimum below critical
            LoadClassification(40, 25, 15, 20)
        with pytest.raises(ValidationError):  # minimum above total
            LoadClassification(40, 25, 15, 41)

    def _state(self, t0, **kw):
        base = dict(timestamp=t0, current_demand_kw=30, predicted_demand_kw=35, backlog_kw=5,
                    desired_demand_kw=40, critical_demand_kw=25, flexible_demand_kw=15,
                    minimum_demand_kw=25, maximum_demand_kw=120)
        base.update(kw)
        return DemandState(**base)

    def test_valid_state(self, t0):
        state = self._state(t0)
        assert state.classification.total_kw == 40
        assert state.to_dict()["desired_demand_kw"] == 40

    def test_state_rejects_desired_above_capacity(self, t0):
        with pytest.raises(ValidationError):
            self._state(t0, maximum_demand_kw=39)

    def test_state_rejects_critical_above_minimum(self, t0):
        with pytest.raises(ValidationError):
            self._state(t0, minimum_demand_kw=20)


# ---------------------------------------------------------------------- Bid
class TestBid:
    def test_valid_bid_and_roundtrip(self, slot):
        bid = make_bid(slot)
        assert bid.curtailable_power_kw == 15
        assert bid.requested_energy_kwh == pytest.approx(10.0)
        assert Bid.from_dict(bid.to_dict()) == bid

    @pytest.mark.parametrize(
        "overrides",
        [
            {"critical_power_kw": 30.0, "flexible_power_kw": 10.0},  # critical > minimum
            {"minimum_power_kw": 45.0},                               # minimum > requested
            {"flexible_power_kw": 10.0},                              # parts don't add up
            {"priority_score": 1.2},
            {"priority_score": -0.1},
            {"flexibility_score": 2.0},
            {"willingness_to_pay": 13.0},                             # above max price
            {"requested_power_kw": -40.0},
            {"revision": -1},
        ],
    )
    def test_invalid_bids_rejected(self, slot, overrides):
        with pytest.raises(ValidationError):
            make_bid(slot, **overrides)

    def test_bid_is_immutable(self, slot):
        bid = make_bid(slot)
        with pytest.raises(AttributeError):
            bid.requested_power_kw = 1.0  # type: ignore[misc]


# --------------------------------------------------------------- Allocation
class TestAllocation:
    def test_supply_mix_must_sum_to_allocation(self, slot):
        Allocation("b1", "hostel_a", slot, 32.0, supply_mix={"grid": 20, "solar": 12})
        with pytest.raises(ValidationError):
            Allocation("b1", "hostel_a", slot, 32.0, supply_mix={"grid": 20})

    def test_negative_allocation_and_price_rejected(self, slot):
        with pytest.raises(ValidationError):
            Allocation("b1", "hostel_a", slot, -1.0)
        with pytest.raises(ValidationError):
            Allocation("b1", "hostel_a", slot, 1.0, clearing_price=-2)

    def test_roundtrip(self, slot):
        a = Allocation("b1", "hostel_a", slot, 32.0, clearing_price=7.5, supply_mix={"grid": 32.0})
        assert Allocation.from_dict(a.to_dict()) == a

    def test_outcome_conservation_is_enforced(self, slot):
        ok = dict(bid_id="b1", building_id="h", time_slot=slot, status=AllocationStatus.PARTIAL,
                  requested_kw=40, minimum_kw=25, allocated_kw=32, accepted_kw=32, unused_allocation_kw=0,
                  critical_requested_kw=25, critical_served_kw=25, critical_shortfall_kw=0,
                  flexible_requested_kw=15, flexible_served_kw=7, flexible_deferred_kw=8, flexible_curtailed_kw=0)
        outcome = AllocationOutcome(**ok)
        assert outcome.minimum_met and outcome.satisfaction_ratio == pytest.approx(0.8)
        with pytest.raises(ValidationError):
            AllocationOutcome(**{**ok, "flexible_deferred_kw": 5})  # 3 kW vanished


def test_timeslot(slot):
    assert slot.end == datetime(2026, 1, 5, 18, 15)
    assert slot.hours == 0.25
    assert slot.next().start == slot.end
    assert TimeSlot.from_dict(slot.to_dict()) == slot
    with pytest.raises(ValidationError):
        TimeSlot(slot.start, 0)


class TestBidCapacity:
    """requested <= capacity is enforced only when the bid carries capacity (audit F9)."""

    def test_within_capacity(self, slot):
        assert make_bid(slot, capacity_kw=120.0).capacity_kw == 120.0

    def test_exactly_at_capacity(self, slot):
        bid = make_bid(slot, capacity_kw=40.0)
        assert bid.requested_power_kw == bid.capacity_kw

    def test_above_capacity_rejected(self, slot):
        with pytest.raises(ValidationError):
            make_bid(slot, capacity_kw=39.0)

    def test_without_capacity_context_no_check_is_possible(self, slot):
        assert make_bid(slot, requested_power_kw=10_000.0, flexible_power_kw=9_975.0).capacity_kw is None

    def test_capacity_roundtrip(self, slot):
        bid = make_bid(slot, capacity_kw=120.0, voluntary_reduction_kw=3.0)
        assert Bid.from_dict(bid.to_dict()) == bid

    def test_negative_reduction_rejected(self, slot):
        with pytest.raises(ValidationError):
            make_bid(slot, voluntary_reduction_kw=-1.0)
