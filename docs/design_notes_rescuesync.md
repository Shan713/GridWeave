# Design note: what GridWeave takes from RescueSync (and what it doesn't)

[RescueSync](https://github.com/Kavinesh11/RescueSync) is a cooperative multi-agent rescue planner
built for the same course format (Review 1: PEAS / environment / search strategy; Review 2: tools /
execution / demo / structure). We reviewed its repository structure, code and docs before designing
GridWeave. Inspiration is **structural and conceptual only**; no code was copied. The domains differ
completely (grid path-planning vs. energy markets).

## What RescueSync does well, and how GridWeave adapts it

| RescueSync idea | Why it works | GridWeave adaptation |
|---|---|---|
| A pure "graded core engine" package (`rescuesync/`) with no UI dependencies; UI and backend are thin wrappers | The algorithm is testable and demoable without a frontend | `src/gridweave/` is pure standard-library Python. Dashboards (P4) and any LLM/agent framework wrap it and are never the source of truth |
| One module per responsibility (`environment`, `agents`, `reservation`, `planner`, `simulator`, `metrics`) | Easy to navigate and to review | Sub-packages per responsibility: `models`, `simulation`, `forecasting`, `classification`, `bidding`, `agents`, `mocks`, `config` |
| Scenarios are JSON data, not code ("adding robots means editing a JSON file") | Scalability without code changes | `configs/campus_default.json` plus `synthetic_campus(n)`; a new building is one JSON entry |
| Simulator/collision-checker is decoupled from the planner and *verifies* it rather than trusting it | Catches bugs in the thing being verified | Every domain model validates its own invariants. The reference coordinator validates every clearing and dispatch (`gridweave.contracts`). Integration tests re-check conservation laws (served ≤ realised need, Σ allocations ≤ supply, energy balance per building) on every simulated step |
| Several modes side by side (Independent A* / Cooperative A* / RescueSync) to show incremental improvement | Gives Review 2 a clear baseline story | Several forecasters compared under an identical backtest (MA, EWMA, seasonal naive, seasonal EWMA) with real MAE/RMSE/MAPE numbers |
| `experiments.py` regenerates the Review-2 evidence reproducibly | Numbers in the report are reproducible | `scripts/run_forecast_experiment.py` and `scripts/run_building_simulation.py`, all seeded |
| A single design document that explains *why*, states assumptions verbatim, gives PEAS and honest limitations | Viva-ready; stops people contradicting the code | `docs/building_agent.md` (PEAS, environment properties, assumptions), `docs/architecture.md`, plus a limitations section in the README |
| Scenario tests that each prove one specific claim | Tests double as demo evidence | Named tests for each claim (e.g. "forecast error reaches outcomes", "revised bids actually reduce requested power", "runs are reproducible"). The post-audit lesson: a test name is not evidence. Tests must check the claim against realised data, not against the system's own request |

## What GridWeave deliberately does **not** copy

| RescueSync choice | Why it doesn't fit GridWeave |
|---|---|
| Role subclasses (`EngineerAgent`, `MedicAgent`) | Buildings differ in *parameters*, not behaviour. One `BuildingAgent` configured by a `BuildingSpec` and a `DemandProfile` avoids `HostelAAgent`-style class explosion and is what makes 100+ buildings a config change |
| Mutable dataclasses whose fields the planner fills in (`schedule`, `failed`) | GridWeave objects cross team boundaries. `Bid`, `Allocation`, `Observation` and so on are frozen and self-validating so no workstream can corrupt another's data |
| Centralised planner computes everyone's plan | GridWeave is a *market*. Agents decide locally and the auction (P2) allocates. The Building Agent must not contain the auction |
| `sys.path` insertion in `tests/conftest.py` | We use a `src/` layout with `pip install -e .`, so imports behave the same in tests, scripts and teammates' code |
| Pygame and React/FastAPI frontends inside the core repo | Out of scope for Workstream 1. The dashboard belongs to P4 and consumes `agent.snapshot()` / `StepRecord.to_dict()` |
| A* / reservation-table search | Not relevant: GridWeave's "search" is market clearing (P2) plus forecasting and priority heuristics (P1) |
| Heavy dependencies in `requirements.txt` for the core | The GridWeave core has zero runtime dependencies. Dev tools are in the `[dev]` extra |
