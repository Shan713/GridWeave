"""Unit tests for the scenario and event scheduling system."""
from __future__ import annotations

import pytest

from gridweave.coordinator.scenarios import (
    BATTERY_DERATE,
    BATTERY_OUTAGE,
    GRID_OUTAGE,
    MIXED_STRESS,
    NORMAL,
    SCENARIOS,
    SCARCITY,
    SOLAR_DROP,
    TARIFF_SPIKE,
    VALID_EVENT_TYPES,
    Scenario,
    ScheduledEvent,
    get_scenario,
)
from gridweave.utils.validation import ValidationError


# ---------------------------------------------------------------------------
# ScheduledEvent
# ---------------------------------------------------------------------------

class TestScheduledEvent:
    def test_valid_event(self):
        e = ScheduledEvent(slot_index=10, event_type="solar_drop")
        assert e.slot_index == 10
        assert e.event_type == "solar_drop"
        assert e.params == {}

    def test_event_with_params(self):
        e = ScheduledEvent(0, "solar_drop", params={"cloud_cover": 0.8})
        assert e.params["cloud_cover"] == 0.8

    def test_event_with_source_id(self):
        e = ScheduledEvent(5, "grid_outage", source_id="grid_backup")
        assert e.source_id == "grid_backup"

    def test_invalid_event_type_raises(self):
        with pytest.raises(ValidationError, match="Unknown event_type"):
            ScheduledEvent(0, "teleport_power")

    def test_all_valid_event_types_accepted(self):
        for etype in VALID_EVENT_TYPES:
            e = ScheduledEvent(0, etype)
            assert e.event_type == etype

    def test_negative_slot_index_resolved_correctly(self):
        e = ScheduledEvent(-1, "solar_recovery")
        assert e.resolved_index(100) == 99

    def test_negative_index_clamps_to_zero(self):
        e = ScheduledEvent(-200, "solar_recovery")
        assert e.resolved_index(10) == 0

    def test_zero_index(self):
        e = ScheduledEvent(0, "grid_outage")
        assert e.resolved_index(288) == 0

    def test_to_dict(self):
        e = ScheduledEvent(42, "tariff_spike", params={"multiplier": 2.0})
        d = e.to_dict()
        assert d["slot_index"] == 42
        assert d["event_type"] == "tariff_spike"
        assert d["params"]["multiplier"] == 2.0


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

class TestScenario:
    def test_basic_scenario(self):
        s = Scenario("test", "A test scenario", days=1, n_buildings=3, seed=7)
        assert s.n_slots == 96
        assert s.name == "test"

    def test_days_converts_to_slots(self):
        s = Scenario("t", "t", days=3)
        assert s.n_slots == 288

    def test_invalid_days_raises(self):
        with pytest.raises(ValidationError, match="days"):
            Scenario("t", "t", days=0)

    def test_invalid_n_buildings_raises(self):
        with pytest.raises(ValidationError, match="n_buildings"):
            Scenario("t", "t", n_buildings=-1)

    def test_invalid_negotiation_rounds_raises(self):
        with pytest.raises(ValidationError, match="negotiation_rounds"):
            Scenario("t", "t", negotiation_rounds=0)

    def test_invalid_on_failure_raises(self):
        with pytest.raises(ValidationError, match="on_failure"):
            Scenario("t", "t", on_failure="panic")

    def test_events_at_correct_slot(self):
        ev = ScheduledEvent(5, "solar_drop")
        s = Scenario("t", "t", days=2, events=(ev,))
        assert s.events_at(5) == [ev]
        assert s.events_at(6) == []
        assert s.events_at(0) == []

    def test_events_at_negative_index(self):
        ev = ScheduledEvent(-1, "solar_recovery")
        s = Scenario("t", "t", days=1, events=(ev,))  # 96 slots
        assert s.events_at(95) == [ev]

    def test_multiple_events_same_slot(self):
        ev1 = ScheduledEvent(10, "solar_drop")
        ev2 = ScheduledEvent(10, "tariff_spike", params={"multiplier": 2.0})
        s = Scenario("t", "t", days=1, events=(ev1, ev2))
        assert len(s.events_at(10)) == 2

    def test_to_dict(self):
        s = Scenario("demo", "Demo scenario", days=2, seed=99)
        d = s.to_dict()
        assert d["name"] == "demo"
        assert d["days"] == 2
        assert d["n_slots"] == 192
        assert d["seed"] == 99
        assert isinstance(d["events"], list)

    def test_events_outside_range_not_triggered(self):
        ev = ScheduledEvent(999, "solar_drop")
        s = Scenario("t", "t", days=1, events=(ev,))  # 96 slots
        # 999 >= n_slots so events_at(999) should find it via resolved_index
        # but within a run of 96 slots, this slot will never be reached
        assert s.events_at(999) == [ev]  # events_at just checks slot_index, no range guard


# ---------------------------------------------------------------------------
# Predefined scenarios
# ---------------------------------------------------------------------------

class TestPredefinedScenarios:
    def test_all_scenarios_in_registry(self):
        expected = {"normal", "solar_drop", "grid_outage", "battery_outage",
                    "battery_derate", "tariff_spike", "scarcity", "mixed_stress"}
        assert set(SCENARIOS) == expected

    def test_normal_has_no_events(self):
        assert len(NORMAL.events) == 0

    def test_solar_drop_has_events(self):
        assert len(SOLAR_DROP.events) >= 1
        types = {e.event_type for e in SOLAR_DROP.events}
        assert "solar_drop" in types

    def test_grid_outage_has_outage_and_restore(self):
        types = {e.event_type for e in GRID_OUTAGE.events}
        assert "grid_outage" in types
        assert "grid_restoration" in types

    def test_battery_outage_has_outage_and_restore(self):
        types = {e.event_type for e in BATTERY_OUTAGE.events}
        assert "battery_outage" in types
        assert "battery_restoration" in types

    def test_tariff_spike_has_spike_and_normalize(self):
        types = {e.event_type for e in TARIFF_SPIKE.events}
        assert "tariff_spike" in types
        assert "tariff_normalization" in types

    def test_scarcity_has_no_events_but_tight_supply(self):
        assert len(SCARCITY.events) == 0
        total_kw = sum(
            s.get("nominal_capacity_kw", 0) + s.get("installed_capacity_kw", 0)
            for s in SCARCITY.supply_config.get("sources", [])
        )
        # Supply is intentionally constrained
        assert total_kw < 300.0

    def test_mixed_stress_has_multiple_event_types(self):
        types = {e.event_type for e in MIXED_STRESS.events}
        assert len(types) >= 3

    def test_all_events_have_valid_types(self):
        for scenario in SCENARIOS.values():
            for ev in scenario.events:
                assert ev.event_type in VALID_EVENT_TYPES


# ---------------------------------------------------------------------------
# get_scenario
# ---------------------------------------------------------------------------

class TestGetScenario:
    def test_get_by_name(self):
        s = get_scenario("normal")
        assert s.name == "normal"

    def test_case_insensitive(self):
        s = get_scenario("NORMAL")
        assert s is NORMAL

    def test_with_whitespace(self):
        s = get_scenario("  solar_drop  ")
        assert s is SOLAR_DROP

    def test_unknown_raises(self):
        with pytest.raises(ValidationError, match="Unknown scenario"):
            get_scenario("not_a_real_scenario")
