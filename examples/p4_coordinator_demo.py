"""Demonstration of Coordinator & Full Closed-Loop Campus Energy Simulation.

Shows:
1. Orchestration of Building Agents, Market Engine, and Supply Provider.
2. Executing preset scenarios (Normal, Solar Drop, Grid Outage, Scarcity).
3. Collecting end-to-end metrics via MetricsAggregator.
4. Rendering rich terminal dashboard with CLI dashboard.
"""
from __future__ import annotations

import sys

from gridweave.auction import AuctionEngine, GreedyAllocationStrategy
from gridweave.config import synthetic_campus
from gridweave.coordinator import (
    Coordinator,
    MetricsAggregator,
    get_scenario,
)
from gridweave.coordinator.cli_dashboard import CliDashboard
from gridweave.factory import build_agents, build_simulators
from gridweave.supply import CampusSupplyProvider


def run_demo(scenario_name: str = "solar_drop", steps: int = 48) -> None:
    print("\n==========================================================================")
    print(f"  GridWeave Coordinator Demo - Running Scenario: '{scenario_name}' ({steps} slots)")
    print("==========================================================================")

    scenario = get_scenario(scenario_name)
    cfg = synthetic_campus(scenario.n_buildings, seed=scenario.seed)
    agents = build_agents(cfg)
    simulators = build_simulators(cfg)
    auction_engine = AuctionEngine(strategy=GreedyAllocationStrategy())
    supply_provider = CampusSupplyProvider.from_config(scenario.supply_config)

    coordinator = Coordinator(
        agents=agents,
        environments=simulators,
        auctioneer=auction_engine,
        supply=supply_provider,
        scenario=scenario,
    )

    print(f"Initialized coordinator with {len(agents)} buildings, supply provider, and auction engine.")
    print("Running simulation loop...\n")

    result = coordinator.run(steps=steps)
    metrics = MetricsAggregator.compute(result)

    print("Simulation complete! Rendering terminal dashboard...\n")
    print(CliDashboard.render_summary(result, metrics))
    print("\n" + CliDashboard.render_time_series_ascii(result))


if __name__ == "__main__":
    scenario_name = sys.argv[1] if len(sys.argv) > 1 else "solar_drop"
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 48
    run_demo(scenario_name=scenario_name, steps=steps)
