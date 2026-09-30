"""Tests for dashboard data layer — verifies the one canonical metrics source.

The dashboards must never compute metrics independently.  These tests confirm
that :class:`MetricsAggregator` produces all data fields needed by both the
web dashboard and the native Python dashboard.
"""
from __future__ import annotations

import pytest

from gridweave.auction import AuctionEngine, GreedyAllocationStrategy
from gridweave.config.settings import synthetic_campus
from gridweave.coordinator import (
    Coordinator,
    MetricsAggregator,
    SimulationResult,
    get_scenario,
)
from gridweave.factory import build_agents, build_simulators
from gridweave.supply.provider import CampusSupplyProvider

_SUPPLY_CFG = {
    "sources": [
        {"type": "grid",    "source_id": "g", "nominal_capacity_kw": 400.0},
        {"type": "solar",   "source_id": "s", "installed_capacity_kw": 150.0},
        {"type": "battery", "source_id": "b", "capacity_kwh": 80.0, "initial_soc": 0.6},
    ]
}


def _run_sim(n: int = 3, steps: int = 8) -> tuple[SimulationResult, dict]:
    cfg = synthetic_campus(n, seed=42)
    agents = build_agents(cfg)
    envs = build_simulators(cfg)
    auc = AuctionEngine(strategy=GreedyAllocationStrategy())
    supply = CampusSupplyProvider.from_config(_SUPPLY_CFG)
    coord = Coordinator(agents, envs, auc, supply)
    result = coord.run(steps=steps)
    return result, agents


def _run_scarce(steps: int = 96) -> tuple[SimulationResult, dict]:
    """A supply-constrained run (the built-in 'scarcity' scenario) so shortfalls are non-zero."""
    scenario = get_scenario("scarcity")
    cfg = synthetic_campus(5, seed=scenario.seed)
    agents = build_agents(cfg)
    coord = Coordinator(agents, build_simulators(cfg), AuctionEngine(strategy=GreedyAllocationStrategy()),
                        CampusSupplyProvider.from_config(scenario.supply_config), scenario=scenario)
    return coord.run(steps=steps), agents


class TestDashboardDataLayer:
    """Verify the dashboard data contract: one canonical MetricsAggregator."""

    def test_metrics_aggregator_is_sole_source(self):
        """The same metrics object must be used for both dashboards."""
        result, agents = _run_sim(3, 6)
        m1 = MetricsAggregator.compute(result, agents)
        m2 = MetricsAggregator.compute(result, agents)
        # Deterministic — same result twice
        assert m1.overall_service_ratio == m2.overall_service_ratio
        assert m1.total_demand_kwh == m2.total_demand_kwh

    def test_overview_kpis_available(self):
        """Fields required by the web dashboard Overview page."""
        result, agents = _run_sim(3, 5)
        m = MetricsAggregator.compute(result, agents)
        # Service
        assert hasattr(m, "overall_service_ratio")
        assert hasattr(m, "critical_service_ratio")
        # Demand
        assert hasattr(m, "total_demand_kwh")
        assert hasattr(m, "total_served_kwh")
        assert hasattr(m, "total_deferred_kwh")
        assert hasattr(m, "total_curtailed_kwh")
        assert hasattr(m, "total_critical_shortfall_kwh")
        # Supply
        assert hasattr(m, "total_grid_kwh")
        assert hasattr(m, "total_solar_kwh")
        assert hasattr(m, "total_battery_discharge_kwh")
        assert hasattr(m, "renewable_penetration_rate")
        # Market
        assert hasattr(m.market, "scarcity_slots")
        assert hasattr(m.market, "re_auction_slots")
        assert hasattr(m.market, "avg_clearing_price")
        assert hasattr(m.market, "supply_utilization")
        # Fairness
        assert hasattr(m.fairness, "jains_index")
        assert hasattr(m.fairness, "jains_index_final")

    def test_time_series_for_demand_supply_chart(self):
        """Fields required by Demand vs Supply time-series chart."""
        result, _ = _run_sim(3, 8)
        m = MetricsAggregator.compute(result)
        n = result.n_slots
        assert len(m.slot_timestamps) == n
        assert len(m.slot_demand_kw) == n
        assert len(m.slot_supply_kw) == n
        assert len(m.slot_allocated_kw) == n
        assert len(m.slot_delivered_kw) == n
        assert len(m.slot_served_kw) == n

    def test_time_series_for_critical_chart(self):
        result, _ = _run_sim(2, 5)
        m = MetricsAggregator.compute(result)
        assert len(m.slot_critical_shortfall_kw) == result.n_slots
        assert len(m.slot_deferred_kw) == result.n_slots
        assert len(m.slot_curtailed_kw) == result.n_slots

    def test_time_series_for_price_chart(self):
        result, _ = _run_sim(2, 4)
        m = MetricsAggregator.compute(result)
        assert len(m.slot_clearing_price) == result.n_slots
        # Prices are None or float
        for p in m.slot_clearing_price:
            assert p is None or isinstance(p, float)

    def test_time_series_for_fairness_chart(self):
        result, _ = _run_sim(3, 6)
        m = MetricsAggregator.compute(result)
        assert len(m.slot_service_ratio) == result.n_slots

    def test_time_series_for_events_chart(self):
        result, _ = _run_sim(2, 4)
        m = MetricsAggregator.compute(result)
        assert len(m.slot_events) == result.n_slots
        for ev_list in m.slot_events:
            assert isinstance(ev_list, list)

    def test_building_table_data_available(self):
        """Data required for per-building table in dashboard."""
        result, agents = _run_sim(3, 6)
        for bid, bs in result.building_summaries.items():
            assert hasattr(bs, "demand_kwh")
            assert hasattr(bs, "served_kwh")
            assert hasattr(bs, "service_ratio")
            assert hasattr(bs, "critical_shortfall_kwh")
            assert hasattr(bs, "critical_shortfall_events")
            assert hasattr(bs, "deferred_kwh")
            assert hasattr(bs, "curtailed_kwh")
            assert hasattr(bs, "forecast_mae_kw")
            assert hasattr(bs, "total_cost")

    def test_building_series_for_per_building_chart(self):
        result, _ = _run_sim(3, 5)
        bid = next(iter(result.building_ids))
        for field in ("served_kw", "allocated_kw", "deferred_kw", "actual_demand_kw"):
            series = result.building_series(bid, field)
            assert len(series) == result.n_slots

    def test_export_to_dict_has_all_sections(self):
        result, agents = _run_sim(2, 3)
        d = result.to_dict()
        assert "slots" in d
        assert "building_summaries" in d
        assert "supply_metrics" in d
        assert "event_log" in d
        assert "n_buildings" in d
        assert "overall_service_ratio" in d

    def test_supply_metrics_has_grid_solar_battery(self):
        result, _ = _run_sim(2, 4)
        sm = result.supply_metrics
        assert "grid_import_kwh" in sm
        assert "solar_delivered_kwh" in sm
        assert "battery_discharge_kwh" in sm
        assert "renewable_penetration_rate" in sm
        assert "total_procurement_cost" in sm

    def test_market_decision_traces_in_slot(self):
        """MarketResult (with traces) is stored when AuctionEngine is used."""
        result, _ = _run_sim(2, 2)
        for r in result.slots:
            # market_result may be None if engine doesn't expose get_last_result
            # but if it exists it should have decision_traces
            if r.market_result is not None:
                assert hasattr(r.market_result, "decision_traces")

    def test_event_log_accessible(self):
        result, _ = _run_sim(2, 3)
        assert isinstance(result.event_log, list)
        for ev in result.event_log:
            assert hasattr(ev, "event_type")
            assert hasattr(ev, "slot_index")

    def test_two_dashboards_same_service_ratio(self):
        """Simulate web + native dashboards both reading from the same metrics."""
        result, agents = _run_sim(3, 6)
        web_metrics = MetricsAggregator.compute(result, agents)
        python_metrics = MetricsAggregator.compute(result, agents)
        assert web_metrics.overall_service_ratio == python_metrics.overall_service_ratio
        assert web_metrics.total_demand_kwh == python_metrics.total_demand_kwh
        assert web_metrics.fairness.jains_index == python_metrics.fairness.jains_index

    def test_unserved_breakdown_partitions_demand(self):
        """Dashboards show where unserved energy went; the parts must add up to demand."""
        result, agents = _run_scarce()
        m = MetricsAggregator.compute(result, agents)
        parts = (m.total_served_kwh + m.total_critical_shortfall_kwh + m.total_curtailed_kwh
                 + m.total_expired_kwh + m.total_backlog_remaining_kwh)
        assert parts == pytest.approx(m.total_demand_kwh, abs=0.01)
        assert m.total_unserved_kwh == pytest.approx(m.total_demand_kwh - m.total_served_kwh)
        # the scenario really is scarce, so the breakdown is non-trivial
        assert m.total_critical_shortfall_kwh > 0 and m.total_expired_kwh > 0
        assert 0.0 <= m.critical_service_ratio < 1.0

    def test_building_bar_segments_sum_to_demand(self):
        """The per-building stacked bar uses end states only, so it sums to demand."""
        result, _ = _run_scarce()
        for bs in result.building_summaries.values():
            d = bs.to_dict()
            stacked = (d["served_kwh"] + d["critical_shortfall_kwh"] + d["curtailed_kwh"]
                       + d["expired_kwh"] + d["final_backlog_kwh"])
            assert stacked == pytest.approx(d["demand_kwh"], abs=0.05), bs.building_id

    def test_web_api_serves_critical_and_expired_fields(self):
        """The web page reads these keys from /api/metrics; they must be served."""
        import json
        import threading
        import urllib.request

        from gridweave.coordinator.web_dashboard import serve_dashboard

        result, _ = _run_scarce(steps=24)
        server = serve_dashboard(result, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            data = json.load(urllib.request.urlopen(f"{url}/api/metrics", timeout=5))
            page = urllib.request.urlopen(url, timeout=5).read().decode()
        finally:
            server.shutdown()
        for key in ("critical_service_ratio", "total_critical_shortfall_kwh", "total_critical_shortfall_events",
                    "total_expired_kwh", "total_backlog_remaining_kwh", "total_unserved_kwh"):
            assert key in data, key
        for key in ("expired_kwh", "critical_shortfall_kwh", "final_backlog_kwh"):
            assert key in next(iter(data["building_summaries"].values())), key
        assert "Critical Load Served" in page and "kpi-crit-ratio" in page
        assert "batteryChart" in page and "priceChart" in page
        assert {"battery_soc", "battery_discharge_kw", "battery_offer_price", "grid_price"} <= set(data["series"])

    def test_battery_series_for_dashboard(self):
        """Battery SOC, discharge and prices are available per slot and consistent with the run."""
        result, agents = _run_scarce(steps=48)
        m = MetricsAggregator.compute(result, agents)
        s = m.series_dict()
        n = result.n_slots
        assert len(s["battery_soc"]) == len(s["battery_discharge_kw"]) == len(s["grid_price"]) == n
        assert all(v is None or 0.0 <= v <= 1.0 for v in s["battery_soc"])
        assert sum(s["battery_discharge_kw"]) * 0.25 == pytest.approx(m.total_battery_discharge_kwh, abs=0.1)
        assert all(p is not None and p > 0 for p in s["battery_offer_price"])

    def test_cli_report_shows_critical_and_expired(self):
        from gridweave.coordinator.cli_dashboard import CliDashboard

        result, agents = _run_scarce(steps=24)
        text = CliDashboard.render_summary(result, MetricsAggregator.compute(result, agents))
        assert "Critical served" in text and "Crit. short" in text and "Expired" in text

    def test_web_dashboard_make_handler(self):
        """Verify web dashboard handler initializes without attribute errors."""
        from gridweave.coordinator.web_dashboard import make_handler
        result, _ = _run_sim(2, 3)
        handler_cls = make_handler(result)
        assert handler_cls is not None
