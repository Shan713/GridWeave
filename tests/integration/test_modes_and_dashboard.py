"""Operating modes (before -> after comparison) and the interactive dashboard endpoints."""
from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from gridweave.auction import PriorityAllocationStrategy
from gridweave.coordinator import MODES, MetricsAggregator, get_mode, run_simulation
from gridweave.coordinator.web_dashboard import serve_interactive_dashboard
from gridweave.utils.validation import ValidationError


@pytest.fixture(scope="module")
def outage_runs():
    runs = {name: run_simulation("grid_outage", name) for name in MODES}
    return {name: (out, MetricsAggregator.compute(out.result, out.agents)) for name, out in runs.items()}


def test_three_modes_are_defined():
    assert list(MODES) == ["equal_share", "critical_first", "gridweave"]
    assert get_mode(" GridWeave ").name == "gridweave"
    with pytest.raises(ValidationError):
        get_mode("magic")


def test_every_mode_runs_the_same_campus_and_conserves_energy(outage_runs):
    demands = {round(m.total_demand_kwh, 1) for _, m in outage_runs.values()}
    assert len(demands) == 1                                   # identical demand in every mode
    for out, m in outage_runs.values():
        assert out.result.metadata["mode"] == out.mode.name
        assert m.conservation.is_conserved
        assert all(a.energy_balance_kwh() == pytest.approx(0, abs=1e-6) for a in out.agents.values())


def test_protecting_critical_load_cuts_critical_shortfall_during_an_outage(outage_runs):
    """Claim: under a daytime grid outage, critical-first allocation serves far more critical load
    than an equal share, and the full GridWeave mode is at least as good as critical-first."""
    equal = outage_runs["equal_share"][1].total_critical_shortfall_kwh
    critical_first = outage_runs["critical_first"][1].total_critical_shortfall_kwh
    gridweave = outage_runs["gridweave"][1].total_critical_shortfall_kwh
    assert critical_first < 0.5 * equal
    assert gridweave <= critical_first + 1e-6


def test_demand_response_round_only_in_full_mode(outage_runs):
    assert outage_runs["equal_share"][1].market.re_auction_slots == 0
    assert outage_runs["critical_first"][1].market.re_auction_slots == 0
    assert outage_runs["gridweave"][1].market.re_auction_slots > 0


def test_strategy_override():
    default = run_simulation("normal", "gridweave", steps=4)
    override = run_simulation("normal", "gridweave", steps=4, strategy=PriorityAllocationStrategy())
    assert {a.metadata["mechanism"] for a in default.result.slots[-1].clearing.allocations} != {"PriorityOnlyBaseline"}
    assert {a.metadata["mechanism"] for a in override.result.slots[-1].clearing.allocations} == {"PriorityOnlyBaseline"}
    assert len(override.result.slots) == 4


def test_interactive_dashboard_endpoints():
    server = serve_interactive_dashboard(run_simulation("grid_outage", "gridweave", steps=12), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path):
        return json.load(urllib.request.urlopen(base + path, timeout=60))

    try:
        opts = get("/api/options")
        assert opts["interactive"] is True
        assert {m["name"] for m in opts["modes"]} == set(MODES)
        assert opts["current"] == {"scenario": "grid_outage", "mode": "gridweave"}
        assert len(get("/api/replay")) == 12

        assert get("/api/run?scenario=scarcity&mode=equal_share")["ok"] is True
        metrics = get("/api/metrics")
        assert metrics["scenario_name"] == "scarcity" and metrics["mode"] == "equal_share"
        frames = get("/api/replay")
        assert len(frames) == metrics["n_slots"]
        frame = frames[0]
        assert {"i", "t", "supply", "requested", "served", "sources", "buildings"} <= set(frame)
        assert {"requested", "critical", "allocated", "needed", "served", "critical_short"} <= set(frame["buildings"][0])

        rows = get("/api/compare?scenario=scarcity")
        assert [r["mode"] for r in rows] == list(MODES)

        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/run?scenario=nope&mode=gridweave", timeout=10)
    finally:
        server.shutdown()


def test_read_only_dashboard_has_no_run_endpoint():
    from gridweave.coordinator.web_dashboard import serve_dashboard

    out = run_simulation("normal", "gridweave", steps=4)
    server = serve_dashboard(out.result, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert json.load(urllib.request.urlopen(base + "/api/options", timeout=10))["interactive"] is False
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/run?scenario=normal&mode=gridweave", timeout=10)
    finally:
        server.shutdown()
