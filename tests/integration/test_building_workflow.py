"""End-to-end: observe -> forecast -> bid -> mock auction -> allocation -> state update."""
from __future__ import annotations

import json
import time

import pytest

from gridweave.config import load_campus_config, synthetic_campus
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockCoordinator, MockGrid, summarise
from gridweave.models import AllocationStatus


def run_campus(cfg, capacity_kw, windows=(), steps=None, periods=None):
    agents = build_agents(cfg)
    sims = build_simulators(cfg, periods)
    coord = MockCoordinator(agents, sims, MockAuctioneer(), MockGrid(capacity_kw, windows),
                            resolution_minutes=cfg.simulation.resolution_minutes)
    coord.run(steps)
    return agents, coord


def assert_step_invariants(record):
    assert record.served_kw <= record.supply_kw + 1e-6
    for building_id, bid in record.bids.items():
        o = record.outcomes[building_id]
        assert o.bid_id == bid.bid_id
        assert 0 <= bid.critical_power_kw <= bid.minimum_power_kw <= bid.requested_power_kw
        assert 0 <= bid.priority_score <= 1
        assert o.accepted_kw <= bid.requested_power_kw + 1e-9
        assert o.flexible_deferred_kw >= 0 and o.flexible_curtailed_kw >= 0


def test_single_cycle_walkthrough():
    agents, coord = run_campus(load_campus_config(), 1000, steps=1)
    record = coord.records[0]
    assert set(record.bids) == set(agents)
    assert all(o.status is AllocationStatus.FULL for o in record.outcomes.values())
    assert all(a.phase.value == "settled" for a in agents.values())
    assert_step_invariants(record)


def test_default_campus_three_days_with_evening_shortage():
    cfg = load_campus_config()
    agents, coord = run_campus(cfg, cfg.supply["mock_grid_capacity_kw"], cfg.supply["shortage_windows"])
    assert len(coord.records) == cfg.simulation.periods
    for r in coord.records:
        assert_step_invariants(r)
    summary = summarise(coord.records, agents)
    assert summary["shortage_steps"] > 0                      # the scenario actually stresses the system
    assert summary["critical_shortfall_events"] == 0          # ...yet critical load is always protected
    assert summary["renegotiated_steps"] == summary["shortage_steps"]
    lab, hostel = summary["buildings"]["eng_lab"], summary["buildings"]["hostel_a"]
    assert lab["service_ratio"] > hostel["service_ratio"]     # priority matters
    assert hostel["deferred_kwh"] > 0                         # demand response happened
    json.dumps([r.to_dict() for r in coord.records[:5]])      # records are serialisable


def test_extreme_shortage_reports_critical_shortfalls_without_crashing():
    cfg = load_campus_config()
    agents, coord = run_campus(cfg, 30, steps=96)
    summary = summarise(coord.records, agents)
    assert summary["critical_shortfall_events"] > 0
    for r in coord.records:
        assert_step_invariants(r)
    # sustained deprivation pushes priority up over time
    first, last = coord.records[0].bids["hostel_a"], coord.records[-1].bids["hostel_a"]
    assert last.explanation["priority"]["factors"]["deprivation"] > first.explanation["priority"]["factors"]["deprivation"]


def test_runs_are_reproducible():
    cfg = load_campus_config()
    _, a = run_campus(cfg, 380, steps=40)
    _, b = run_campus(cfg, 380, steps=40)
    assert [r.to_dict() for r in a.records] == [r.to_dict() for r in b.records]
    _, c = run_campus(cfg.with_seed(1), 380, steps=40)
    assert [r.to_dict() for r in a.records] != [r.to_dict() for r in c.records]


@pytest.mark.parametrize("n", [10, 50, 100])
def test_scales_to_many_buildings(n):
    cfg = synthetic_campus(n)
    start = time.perf_counter()
    agents, coord = run_campus(cfg, cfg.total_capacity_kw * 0.5, steps=8, periods=8)
    assert len(agents) == n and len(coord.records) == 8
    for r in coord.records:
        assert_step_invariants(r)
    assert time.perf_counter() - start < 30
