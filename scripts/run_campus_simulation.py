"""Run the full campus energy simulation (P1 agents + P2 market + P3 supply + P4 coordinator).

Examples:
  python scripts/run_campus_simulation.py --scenario grid_outage --web
  python scripts/run_campus_simulation.py --scenario grid_outage --compare
  python scripts/run_campus_simulation.py --scenario mixed_stress --mode equal_share
  python scripts/run_campus_simulation.py --buildings 10 --days 2 --output results.json

Modes (same campus, same demand, same events; only decision-making changes):
  equal_share     baseline: everyone gets the same fraction of their request
  critical_first  critical load first, then minimum, then flexible by priority
  gridweave       welfare-maximising market + demand-response round (default)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from gridweave.auction import (
    GreedyAllocationStrategy,
    OptimizedAllocationStrategy,
    PriorityAllocationStrategy,
    ProportionalAllocationStrategy,
)
from gridweave.coordinator import DEFAULT_MODE, MODES, SCENARIOS, MetricsAggregator, run_simulation
from gridweave.coordinator.cli_dashboard import CliDashboard
from gridweave.coordinator.web_dashboard import serve_interactive_dashboard
from gridweave.utils.logging import configure

STRATEGIES = {
    "greedy": GreedyAllocationStrategy,
    "optimized": OptimizedAllocationStrategy,
    "proportional": ProportionalAllocationStrategy,
    "priority": PriorityAllocationStrategy,
}


def compare_modes(args: argparse.Namespace) -> None:
    print(f"Scenario: {args.scenario} | same campus, demand and events in every mode\n")
    print(f"{'mode':<24}{'service':>9}{'critical served':>17}{'crit. shortfall':>17}{'expired':>10}"
          f"{'fairness':>10}{'cost':>11}")
    for name, mode in MODES.items():
        out = run_simulation(args.scenario, name, steps=args.steps, n_buildings=args.buildings,
                             days=args.days, seed=args.seed)
        m = MetricsAggregator.compute(out.result, out.agents)
        print(f"{mode.label:<24}{m.overall_service_ratio:>9.1%}{m.critical_service_ratio:>17.1%}"
              f"{m.total_critical_shortfall_kwh:>13.1f} kWh{m.total_expired_kwh:>6.0f} kWh"
              f"{m.fairness.jains_index_final:>10.3f}{m.total_procurement_cost:>11.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="normal", choices=sorted(SCENARIOS),
                        help="predefined scenario (default: normal)")
    parser.add_argument("--mode", default=DEFAULT_MODE, choices=list(MODES),
                        help=f"decision-making mode (default: {DEFAULT_MODE})")
    parser.add_argument("--compare", action="store_true", help="run every mode on the scenario and compare")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES),
                        help="override the mode's P2 allocation strategy")
    parser.add_argument("--buildings", type=int, help="override number of buildings (synthetic campus)")
    parser.add_argument("--steps", type=int, help="number of 15-min slots to run (default: scenario length)")
    parser.add_argument("--days", type=int, help="override number of simulation days")
    parser.add_argument("--seed", type=int, help="master random seed")
    parser.add_argument("--web", action="store_true",
                        help="open the interactive web dashboard (pick scenario/mode, replay slots)")
    parser.add_argument("--port", type=int, default=8050, help="web dashboard port (default: 8050)")
    parser.add_argument("--output", help="path to write JSON simulation results and metrics")
    parser.add_argument("--quiet", action="store_true", help="suppress terminal dashboard output")
    parser.add_argument("--log-level", default="WARNING", help="logging level (DEBUG, INFO, WARNING, ERROR)")
    args = parser.parse_args()
    configure(args.log_level)

    if args.compare:
        compare_modes(args)
        return

    strategy = STRATEGIES[args.strategy]() if args.strategy else None
    print("Starting GridWeave Campus Simulation...")
    t0 = time.perf_counter()
    out = run_simulation(args.scenario, args.mode, steps=args.steps, n_buildings=args.buildings,
                         days=args.days, seed=args.seed, strategy=strategy)
    elapsed = time.perf_counter() - t0
    result = out.result
    metrics = MetricsAggregator.compute(result, out.agents)
    print(f"Scenario: {out.scenario.name} | Mode: {out.mode.label}"
          f"{f' (strategy override: {args.strategy})' if args.strategy else ''} | "
          f"Buildings: {len(out.agents)} | Slots: {len(result.slots)}")

    if not args.quiet:
        print("\n" + CliDashboard.render_summary(result, metrics))
        print("\n" + CliDashboard.render_time_series_ascii(result))
        print(f"\nCompleted {len(result.slots)} slots in {elapsed:.2f}s ({len(result.slots) / elapsed:.1f} slots/sec)")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = result.to_dict()
        data["metrics"] = metrics.to_dict()
        out_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        print(f"Saved simulation results to {out_path}")

    if args.web:
        print(f"\nLaunching web dashboard at http://localhost:{args.port} ... (Press Ctrl+C to stop)")
        server = serve_interactive_dashboard(out, host="127.0.0.1", port=args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nDashboard server stopped.")
            server.server_close()


if __name__ == "__main__":
    main()
