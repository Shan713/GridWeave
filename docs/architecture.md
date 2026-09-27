# GridWeave architecture

## System context (closed loop, one 15-minute slot)

```
                  ┌───────────────────────── P4 Coordinator ─────────────────────────┐
                  │                                                                   │
 Environment ──Observation (slot t-1)──► Building Agents (P1) ──Bid──► Auction / Market (P2)
 (P4 owns; P1                              ▲        │                     │     ▲
  provides the                             │        │ revised Bid          │     │ SupplyOffers
  simulator)                               │        ▼ (scarcity)           │     │
     ▲                                     │   [re-auction round]          │   Supply (P3)
     │                                     │                               │  grid / solar / battery
     │                         Allocation ─┘      ClearingResult ─────────►│     │
     │                                                  DispatchRequests ──┼────►│ dispatch
     │                                                                     │  DispatchResults (SOC)
     └── apply_settlement ◄── Settlement ◄── agent.settle(allocation, realised Observation for slot t)
```

The **bid** is a forecast. The **settlement** compares the allocation with the demand that actually
occurred. All service metrics come from settlements, so forecasting quality affects outcomes.

All workstreams exchange **only** the immutable, validated models in `gridweave.models`, through the
`typing.Protocol`s in `gridweave.interfaces` (contract version 2.0). `gridweave.contracts` checks
cross-party consistency (`validate_clearing`, `validate_dispatch`).

## Ownership

| | Owns | Must not implement |
|---|---|---|
| **P1** | Building Agent, demand simulation, forecasting, load classification, priority, bid, settlement, local response (defer/curtail/queue) | market clearing, dispatch, the coordinator loop |
| **P2** | Auction mechanism, bid ranking, pricing, allocation optimisation, critical-load constraint policy | building forecasting or supply physics |
| **P3** | Grid, solar and battery models, supply offers, dispatch, state of charge | market clearing |
| **P4** | Environment and time progression, events, re-auction policy, dashboard, global metrics | the other three algorithms |

## Package layout

```
src/gridweave/
├── models/          contracts: BuildingSpec, Observation, DemandState, LoadClassification, BidContext,
│                    Bid, Allocation, AllocationOutcome (ex-ante), Settlement + DeferredEnergy (ex-post),
│                    SupplyOffer, DispatchRequest, DispatchResult, ClearingResult, TimeSlot
├── contracts.py     cross-party checks: validate_clearing, validate_dispatch
├── interfaces.py    Protocols: DemandAgent, Auctioneer, SupplyProvider, EnvironmentStream
├── simulation/      environment side: DemandProfile, DemandGenerator, BuildingSimulator (feedback hook)
├── forecasting/     BaseForecaster + MA / EWMA / seasonal / fallback, metrics, backtest, spike detector
├── classification/  LoadClassifier: critical / flexible / minimum
├── bidding/         PriorityModel, BidGenerator (+ PricingPolicy, voluntary reduction)
├── agents/          BaseAgent, BuildingAgent (lifecycle, settlement, deferred-energy queue)
├── config/          typed settings, packaged campus_default.json, synthetic_campus(n)
├── factory.py       config → agents / simulators (composition root)
├── mocks/           MockAuctioneer, MockGrid/MockSolar/MockBattery/MockSupply, MockCoordinator
│                    (test doubles for P2/P3/P4, NOT production)
└── utils/           validation helpers, logging
```

### Dependency direction

```
utils ◄── models ◄── simulation, forecasting, classification, bidding
               ▲ ◄── agents ──► forecasting, classification, bidding
               ▲ ◄── contracts, interfaces
               ▲ ◄── mocks ──► contracts, interfaces
config ──► models, bidding (weights), simulation (profiles)
factory ──► everything above (composition root)
```

There are no cycles (verified by import order and by the audit). `agents` never import `mocks`,
`config` or `simulation`.

## Key design decisions

| Decision | Rationale |
|---|---|
| Frozen, self-validating dataclasses (stdlib, no Pydantic) | Invalid physical states cannot be constructed by accident. Zero dependencies. Safe to share between workstreams |
| Bid (ex-ante) vs Settlement (ex-post) | Forecast errors must affect outcomes. Metrics based only on bids would hide them |
| Deferred energy as a queue of kWh with deadlines | Each kWh is counted once. Deferral cannot grow without bound. `energy_balance_kwh()` checks conservation |
| Stateless `Auctioneer.clear(slot, bids, offers)` returning allocations **and** dispatch | P2 can run a genuine multi-source market; P3 gets explicit dispatch and can update battery state |
| Strict 15-min grid; gaps rejected, not repaired | Seasonal models index history by position; silent correction would hide data errors |
| One configurable `BuildingAgent` class with injected strategies | N buildings is a configuration change, not a code change |
| Protocols instead of base classes for cross-team interfaces | Teammates never inherit from P1 code |
| Deterministic, explainable numerics (no LLM, no ML) | Reproducible experiments. Every bid carries an `explanation` |
| Seeded generators with CRC32-derived per-building seeds | Identical results across machines; adding a building never changes the others |

## Scalability: what has actually been measured

`python scripts/benchmark_scaling.py`, 96 closed-loop cycles on synthetic campuses, mock market and a
single mock grid, `keep_records=False`. Measured on Python 3.13.7 on an Apple-silicon (arm64) laptop:

| buildings | ms per 15-min cycle | µs per agent-cycle | peak traced memory |
|---:|---:|---:|---:|
| 3 | 0.6 | 207 | 1.0 MiB |
| 10 | 2.1 | 207 | 2.7 MiB |
| 50 | 10.4 | 208 | 12.5 MiB |
| 100 | 20.5 | 205 | 24.9 MiB |
| 500 | 104.6 | 209 | 123.1 MiB |

**What this shows:** the current simulation loop was run with up to 500 building agents. Its cost grew
linearly (about 0.2 ms per agent per cycle), and per-agent memory is bounded (history and event log
are capped). **What it does not show:** the loop is sequential and single-process, the mock market is
O(N log N), and there is no distributed or parallel execution. Agents are independent between
market calls, so parallelism would be possible, but it has not been implemented or measured. With
`keep_records=True` (the default, for demos) memory also grows with the number of cycles.

## Wrapping with an agent framework

If the team later adopts LangGraph, AutoGen or CrewAI, wrap the existing methods as nodes or tools:
`observe` → `generate_bid` → (market node) → (dispatch node) → `settle`. The framework orchestrates;
the numbers still come from this package, so results stay reproducible and testable without it.
