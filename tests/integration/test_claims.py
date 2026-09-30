"""Claims made in docs/DESIGN.md, each proved by one named test (the testing table points here).

Other claims in the table are proved by tests elsewhere; the table names each test file.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from gridweave.coordinator import MetricsAggregator, get_scenario, run_simulation


def _metrics(scenario, mode="gridweave", **kw):
    out = run_simulation(scenario, mode, **kw)
    return MetricsAggregator.compute(out.result, out.agents), out


def _without_events(name):
    return replace(get_scenario(name), name=f"{name}_no_events", events=())


@pytest.mark.parametrize(
    "scenario, check",
    [
        ("solar_drop", lambda ev, base: ev.total_served_kwh < base.total_served_kwh),
        ("battery_outage", lambda ev, base: ev.total_battery_discharge_kwh < base.total_battery_discharge_kwh),
        ("battery_derate", lambda ev, base: ev.total_battery_discharge_kwh < base.total_battery_discharge_kwh),
        ("tariff_spike", lambda ev, base: ev.total_procurement_cost > base.total_procurement_cost),
        ("grid_outage", lambda ev, base: ev.total_critical_shortfall_kwh > base.total_critical_shortfall_kwh),
        ("mixed_stress", lambda ev, base: ev.total_critical_shortfall_kwh > base.total_critical_shortfall_kwh),
    ],
)
def test_every_supply_event_changes_the_outcome(scenario, check):
    """C3: each scenario's event has a measurable effect vs the same days without it."""
    with_event, _ = _metrics(scenario)
    without, _ = _metrics(_without_events(scenario))
    assert check(with_event, without)


def test_daytime_grid_outage_leaves_supply_between_critical_and_total_need():
    """C8: the outage creates the regime where allocation choices matter: supply is above the
    campus's critical need (so critical load can be protected) but below its total need."""
    _, out = _metrics("grid_outage", steps=96)
    window = [r for r in out.result.slots if 44 <= r.slot_index < 64]
    in_band = [r for r in window
               if sum(s.actual_critical_kw for s in r.settlements.values()) < r.supply_kw
               < sum(s.actual_total_kw for s in r.settlements.values())]
    assert len(in_band) >= 0.75 * len(window)


def test_scarcer_supply_never_reduces_critical_shortfall_and_critical_first_always_helps():
    """C15: along a supply sweep, critical shortfall rises as supply shrinks, and protecting
    critical load beats an equal share at every level."""
    base = get_scenario("normal")
    shortfall = {}
    for grid_kw in (200, 125, 50):
        supply = json.loads(json.dumps(base.supply_config))
        supply["sources"][0]["nominal_capacity_kw"] = float(grid_kw)
        sc = replace(base, name=f"grid_{grid_kw}", days=1, supply_config=supply)
        for mode in ("equal_share", "critical_first"):
            shortfall[(mode, grid_kw)] = _metrics(sc, mode)[0].total_critical_shortfall_kwh
    for mode in ("equal_share", "critical_first"):
        assert shortfall[(mode, 200)] <= shortfall[(mode, 125)] <= shortfall[(mode, 50)]
    for grid_kw in (125, 50):
        assert shortfall[("critical_first", grid_kw)] < shortfall[("equal_share", grid_kw)]
