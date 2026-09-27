"""End-to-end closed loop:
observe -> forecast -> bid -> [revised bid] -> clearing -> dispatch -> realised demand -> settlement -> next state.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest

from gridweave.config import load_campus_config, synthetic_campus
from gridweave.contracts import validate_clearing
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockCoordinator, MockGrid, MockSupply, summarise
from gridweave.models import AllocationStatus


def run_campus(cfg, supply, steps=None, periods=None):
    agents = build_agents(cfg)
    sims = build_simulators(cfg, periods)
    coord = MockCoordinator(agents, sims, MockAuctioneer(), supply,
                            resolution_minutes=cfg.simulation.resolution_minutes)
    coord.run(steps)
    return agents, sims, coord


def assert_step_invariants(record):
    assert record.failure is None
    validate_clearing(record.clearing, list(record.bids.values()), record.offers)
    assert record.served_kw <= record.supply_kw + 1e-6
    for building_id, bid in record.bids.items():
        s = record.settlements[building_id]
        assert s.bid_id == bid.bid_id and s.time_slot == bid.time_slot == record.time_slot
        assert 0 <= bid.critical_power_kw <= bid.minimum_power_kw <= bid.requested_power_kw <= bid.capacity_kw
        assert s.served_kw <= s.actual_total_kw + 1e-9 and s.served_kw <= s.allocated_kw + 1e-9
        assert min(s.deferred_kw, s.curtailed_kw, s.critical_shortfall_kw) >= 0


def test_single_cycle_walkthrough():
    agents, _, coord = run_campus(load_campus_config(), MockSupply(MockGrid(1000)), steps=1)
    record = coord.records[0]
    assert set(record.bids) == set(record.settlements) == set(agents)
    assert all(a.phase.value == "settled" for a in agents.values())
    assert_step_invariants(record)


def test_default_campus_three_days_closed_loop():
    cfg = load_campus_config()
    agents, _, coord = run_campus(cfg, MockSupply.from_config(cfg.supply))
    assert len(coord.records) == cfg.simulation.periods - 1          # first slot is warm-up only
    for r in coord.records:
        assert_step_invariants(r)
    for agent in agents.values():                                   # every kWh accounted for exactly once
        assert agent.energy_balance_kwh() == pytest.approx(0, abs=1e-6)
    summary = summarise(coord.records, agents)
    assert summary["shortage_steps"] > 0                             # the scenario stresses the system
    # critical shortfalls are measured against REALISED demand and reported, not assumed away
    reported = sum(s.has_critical_shortfall for r in coord.records for s in r.settlements.values())
    assert summary["critical_shortfall_events"] == reported
    lab, hostel = summary["buildings"]["eng_lab"], summary["buildings"]["hostel_a"]
    assert lab["service_ratio"] > hostel["service_ratio"]
    assert hostel["deferred_kwh"] > 0 and hostel["deferred_kwh"] < hostel["demand_kwh"]
    json.dumps([r.to_dict() for r in coord.records[:5]])


def test_revised_bids_actually_reduce_requested_power():
    cfg = load_campus_config()
    _, _, coord = run_campus(cfg, MockSupply.from_config(cfg.supply))
    revised = [r for r in coord.records if r.rounds > 1]
    assert revised
    for r in revised:
        assert r.requested_kw < r.first_round_requested_kw           # demand response, not just a price change
        for b in r.bids.values():
            assert b.revision == 1 and b.voluntary_reduction_kw >= 0


def test_forecast_error_reaches_outcomes():
    cfg = load_campus_config()
    _, _, coord = run_campus(cfg, MockSupply(MockGrid(10_000)))       # unlimited supply
    settlements = [s for r in coord.records for s in r.settlements.values()]
    under = [s for s in settlements if s.forecast_error_kw < -1.0]
    over = [s for s in settlements if s.forecast_error_kw > 1.0]
    assert under and over
    # With unlimited supply, the only reason for unserved need is under-forecasting. Usually the
    # slot is short (not FULL); when the building is at its capacity cap, the extra new demand
    # displaces deferred backlog instead, so something that was waiting is still not served.
    for s in under:
        assert s.served_kw < s.actual_demand_kw + s.backlog_available_kw - 1e-6
    assert sum(s.status is not AllocationStatus.FULL for s in under) > 0.9 * len(under)
    assert all(s.unused_allocation_kw > 0 for s in over)


def test_allocation_changes_future_state():
    cfg = load_campus_config()
    _, _, rich = run_campus(cfg, MockSupply(MockGrid(10_000)), steps=90)
    _, _, poor = run_campus(cfg, MockSupply(MockGrid(150)), steps=90)
    k = 89
    rich_bid, poor_bid = rich.records[k].bids["hostel_a"], poor.records[k].bids["hostel_a"]
    assert poor_bid.explanation["demand"]["backlog_kw"] > rich_bid.explanation["demand"]["backlog_kw"]
    assert poor_bid.priority_score > rich_bid.priority_score           # deprivation state raised priority


def test_deferred_load_enters_future_demand():
    cfg = load_campus_config()
    _, _, coord = run_campus(cfg, MockSupply.from_config(cfg.supply))
    settlements = [s for r in coord.records for s in r.settlements.values()]
    assert any(s.backlog_attempted_kw > 0 and s.backlog_served_kw > 0 for s in settlements)
    assert any(s.actual_total_kw > s.actual_demand_kw for s in settlements)


def test_rebound_makes_environment_depend_on_curtailment():
    base = load_campus_config()
    no_rebound = replace(base, simulation=replace(base.simulation, rebound_fraction=0.0))
    rebound = replace(base, simulation=replace(base.simulation, rebound_fraction=1.0))
    supply = lambda: MockSupply(MockGrid(200))  # noqa: E731
    _, s0, _ = run_campus(no_rebound, supply(), steps=96)
    _, s1, _ = run_campus(rebound, supply(), steps=96)
    real0 = [s.actual_demand_kw for s in s0["hostel_a"].settlements]
    real1 = [s.actual_demand_kw for s in s1["hostel_a"].settlements]
    assert sum(real1) > sum(real0)                                    # curtailed load came back as demand


def test_battery_state_changes_through_dispatch():
    cfg = load_campus_config()
    _, _, coord = run_campus(cfg, MockSupply.from_config(cfg.supply), steps=96)
    socs = [r.state["soc"] for rec in coord.records for r in rec.dispatch_results if r.source_id == "battery"]
    assert socs and socs == sorted(socs, reverse=True) and socs[-1] < 0.9


def test_extreme_shortage_reports_critical_shortfalls_without_crashing():
    cfg = load_campus_config()
    agents, _, coord = run_campus(cfg, MockSupply(MockGrid(30)), steps=96)
    summary = summarise(coord.records, agents)
    assert summary["critical_shortfall_events"] > 0
    for r in coord.records:
        assert_step_invariants(r)
    deprivation = [r.bids["hostel_a"].explanation["priority"]["factors"]["deprivation"] for r in coord.records]
    assert deprivation[-1] > deprivation[0]


def test_runs_are_reproducible():
    cfg = load_campus_config()
    _, _, a = run_campus(cfg, MockSupply.from_config(cfg.supply), steps=40)
    _, _, b = run_campus(cfg, MockSupply.from_config(cfg.supply), steps=40)
    assert [r.to_dict() for r in a.records] == [r.to_dict() for r in b.records]
    _, _, c = run_campus(cfg.with_seed(1), MockSupply.from_config(cfg.supply), steps=40)
    assert [r.to_dict() for r in a.records] != [r.to_dict() for r in c.records]


@pytest.mark.parametrize("n", [10, 50, 100])
def test_runs_with_many_buildings(n):
    cfg = synthetic_campus(n)
    start = time.perf_counter()
    agents, _, coord = run_campus(cfg, MockSupply(MockGrid(cfg.total_capacity_kw * 0.5)), steps=8, periods=9)
    assert len(agents) == n and len(coord.records) == 8
    for r in coord.records:
        assert_step_invariants(r)
    assert time.perf_counter() - start < 30
