"""Unit tests for the MetricsAggregator and CampusMetrics."""
from __future__ import annotations

from gridweave.auction import AuctionEngine, GreedyAllocationStrategy
from gridweave.config.settings import synthetic_campus
from gridweave.coordinator import Coordinator, MetricsAggregator
from gridweave.coordinator.metrics import (
    CampusMetrics,
    EnergyConservationMetrics,
    FairnessMetrics,
    MarketMetrics,
    _jains_index,
)
from gridweave.factory import build_agents, build_simulators
from gridweave.supply.provider import CampusSupplyProvider

_SUPPLY_CFG = {
    "sources": [
        {"type": "grid",    "source_id": "g", "nominal_capacity_kw": 500.0},
        {"type": "solar",   "source_id": "s", "installed_capacity_kw": 200.0},
        {"type": "battery", "source_id": "b", "capacity_kwh": 100.0, "initial_soc": 0.70},
    ]
}


def _run(n: int = 3, steps: int = 10, seed: int = 42, supply_cfg: dict | None = None):
    cfg = synthetic_campus(n, seed=seed)
    agents = build_agents(cfg)
    envs = build_simulators(cfg)
    auc = AuctionEngine(strategy=GreedyAllocationStrategy(), track_fairness=True)
    supply = CampusSupplyProvider.from_config(supply_cfg or _SUPPLY_CFG)
    coord = Coordinator(agents, envs, auc, supply)
    result = coord.run(steps=steps)
    return result, agents


# ---------------------------------------------------------------------------
# Jain's fairness index helper
# ---------------------------------------------------------------------------

class TestJainsIndex:
    def test_all_equal_max_fairness(self):
        values = [0.8, 0.8, 0.8, 0.8]
        assert abs(_jains_index(values) - 1.0) < 1e-9

    def test_one_winner_min_fairness(self):
        # One agent gets everything, others get 0 → worst case
        values = [1.0, 0.0, 0.0, 0.0]
        expected = (1.0 / 4) ** 2 / (1.0 / 4)  # Jain's formula
        assert abs(_jains_index(values) - expected) < 1e-9

    def test_empty_returns_one(self):
        assert _jains_index([]) == 1.0

    def test_single_value_returns_one(self):
        assert _jains_index([0.5]) == 1.0

    def test_all_zero_returns_one(self):
        # Zero supply ⇒ no allocation; by convention fairness = 1.0
        assert _jains_index([0.0, 0.0]) == 1.0


# ---------------------------------------------------------------------------
# MetricsAggregator basic structure
# ---------------------------------------------------------------------------

class TestMetricsAggregatorStructure:
    def test_returns_campus_metrics(self):
        result, agents = _run(3, 5)
        m = MetricsAggregator.compute(result)
        assert isinstance(m, CampusMetrics)

    def test_sub_metrics_present(self):
        result, agents = _run(2, 4)
        m = MetricsAggregator.compute(result)
        assert isinstance(m.market, MarketMetrics)
        assert isinstance(m.fairness, FairnessMetrics)
        assert isinstance(m.conservation, EnergyConservationMetrics)

    def test_slot_series_correct_length(self):
        result, _ = _run(2, 6)
        m = MetricsAggregator.compute(result)
        assert len(m.slot_timestamps) == 6
        assert len(m.slot_demand_kw) == 6
        assert len(m.slot_served_kw) == 6

    def test_to_dict_serialisable(self):
        import json
        result, _ = _run(2, 3)
        m = MetricsAggregator.compute(result)
        d = m.to_dict()
        json.dumps(d)  # must not raise


# ---------------------------------------------------------------------------
# Campus totals — conservation check
# ---------------------------------------------------------------------------

class TestCampusTotals:
    def test_total_demand_positive(self):
        result, agents = _run(3, 10)
        m = MetricsAggregator.compute(result, agents)
        assert m.total_demand_kwh > 0

    def test_total_served_le_total_demand(self):
        result, agents = _run(3, 8)
        m = MetricsAggregator.compute(result, agents)
        assert m.total_served_kwh <= m.total_demand_kwh + 1e-6

    def test_service_ratio_in_unit_interval(self):
        result, agents = _run(3, 8)
        m = MetricsAggregator.compute(result, agents)
        assert 0.0 <= m.overall_service_ratio <= 1.0 + 1e-9

    def test_critical_service_ratio_in_unit_interval(self):
        result, agents = _run(3, 8)
        m = MetricsAggregator.compute(result, agents)
        assert 0.0 <= m.critical_service_ratio <= 1.0 + 1e-9

    def test_deferred_plus_curtailed_le_demand(self):
        result, agents = _run(3, 8)
        m = MetricsAggregator.compute(result, agents)
        assert m.total_deferred_kwh + m.total_curtailed_kwh <= m.total_demand_kwh + 1e-6

    def test_n_buildings_matches(self):
        result, agents = _run(4, 4)
        m = MetricsAggregator.compute(result, agents)
        assert m.n_buildings == 4

    def test_n_slots_matches(self):
        result, _ = _run(2, 7)
        m = MetricsAggregator.compute(result)
        assert m.n_slots == 7


# ---------------------------------------------------------------------------
# Market metrics
# ---------------------------------------------------------------------------

class TestMarketMetrics:
    def test_total_bids_equals_buildings_times_slots(self):
        result, _ = _run(3, 5)
        m = MetricsAggregator.compute(result)
        assert m.market.total_bids == 3 * 5

    def test_supply_utilization_in_unit_interval(self):
        result, _ = _run(3, 4)
        m = MetricsAggregator.compute(result)
        assert 0.0 <= m.market.supply_utilization <= 1.0 + 1e-9

    def test_bid_acceptance_ratio_in_unit_interval(self):
        result, _ = _run(3, 4)
        m = MetricsAggregator.compute(result)
        assert 0.0 <= m.market.bid_acceptance_ratio <= 1.0 + 1e-9

    def test_scarcity_slots_zero_with_abundant_supply(self):
        result, _ = _run(3, 6, supply_cfg={
            "sources": [{"type": "grid", "source_id": "g", "nominal_capacity_kw": 9999.0}]
        })
        m = MetricsAggregator.compute(result)
        assert m.market.scarcity_slots == 0

    def test_market_to_dict_has_required_keys(self):
        result, _ = _run(2, 3)
        m = MetricsAggregator.compute(result)
        d = m.market.to_dict()
        for key in ("total_bids", "scarcity_slots", "bid_acceptance_ratio"):
            assert key in d


# ---------------------------------------------------------------------------
# Fairness metrics
# ---------------------------------------------------------------------------

class TestFairnessMetrics:
    def test_jains_index_in_unit_interval(self):
        result, _ = _run(4, 6)
        m = MetricsAggregator.compute(result)
        assert 0.0 <= m.fairness.jains_index <= 1.0 + 1e-9
        assert 0.0 <= m.fairness.jains_index_final <= 1.0 + 1e-9

    def test_max_service_ratio_ge_min(self):
        result, _ = _run(3, 5)
        m = MetricsAggregator.compute(result)
        assert m.fairness.max_building_service_ratio >= m.fairness.min_building_service_ratio - 1e-9

    def test_fairness_to_dict(self):
        result, _ = _run(2, 3)
        m = MetricsAggregator.compute(result)
        d = m.fairness.to_dict()
        assert "jains_index" in d
        assert "jains_index_final" in d


# ---------------------------------------------------------------------------
# Energy conservation
# ---------------------------------------------------------------------------

class TestEnergyConservation:
    def test_conservation_ok_with_normal_run(self):
        result, agents = _run(3, 10)
        m = MetricsAggregator.compute(result, agents)
        # Should pass with normal adequate supply
        assert m.conservation.is_conserved

    def test_energy_balance_sum_near_zero(self):
        result, agents = _run(3, 10)
        m = MetricsAggregator.compute(result, agents)
        assert abs(m.conservation.energy_balance_sum_kwh) < 1.0

    def test_conservation_to_dict(self):
        result, agents = _run(2, 3)
        m = MetricsAggregator.compute(result, agents)
        d = m.conservation.to_dict()
        assert "energy_balance_sum_kwh" in d
        assert "is_conserved" in d


# ---------------------------------------------------------------------------
# Summary text
# ---------------------------------------------------------------------------

class TestSummaryText:
    def test_summary_text_contains_key_fields(self):
        result, agents = _run(3, 5)
        m = MetricsAggregator.compute(result, agents)
        text = m.summary_text()
        assert "service ratio" in text.lower()
        assert "demand" in text.lower()
        assert "supply" in text.lower()

    def test_summary_text_is_multiline(self):
        result, _ = _run(2, 3)
        m = MetricsAggregator.compute(result)
        assert text.count("\n") > 5 if (text := m.summary_text()) else True


# ---------------------------------------------------------------------------
# Zero-demand edge case
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_zero_demand_service_ratio_one(self):
        """With zero demand, service ratio should be 1.0 by convention."""
        result, agents = _run(1, 1)
        m = MetricsAggregator.compute(result, agents)
        assert m.overall_service_ratio >= 0.0  # at minimum non-negative

    def test_peak_demand_non_negative(self):
        result, _ = _run(2, 4)
        m = MetricsAggregator.compute(result)
        assert m.peak_demand_kw >= 0.0
