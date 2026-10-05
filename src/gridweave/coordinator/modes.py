"""Operating modes: the before -> after comparison for GridWeave.

Every mode runs the *same* campus, the *same* demand and the *same* supply
events; only the decision-making changes. That isolates what the market and
the agents' demand response contribute.

====================  ==========================================================
``equal_share``       Baseline. Every building gets the same fraction of what it
                      asked for (P2 ``ProportionalAllocationStrategy``). No
                      priority, no critical-load protection, no demand response.
``critical_first``    Critical load is served first, then minimum load, then
                      flexible load by priority (P2 ``GreedyAllocationStrategy``).
                      No demand-response round.
``gridweave``         The full system: P2's welfare-maximising allocation
                      (``OptimizedAllocationStrategy``) plus a demand-response
                      round in which buildings trim flexible load when supply is
                      short (``negotiation_rounds=2``).
====================  ==========================================================

Battery charging and cost-based battery pricing (P3) are physical supply
behaviour and are the same in every mode.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from gridweave.auction import (
    AuctionEngine,
    GreedyAllocationStrategy,
    OptimizedAllocationStrategy,
    ProportionalAllocationStrategy,
)
from gridweave.config import synthetic_campus
from gridweave.coordinator.coordinator import Coordinator
from gridweave.coordinator.records import SimulationResult
from gridweave.coordinator.scenarios import Scenario, get_scenario
from gridweave.factory import build_agents, build_simulators
from gridweave.supply import CampusSupplyProvider
from gridweave.utils.validation import ValidationError


@dataclass(frozen=True)
class Mode:
    name: str
    label: str
    description: str
    strategy_factory: Callable[[], Any]
    negotiation_rounds: int


MODES: dict[str, Mode] = {
    m.name: m for m in (
        Mode("equal_share", "Equal share (baseline)",
             "Everyone gets the same fraction of their request; no priority, no critical protection.",
             ProportionalAllocationStrategy, 1),
        Mode("critical_first", "Critical first",
             "Critical load first, then minimum, then flexible load by priority; no demand response.",
             GreedyAllocationStrategy, 1),
        Mode("gridweave", "GridWeave (full)",
             "Welfare-maximising allocation plus a demand-response round when supply is short.",
             OptimizedAllocationStrategy, 2),
    )
}

DEFAULT_MODE = "gridweave"


def get_mode(name: str) -> Mode:
    key = name.strip().lower()
    if key not in MODES:
        raise ValidationError(f"Unknown mode {name!r}. Available: {sorted(MODES)}")
    return MODES[key]


@dataclass
class RunOutput:
    result: SimulationResult
    agents: dict[str, Any]
    supply: CampusSupplyProvider
    scenario: Scenario
    mode: Mode


def run_simulation(
    scenario: str | Scenario = "normal",
    mode: str | Mode = DEFAULT_MODE,
    *,
    steps: int | None = None,
    n_buildings: int | None = None,
    days: int | None = None,
    seed: int | None = None,
    strategy: Any | None = None,
) -> RunOutput:
    """Build and run one simulation. ``strategy`` overrides the mode's P2 strategy."""
    sc = get_scenario(scenario) if isinstance(scenario, str) else scenario
    md = get_mode(mode) if isinstance(mode, str) else mode
    changes: dict[str, Any] = {"negotiation_rounds": md.negotiation_rounds}
    if days is not None:
        changes["days"] = days
    if n_buildings is not None:
        changes["n_buildings"] = n_buildings
    if seed is not None:
        changes["seed"] = seed
    sc = replace(sc, **changes)

    cfg = synthetic_campus(sc.n_buildings if sc.n_buildings > 0 else 5, seed=sc.seed)
    agents = build_agents(cfg)
    supply = CampusSupplyProvider.from_config(sc.supply_config)
    engine = AuctionEngine(strategy=strategy if strategy is not None else md.strategy_factory())
    coordinator = Coordinator(agents, build_simulators(cfg), engine, supply, scenario=sc)
    result = coordinator.run(steps if steps is not None else sc.n_slots)
    result.metadata["mode"] = md.name
    return RunOutput(result, agents, supply, sc, md)
