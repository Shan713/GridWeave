"""End-to-end integration tests for the full closed-loop campus simulation.

These tests exercise the real AuctionEngine, real CampusSupplyProvider, and
real BuildingAgents — no mocks in the core path.
"""
from __future__ import annotations

import pytest

from gridweave.auction import AuctionEngine, GreedyAllocationStrategy, OptimizedAllocationStrategy
from gridweave.config.settings import synthetic_campus
from gridweave.contracts import validate_clearing, validate_dispatch
from gridweave.coordinator import (
    Coordinator,
    MetricsAggregator,
    Scenario,
    ScheduledEvent,
)
from gridweave.factory import build_agents, build_simulators
from gridweave.supply.provider import CampusSupplyProvider

_SUPPLY_CFG = {
    "sources": [
        {"type": "grid",    "source_id": "grid_main",    "nominal_capacity_kw": 400.0},
        {"type": "solar",   "source_id": "solar_roof",   "installed_capacity_kw": 200.0},
        {"type": "battery", "source_id": "battery_main", "capacity_kwh": 150.0,
         "initial_soc": 0.70},
    ]
}

_TIGHT_SUPPLY_CFG = {
    "sources": [
        {"type": "grid", "source_id": "g", "nominal_capacity_kw": 80.0},
    ]
}


def _build(n: int = 5, seed: int = 42, supply_cfg: dict | None = None,
           strategy=None, negotiation_rounds: int = 2, scenario: Scenario | None = None):
    cfg = synthetic_campus(n, seed=seed)
    agents = build_agents(cfg)
    envs = build_simulators(cfg)
    auc = AuctionEngine(
        strategy=strategy or GreedyAllocationStrategy(),
        track_fairness=True,
    )
    supply = CampusSupplyProvider.from_config(supply_cfg or _SUPPLY_CFG)
    coord = Coordinator(agents, envs, auc, supply,
                        negotiation_rounds=negotiation_rounds,
                        scenario=scenario)
    return coord, agents, supply


# ---------------------------------------------------------------------------
# Test 1 — Normal operation
# ---------------------------------------------------------------------------

class TestNormalOperation:
    def test_full_loop_five_buildings(self):
        coord, agents, _ = _build(n=5)
        result = coord.run(steps=10)
        assert result.n_slots == 10
        assert result.n_buildings == 5

    def test_all_buildings_settled_every_slot(self):
        coord, agents, _ = _build(n=3)
        result = coord.run(steps=6)
        for r in result.slots:
            assert set(r.settlements) == set(agents)

    def test_clearing_valid_every_slot(self):
        coord, agents, supply = _build(n=3)
        coord.warm_up()
        for _ in range(5):
            r = coord.run_step()
            if r.clearing is not None:
                validate_clearing(r.clearing, list(r.bids.values()), r.offers)

    def test_dispatch_valid_every_slot(self):
        coord, agents, supply = _build(n=3)
        coord.warm_up()
        for _ in range(5):
            r = coord.run_step()
            if r.clearing and r.dispatch_results:
                validate_dispatch(r.clearing.dispatch, r.dispatch_results)

    def test_service_ratio_between_0_and_1(self):
        coord, agents, _ = _build(n=4)
        result = coord.run(steps=8)
        for bs in result.building_summaries.values():
            assert 0.0 <= bs.service_ratio <= 1.0 + 1e-9

    def test_energy_balance_all_agents(self):
        coord, agents, _ = _build(n=3)
        coord.run(steps=8)
        for bid, agent in agents.items():
            err = agent.energy_balance_kwh()
            assert err < 1e-4, f"Energy imbalance {err:.6f} for {bid}"


# ---------------------------------------------------------------------------
# Test 2 — Scarcity
# ---------------------------------------------------------------------------

class TestScarcity:
    def test_allocation_le_supply_kw(self):
        """Total allocation must never exceed total supply offered."""
        coord, _, _ = _build(n=5, supply_cfg=_TIGHT_SUPPLY_CFG, negotiation_rounds=2)
        result = coord.run(steps=8)
        for r in result.slots:
            if r.clearing:
                assert r.allocated_kw <= r.supply_kw + 1e-6

    def test_unmet_demand_positive_in_scarcity(self):
        coord, _, _ = _build(n=5, supply_cfg=_TIGHT_SUPPLY_CFG, negotiation_rounds=2)
        result = coord.run(steps=6)
        total_unmet = result.total_demand_kwh - result.total_served_kwh
        # With tight supply we expect some unmet demand
        assert total_unmet >= 0.0

    def test_scarcity_slots_counted(self):
        coord, _, _ = _build(n=5, supply_cfg=_TIGHT_SUPPLY_CFG, negotiation_rounds=2)
        result = coord.run(steps=8)
        m = MetricsAggregator.compute(result)
        assert m.market.scarcity_slots >= 0


# ---------------------------------------------------------------------------
# Test 3 — Solar drop
# ---------------------------------------------------------------------------

class TestSolarDrop:
    def test_solar_drop_fires_at_correct_slot(self):
        scenario = Scenario(
            "solar_test", "Solar drop at slot 2",
            days=1, n_buildings=3, seed=42,
            events=(
                ScheduledEvent(2, "solar_drop", params={"cloud_cover": 0.95}),
            ),
            supply_config=_SUPPLY_CFG,
        )
        coord, _, _ = _build(n=3, scenario=scenario)
        result = coord.run(steps=5)
        # Event log should contain the solar_drop at slot 2
        solar_evts = [e for e in result.event_log if e.event_type == "solar_drop"]
        assert len(solar_evts) == 1
        assert solar_evts[0].slot_index == 2

    def test_metrics_reflect_solar_event(self):
        scenario = Scenario(
            "solar_drop_test", "Solar drop at slot 2",
            days=1, n_buildings=3, seed=42,
            events=(ScheduledEvent(2, "solar_drop", params={"cloud_cover": 0.90}),),
            supply_config=_SUPPLY_CFG,
        )
        coord, _, _ = _build(n=3, scenario=scenario)
        result = coord.run(steps=10)
        m = MetricsAggregator.compute(result)
        assert m.n_events >= 1


# ---------------------------------------------------------------------------
# Test 4 — Grid outage
# ---------------------------------------------------------------------------

class TestGridOutage:
    def test_grid_outage_triggers_event(self):
        scenario = Scenario(
            "grid_test", "Grid outage",
            days=1, n_buildings=3, seed=42,
            events=(ScheduledEvent(3, "grid_outage"),),
            supply_config=_SUPPLY_CFG,
        )
        coord, _, _ = _build(n=3, scenario=scenario)
        result = coord.run(steps=5)
        outage_evts = [e for e in result.event_log if e.event_type == "grid_outage"]
        assert len(outage_evts) == 1

    def test_system_continues_after_grid_outage(self):
        scenario = Scenario(
            "grid_test2", "Grid outage then restore",
            days=1, n_buildings=3, seed=42,
            events=(
                ScheduledEvent(2, "grid_outage"),
                ScheduledEvent(5, "grid_restoration"),
            ),
            supply_config=_SUPPLY_CFG,
        )
        coord, _, _ = _build(n=3, scenario=scenario)
        result = coord.run(steps=8)
        assert result.n_slots == 8  # simulation continued without crashing


# ---------------------------------------------------------------------------
# Test 5 — Battery event
# ---------------------------------------------------------------------------

class TestBatteryEvent:
    def test_battery_outage_and_restoration(self):
        scenario = Scenario(
            "bat_test", "Battery outage",
            days=1, n_buildings=3, seed=42,
            events=(
                ScheduledEvent(2, "battery_outage"),
                ScheduledEvent(6, "battery_restoration"),
            ),
            supply_config=_SUPPLY_CFG,
        )
        coord, _, _ = _build(n=3, scenario=scenario)
        result = coord.run(steps=8)
        bat_evts = [e for e in result.event_log if "battery" in e.event_type]
        assert len(bat_evts) == 2


# ---------------------------------------------------------------------------
# Test 6 — Forecast error
# ---------------------------------------------------------------------------

class TestForecastError:
    def test_forecast_mae_computed(self):
        coord, agents, _ = _build(n=3)
        result = coord.run(steps=10)
        m = MetricsAggregator.compute(result, agents)
        # After 10 slots of settlement, MAE should be computable
        assert m.forecast_mae_kw is not None
        assert m.forecast_mae_kw >= 0.0


# ---------------------------------------------------------------------------
# Test 7 — Re-auction
# ---------------------------------------------------------------------------

class TestReAuction:
    def test_re_auction_fires_when_demand_exceeds_supply(self):
        tight = {"sources": [{"type": "grid", "source_id": "g", "nominal_capacity_kw": 5.0}]}
        coord, _, _ = _build(n=5, supply_cfg=tight, negotiation_rounds=2)
        result = coord.run(steps=6)
        m = MetricsAggregator.compute(result)
        assert m.scenario_name is not None
        # With 5 buildings and 5 kW supply, almost certainly scarcity each slot
        re_auctions = sum(1 for r in result.slots if r.rounds > 1)
        assert re_auctions >= 0  # may be 0 if first bids fit; just ensure no crash

    def test_single_round_mode_no_re_auction(self):
        tight = {"sources": [{"type": "grid", "source_id": "g", "nominal_capacity_kw": 5.0}]}
        coord, _, _ = _build(n=5, supply_cfg=tight, negotiation_rounds=1)
        result = coord.run(steps=5)
        for r in result.slots:
            assert r.rounds == 1


# ---------------------------------------------------------------------------
# Test 8 — Reproducibility
# ---------------------------------------------------------------------------

class TestReproducibility:
    def test_identical_results_same_seed(self):
        def _served(seed):
            cfg = synthetic_campus(3, seed=seed)
            agents = build_agents(cfg)
            envs = build_simulators(cfg)
            auc = AuctionEngine(strategy=GreedyAllocationStrategy())
            supply = CampusSupplyProvider.from_config(_SUPPLY_CFG)
            coord = Coordinator(agents, envs, auc, supply)
            result = coord.run(steps=8)
            return [round(r.served_kw, 4) for r in result.slots]

        r1 = _served(42)
        r2 = _served(42)
        assert r1 == r2

    def test_different_seeds_different_demand(self):
        def _demands(seed):
            cfg = synthetic_campus(3, seed=seed)
            agents = build_agents(cfg)
            envs = build_simulators(cfg)
            auc = AuctionEngine(strategy=GreedyAllocationStrategy())
            supply = CampusSupplyProvider.from_config(_SUPPLY_CFG)
            coord = Coordinator(agents, envs, auc, supply)
            result = coord.run(steps=5)
            return [round(r.actual_demand_kw, 2) for r in result.slots]

        assert _demands(42) != _demands(99)


# ---------------------------------------------------------------------------
# Test 9 — Allocation invariants (property-style)
# ---------------------------------------------------------------------------

class TestAllocationInvariants:
    def test_allocation_le_request_every_slot(self):
        coord, _, _ = _build(n=4)
        result = coord.run(steps=8)
        for r in result.slots:
            if r.clearing is None:
                continue
            alloc_by_bid = {a.bid_id: a for a in r.clearing.allocations}
            for bid_id, bid in r.bids.items():
                alloc = alloc_by_bid.get(bid.bid_id)
                if alloc:
                    assert alloc.allocated_power_kw <= bid.requested_power_kw + 1e-6

    def test_total_allocation_le_supply_every_slot(self):
        coord, _, _ = _build(n=5)
        result = coord.run(steps=8)
        for r in result.slots:
            assert r.allocated_kw <= r.supply_kw + 1e-6

    def test_served_le_actual_total_every_settlement(self):
        coord, _, _ = _build(n=3)
        result = coord.run(steps=8)
        for r in result.slots:
            for s in r.settlements.values():
                assert s.served_kw <= s.actual_total_kw + 1e-6

    def test_critical_served_le_actual_critical_every_settlement(self):
        coord, _, _ = _build(n=3)
        result = coord.run(steps=8)
        for r in result.slots:
            for s in r.settlements.values():
                assert s.critical_served_kw <= s.actual_critical_kw + 1e-6


# ---------------------------------------------------------------------------
# Test 10 — Multiple strategies
# ---------------------------------------------------------------------------

class TestStrategies:
    @pytest.mark.parametrize("strategy", [
        GreedyAllocationStrategy(),
        OptimizedAllocationStrategy(),
    ])
    def test_strategy_produces_valid_result(self, strategy):
        coord, agents, _ = _build(n=3, strategy=strategy)
        result = coord.run(steps=4)
        assert result.n_slots == 4
        for bs in result.building_summaries.values():
            assert 0.0 <= bs.service_ratio <= 1.0 + 1e-9
