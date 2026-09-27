# GridWeave architecture

## System context

```
                         GRIDWEAVE
                             │
        ┌────────────────────┼─────────────────────┐
        ▼                    ▼                     ▼
  Building Agents      Auction / Market       Energy Supply
  (P1, this repo)          (P2)            grid · solar · battery (P3)
        │                    │                     │
        └────────────────────┼─────────────────────┘
                             ▼
                 Grid Coordinator + environment (P4)
```

All four workstreams exchange **only** the immutable, validated models in `gridweave.models`, through
the structural protocols in `gridweave.interfaces`:

```
            Observation                     BidContext (slot, scarcity)
Environment ───────────► BuildingAgent ◄──────────────────────────── Coordinator (P4)
 (P4 / sim)                  │    ▲                                        │
                             │Bid │Allocation                              │ available_power_kw(slot)
                             ▼    │                                        ▼
                         Auctioneer (P2) ◄──── available_supply_kw ── SupplyProvider (P3)
```

## Package layout (Workstream 1)

```
src/gridweave/
├── models/          contracts: BuildingSpec, Observation, DemandState, LoadClassification,
│                    BidContext, Bid, Allocation, AllocationOutcome, TimeSlot  (depend only on utils)
├── simulation/      environment side: DemandProfile, DemandGenerator, BuildingSimulator, CSV I/O
├── forecasting/     BaseForecaster + MA / EWMA / seasonal / fallback, metrics, backtest, spike detector
├── classification/  LoadClassifier: critical / flexible / minimum split
├── bidding/         PriorityModel (explainable score), BidGenerator (+ PricingPolicy)
├── agents/          BaseAgent, BuildingAgent (lifecycle orchestration), allocation response
├── interfaces.py    Protocols: DemandAgent, Auctioneer, SupplyProvider, EnvironmentStream
├── config/          typed settings + JSON campus loader + synthetic_campus(n)
├── factory.py       config → agents / simulators (the only place that knows the wiring)
├── mocks/           MockAuctioneer, MockGrid, MockCoordinator (test doubles, NOT production)
└── utils/           validation helpers, logging
```

### Dependency direction

```
utils ◄── models ◄── simulation
               ▲ ◄── forecasting
               ▲ ◄── classification
               ▲ ◄── bidding
               ▲ ◄── agents ──► forecasting, classification, bidding
               ▲ ◄── interfaces
               ▲ ◄── mocks ──► interfaces
config ──► models, bidding (weights), simulation (profiles)
factory ──► everything above (composition root)
```

There are no cycles. `agents` never import `mocks`, `config` or `simulation`: an agent does not know
whether its observations come from a simulator or a real meter, or who runs the auction.

## Key design decisions

| Decision | Rationale |
|---|---|
| Frozen, self-validating dataclasses (stdlib, no Pydantic) | Invalid physical states are unconstructable. Zero dependencies. Objects can be shared safely between workstreams |
| One `BuildingAgent` class, behaviour set by `BuildingSpec` + injected strategies (forecaster, priority model, bid generator) | Scales to N buildings by configuration. Strategy injection keeps the agent small and testable |
| Protocols (structural typing) instead of base classes for cross-team interfaces | Teammates don't inherit from P1 code. Any class with the right methods fits. `isinstance` checks work via `runtime_checkable` |
| Deterministic, explainable numerics (no LLM in the loop) | Reproducible experiments. Viva-friendly: every bid carries an `explanation` with the priority factors, weights and pricing pressure |
| Seeded generators with per-building seeds derived by CRC32 | Identical results across machines and runs. Adding a building never changes other buildings' data |
| Deterministic bid IDs `building:YYYYMMDDTHHMM:rN` | Human-readable, reproducible, unique per building, slot and revision |
| Truthful quantities, strategic price only | The auction can trust `requested/minimum/critical`. Scarcity only moves `willingness_to_pay` |

## Wrapping with an agent framework

If the team later adopts LangGraph, AutoGen or CrewAI, wrap the existing methods as nodes or tools:
`observe` → `generate_bid` → (auction node) → `apply_allocation`. The framework orchestrates. The
numbers still come from this package, so results stay reproducible and testable without the framework.
