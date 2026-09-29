"""Run full campus energy simulation with real demand agents, auction market, supply provider, and coordinator.

Command-line interface to run predefined or custom scenarios, export metrics/JSON results,
launch web dashboard, or output ASCII terminal reports.

Examples:
  python scripts/run_campus_simulation.py --scenario normal --steps 96
  python scripts/run_campus_simulation.py --scenario solar_drop --web --port 8050
  python scripts/run_campus_simulation.py --buildings 10 --days 2 --output results.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from gridweave.auction import AuctionEngine, GreedyAllocationStrategy, PriorityAllocationStrategy
from gridweave.config import load_campus_config, synthetic_campus
from gridweave.coordinator import (
    Coordinator,
    MetricsAggregator,
    SCENARIOS,
    Scenario,
    get_scenario,
)
from gridweave.coordinator.cli_dashboard import CliDashboard
from gridweave.coordinator.web_dashboard import serve_dashboard
from gridweave.factory import build_agents, build_simulators
from gridweave.supply import CampusSupplyProvider
from gridweave.utils.logging import configure


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--scenario",
        default="normal",
        choices=sorted(SCENARIOS.keys()),
        help="predefined scenario name (default: 'normal')",
    )
    parser.add_argument(
        "--buildings",
        type=int,
        help="override number of buildings (synthetic campus)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        help="number of 15-min slots to run (default: scenario length)",
    )
    parser.add_argument(
        "--days",
        type=int,
        help="override number of simulation days",
    )
    parser.add_argument(
        "--strategy",
        choices=["greedy", "priority"],
        default="greedy",
        help="P2 market allocation strategy (default: greedy)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="master random seed",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="launch interactive web dashboard after simulation",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8050,
        help="port for web dashboard server (default: 8050)",
    )
    parser.add_argument(
        "--output",
        help="path to write JSON simulation results and metrics",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress terminal dashboard output",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="logging level (DEBUG, INFO, WARNING, ERROR)",
    )

    args = parser.parse_args()
    configure(args.log_level)

    scenario = get_scenario(args.scenario)
    if args.days is not None or args.buildings is not None or args.seed is not None:
        scenario = Scenario(
            name=f"{scenario.name}_custom",
            description=f"{scenario.description} (customized)",
            days=args.days if args.days is not None else scenario.days,
            n_buildings=args.buildings if args.buildings is not None else scenario.n_buildings,
            seed=args.seed if args.seed is not None else scenario.seed,
            events=scenario.events,
            supply_config=scenario.supply_config,
            negotiation_rounds=scenario.negotiation_rounds,
            on_failure=scenario.on_failure,
        )

    n_bldg = scenario.n_buildings if scenario.n_buildings > 0 else 5
    cfg = synthetic_campus(n_bldg, seed=scenario.seed)
    agents = build_agents(cfg)
    simulators = build_simulators(cfg)

    strat = PriorityAllocationStrategy() if args.strategy == "priority" else GreedyAllocationStrategy()
    auction_engine = AuctionEngine(strategy=strat)
    supply_provider = CampusSupplyProvider.from_config(scenario.supply_config)

    coordinator = Coordinator(
        agents=agents,
        environments=simulators,
        auctioneer=auction_engine,
        supply=supply_provider,
        scenario=scenario,
    )

    n_steps = args.steps if args.steps is not None else scenario.n_slots
    print(f"Starting GridWeave Campus Simulation...")
    print(f"Scenario: {scenario.name} | Buildings: {len(agents)} | Steps: {n_steps} | Strategy: {args.strategy}")

    t0 = time.perf_counter()
    result = coordinator.run(steps=n_steps)
    elapsed = time.perf_counter() - t0

    metrics = MetricsAggregator.compute(result)

    if not args.quiet:
        print("\n" + CliDashboard.render_summary(result, metrics))
        print("\n" + CliDashboard.render_time_series_ascii(result))
        print(f"\nCompleted {len(result.slots)} slots in {elapsed:.2f}s ({len(result.slots)/elapsed:.1f} slots/sec)")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = result.to_dict()
        data["metrics"] = metrics.to_dict()
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"Saved simulation results to {out_path}")

    if args.web:
        print(f"\nLaunching web dashboard at http://localhost:{args.port} ... (Press Ctrl+C to stop)")
        server = serve_dashboard(result, host="127.0.0.1", port=args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nDashboard server stopped.")
            server.server_close()


if __name__ == "__main__":
    main()
