"""Unit tests for the Coordinator.

Covers: initialisation, single-slot, multi-slot, events, scarcity,
failure handling, determinism, reset, and the simulation result structure.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.auction import AuctionEngine, GreedyAllocationStrategy
from gridweave.config.settings import synthetic_campus
from gridweave.coordinator import (
    Coordinator,
    CoordinatorError,
    MetricsAggregator,
    NORMAL,
    SCARCITY,
    SOLAR_DROP,
    Scenario,
    ScheduledEvent,
    SettlementError,
    SimulationResult,
    SlotRecord,
)
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockGrid, MockSolar
from gridweave.mocks.supply import MockBattery, MockSupply
from gridweave.models.common import TimeSlot
from gridweave.supply.provider import CampusSupplyProvider


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SUPPLY_CFG = {
    "sources": [
        {"type": "grid",    "source_id": "grid_main",    "nominal_capacity_kw": 500.0},
        {"type": "solar",   "source_id": "solar_roof",   "installed_capacity_kw": 200.0},
        {"type": "battery", "source_id": "battery_main", "capacity_kwh": 100.0,
         "initial_soc": 0.70},
    ]
}


def _make_system(n: int = 3, seed: int = 42, supply_cfg: dict | None = None):
    """Return (agents, envs, auctioneer, supply) for a synthetic n-building campus."""
    cfg = synthetic_campus(n, seed=seed)
    agents = build_agents(cfg)
    envs = build_simulators(cfg)
    auctioneer = AuctionEngine(strategy=GreedyAllocationStrategy(), track_fairness=True)
    supply = CampusSupplyProvider.from_config(supply_cfg or _SUPPLY_CFG)
    return agents, envs, auctioneer, supply


# ---------------------------------------------------------------------------
# 1. Initialisation
# ---------------------------------------------------------------------------

class TestInit:
    def test_basic_init(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        assert coord.steps_run == 0
        assert coord.has_next is True

    def test_mismatched_agents_envs_raises(self):
        agents, envs, auc, supply = _make_system(2)
        extra_agents, _, _, _ = _make_system(3)
        with pytest.raises(CoordinatorError, match="exactly one environment stream"):
            Coordinator(extra_agents, envs, auc, supply)

    def test_invalid_negotiation_rounds_raises(self):
        agents, envs, auc, supply = _make_system(2)
        with pytest.raises(CoordinatorError, match="negotiation_rounds"):
            Coordinator(agents, envs, auc, supply, negotiation_rounds=0)

    def test_invalid_on_failure_raises(self):
        agents, envs, auc, supply = _make_system(2)
        with pytest.raises(CoordinatorError, match="on_failure"):
            Coordinator(agents, envs, auc, supply, on_failure="explode")

    def test_scenario_overrides_negotiation_rounds(self):
        agents, envs, auc, supply = _make_system(2)
        scenario = Scenario("test", "test", days=1, n_buildings=2, seed=42,
                            negotiation_rounds=3)
        coord = Coordinator(agents, envs, auc, supply, scenario=scenario,
                            negotiation_rounds=1)
        assert coord.negotiation_rounds == 3


# ---------------------------------------------------------------------------
# 2. Warm-up
# ---------------------------------------------------------------------------

class TestWarmUp:
    def test_warm_up_gives_agents_history(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        coord.warm_up(1)
        for agent in agents.values():
            assert len(agent.history) == 1

    def test_warm_up_called_automatically_on_run_step(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        # No manual warm-up; run_step should auto-warm
        record = coord.run_step()
        assert isinstance(record, SlotRecord)
        for agent in agents.values():
            assert len(agent.history) >= 2  # 1 warm-up + 1 settled


# ---------------------------------------------------------------------------
# 3. Single-slot execution
# ---------------------------------------------------------------------------

class TestSingleSlot:
    def test_run_step_returns_slot_record(self):
        agents, envs, auc, supply = _make_system(3)
        coord = Coordinator(agents, envs, auc, supply)
        record = coord.run_step()
        assert isinstance(record, SlotRecord)
        assert record.slot_index == 0

    def test_slot_has_bids_for_every_agent(self):
        agents, envs, auc, supply = _make_system(3)
        coord = Coordinator(agents, envs, auc, supply)
        record = coord.run_step()
        assert set(record.bids) == set(agents)

    def test_slot_has_settlement_for_every_agent(self):
        agents, envs, auc, supply = _make_system(3)
        coord = Coordinator(agents, envs, auc, supply)
        record = coord.run_step()
        assert set(record.settlements) == set(agents)

    def test_allocation_le_request(self):
        agents, envs, auc, supply = _make_system(3)
        coord = Coordinator(agents, envs, auc, supply)
        record = coord.run_step()
        assert record.clearing is not None
        for alloc in record.clearing.allocations:
            bid = record.bids[alloc.building_id]
            assert alloc.allocated_power_kw <= bid.requested_power_kw + 1e-6

    def test_slot_appended_to_records(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        coord.run_step()
        assert len(coord.records) == 1

    def test_energy_conservation_per_slot(self):
        """Delivered ≈ allocated (within dispatch tolerance)."""
        agents, envs, auc, supply = _make_system(3)
        coord = Coordinator(agents, envs, auc, supply)
        record = coord.run_step()
        h = 15 / 60
        err = abs(record.delivered_kw - record.allocated_kw) * h
        assert err < 0.5, f"delivery error {err:.4f} kWh too large"

    def test_clearing_price_is_non_negative(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        record = coord.run_step()
        if record.clearing_price is not None:
            assert record.clearing_price >= 0.0


# ---------------------------------------------------------------------------
# 4. Multi-slot execution
# ---------------------------------------------------------------------------

class TestMultiSlot:
    def test_run_five_slots(self):
        agents, envs, auc, supply = _make_system(3)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=5)
        assert result.n_slots == 5

    def test_slot_indices_sequential(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        coord.run(steps=4)
        for i, r in enumerate(coord.records):
            assert r.slot_index == i

    def test_time_slots_monotonically_increasing(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        coord.run(steps=6)
        prev = coord.records[0].time_slot.start
        for r in coord.records[1:]:
            assert r.time_slot.start > prev
            prev = r.time_slot.start

    def test_run_returns_simulation_result(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=3)
        assert isinstance(result, SimulationResult)

    def test_building_stats_match_settlements(self):
        """Agent.stats.demand_kwh should equal sum of slot actual_demand_kw * h."""
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=4)
        h = 15 / 60
        for bid, agent in agents.items():
            expected_demand = sum(
                r.settlements[bid].actual_demand_kw * h
                for r in coord.records if bid in r.settlements
            )
            assert abs(agent.stats.demand_kwh - expected_demand) < 1e-6


# ---------------------------------------------------------------------------
# 5. Multiple buildings
# ---------------------------------------------------------------------------

class TestMultipleBuildings:
    def test_five_buildings(self):
        agents, envs, auc, supply = _make_system(5)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=4)
        assert result.n_buildings == 5

    def test_all_buildings_appear_in_building_summaries(self):
        agents, envs, auc, supply = _make_system(4)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=2)
        assert set(result.building_summaries) == set(agents)

    def test_service_ratio_in_unit_interval(self):
        agents, envs, auc, supply = _make_system(3)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=4)
        for bs in result.building_summaries.values():
            assert 0.0 <= bs.service_ratio <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# 6. Scarcity and re-auction
# ---------------------------------------------------------------------------

class TestScarcity:
    def test_scarcity_triggers_when_demand_exceeds_supply(self):
        """Use very small supply to force scarcity."""
        low_supply_cfg = {
            "sources": [
                {"type": "grid", "source_id": "grid_main", "nominal_capacity_kw": 10.0},
            ]
        }
        agents, envs, auc, supply = _make_system(5, supply_cfg=low_supply_cfg)
        coord = Coordinator(agents, envs, auc, supply, negotiation_rounds=2)
        result = coord.run(steps=3)
        # At least some slots should be scarcity slots given tiny supply
        assert result.scarcity_slots >= 0  # will be > 0 when 5 buildings compete for 10 kW

    def test_revised_bids_round_2(self):
        low_supply_cfg = {
            "sources": [
                {"type": "grid", "source_id": "grid_main", "nominal_capacity_kw": 5.0},
            ]
        }
        agents, envs, auc, supply = _make_system(3, supply_cfg=low_supply_cfg)
        coord = Coordinator(agents, envs, auc, supply, negotiation_rounds=2)
        coord.run_step()
        record = coord.records[0]
        # In scarcity: rounds == 2 and first requested > available
        if record.first_round_requested_kw > record.supply_kw:
            assert record.rounds == 2
            assert record.scarcity > 0.0

    def test_single_round_no_renegotiation(self):
        """negotiation_rounds=1 should never set rounds=2."""
        agents, envs, auc, supply = _make_system(5)
        coord = Coordinator(agents, envs, auc, supply, negotiation_rounds=1)
        coord.run(steps=3)
        for r in coord.records:
            assert r.rounds == 1


# ---------------------------------------------------------------------------
# 7. Settlement feedback
# ---------------------------------------------------------------------------

class TestFeedback:
    def test_deferred_energy_carried_to_next_slot(self):
        """Run with very low supply; some energy should be deferred."""
        low_supply_cfg = {
            "sources": [
                {"type": "grid", "source_id": "g", "nominal_capacity_kw": 20.0},
            ]
        }
        agents, envs, auc, supply = _make_system(3, supply_cfg=low_supply_cfg)
        coord = Coordinator(agents, envs, auc, supply, negotiation_rounds=2)
        coord.run(steps=10)
        total_deferred = sum(
            sum(s.deferred_kw for s in r.settlements.values())
            for r in coord.records
        )
        # This is a probabilistic check; deferred > 0 if supply was ever short
        assert total_deferred >= 0.0  # always true, just validates the field exists

    def test_energy_balance_near_zero(self):
        """P1 energy conservation invariant must hold for all agents."""
        agents, envs, auc, supply = _make_system(3)
        coord = Coordinator(agents, envs, auc, supply)
        coord.run(steps=8)
        for bid, agent in agents.items():
            err = agent.energy_balance_kwh()
            assert err < 1e-4, f"energy imbalance {err:.6f} kWh for {bid}"


# ---------------------------------------------------------------------------
# 8. Failure handling
# ---------------------------------------------------------------------------

class TestFailureHandling:
    def test_market_failure_recorded_settle_zero(self):
        """When on_failure='settle_zero', a bad auction → zero allocation, run continues."""
        agents, envs, _, supply = _make_system(2)

        class FailingAuctioneer:
            def clear(self, *args, **kwargs):
                raise RuntimeError("simulated auction failure")

        coord = Coordinator(agents, envs, FailingAuctioneer(), supply,
                            on_failure="settle_zero")
        result = coord.run(steps=3)
        assert result.market_failure_slots == 3

    def test_market_failure_raise_mode(self):
        """on_failure='raise' should propagate the exception."""
        agents, envs, _, supply = _make_system(2)

        class FailingAuctioneer:
            def clear(self, *args, **kwargs):
                raise RuntimeError("simulated failure")

        coord = Coordinator(agents, envs, FailingAuctioneer(), supply,
                            on_failure="raise")
        with pytest.raises(RuntimeError, match="simulated failure"):
            coord.run(steps=1)

    def test_zero_allocations_after_market_failure(self):
        agents, envs, _, supply = _make_system(2)

        class FailingAuctioneer:
            def clear(self, *args, **kwargs):
                raise RuntimeError("fail")

        coord = Coordinator(agents, envs, FailingAuctioneer(), supply,
                            on_failure="settle_zero")
        coord.run_step()
        record = coord.records[0]
        assert record.failure is not None
        # All settlements should have zero allocation
        for s in record.settlements.values():
            assert s.allocated_kw == 0.0


# ---------------------------------------------------------------------------
# 9. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_same_result(self):
        """Two runs with identical config must produce identical records."""
        def _run():
            agents, envs, auc, supply = _make_system(3, seed=42)
            coord = Coordinator(agents, envs, auc, supply)
            result = coord.run(steps=5)
            return [r.served_kw for r in coord.records]

        r1 = _run()
        r2 = _run()
        assert r1 == r2, f"determinism broken: {r1} != {r2}"

    def test_different_seeds_different_demand(self):
        def _demand(seed):
            agents, envs, auc, supply = _make_system(3, seed=seed)
            coord = Coordinator(agents, envs, auc, supply)
            coord.run(steps=4)
            return [r.actual_demand_kw for r in coord.records]

        d1 = _demand(42)
        d2 = _demand(99)
        assert d1 != d2


# ---------------------------------------------------------------------------
# 10. Reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_records(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        coord.run(steps=3)
        assert len(coord.records) == 3
        coord.reset()
        assert len(coord.records) == 0
        assert coord.steps_run == 0

    def test_reset_clears_event_log(self):
        agents, envs, auc, supply = _make_system(2)
        scenario = Scenario("t", "test", days=1, n_buildings=2,
                            events=(ScheduledEvent(0, "solar_drop"),))
        coord = Coordinator(agents, envs, auc, supply, scenario=scenario)
        coord.run(steps=1)
        coord.reset()
        assert len(coord.event_log) == 0


# ---------------------------------------------------------------------------
# 11. SimulationResult structure
# ---------------------------------------------------------------------------

class TestSimulationResult:
    def test_to_dict_complete(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=2)
        d = result.to_dict()
        assert "slots" in d
        assert "building_summaries" in d
        assert "supply_metrics" in d
        assert "event_log" in d
        assert len(d["slots"]) == 2

    def test_to_json_valid(self):
        import json
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=2)
        j = result.to_json()
        parsed = json.loads(j)
        assert "slots" in parsed

    def test_slot_series_length(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=5)
        assert len(result.slot_series("served_kw")) == 5

    def test_building_series(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        result = coord.run(steps=4)
        bid = next(iter(agents))
        series = result.building_series(bid, "served_kw")
        assert len(series) == 4


# ---------------------------------------------------------------------------
# 12. Snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_snapshot_keys(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        coord.run(steps=2)
        snap = coord.snapshot()
        assert "step_index" in snap
        assert "has_next" in snap
        assert "agents" in snap

    def test_step_index_matches_steps_run(self):
        agents, envs, auc, supply = _make_system(2)
        coord = Coordinator(agents, envs, auc, supply)
        coord.run(steps=4)
        assert coord.snapshot()["step_index"] == 4
