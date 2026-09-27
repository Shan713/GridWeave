# P1 freeze: Building Intelligence & Demand Management

```
P1 STATUS: SEALED
Contract version: 2.0      (gridweave.interfaces.CONTRACT_VERSION)
Bid schema:       1.1      (gridweave.models.bid.BID_SCHEMA_VERSION)
```

P1 is complete and is a stable dependency for P2, P3 and P4. `tests/unit/test_contract_surface.py`
fails if any item below changes. How to use the contract is described in
[integration_contract.md](integration_contract.md).

## Frozen contract

**Models** (`gridweave.models`; frozen, self-validating dataclasses):
`Bid`, `Allocation`, `Settlement`, `Observation`, `DemandState`, `LoadClassification`, `BidContext`,
`SupplyOffer`, `DispatchRequest`, `DispatchResult`, `ClearingResult`, `TimeSlot`, `DeferredEnergy`
(plus `BuildingSpec`, `AllocationStatus`, `SourceType`, `AllocationOutcome`).

**Protocols** (`gridweave.interfaces`):

| Protocol | Methods | Implemented by |
|---|---|---|
| `DemandAgent` | `building_id`, `observe(observation)`, `generate_bid(context)`, `abort_bid(reason)`, `settle(allocation, realised)`, `snapshot()` | P1 `BuildingAgent` |
| `Auctioneer` | `clear(time_slot, bids, offers) -> ClearingResult` | P2 |
| `SupplyProvider` | `offers(time_slot) -> [SupplyOffer]`, `dispatch(requests) -> [DispatchResult]` | P3 |
| `EnvironmentStream` | `has_next`, `step() -> Observation`, `apply_settlement(settlement)` | P4 (P1 provides `BuildingSimulator`) |

**Agent lifecycle** (`BuildingAgent`):
`observe → generate_bid → receive_allocation (optional ex-ante preview) → settle`, with `abort_bid`,
`snapshot`, `demand_outlook`, `next_slot`, `update_state` and `energy_balance_kwh`.

**Cross-party checks** (`gridweave.contracts`): `validate_clearing(result, bids, offers)`,
`validate_dispatch(requests, results)`, `ContractViolation`.

## Ownership

| | Owns |
|---|---|
| **P1** | Building Agent, demand simulation, forecasting, load classification, priority, bid generation, demand response (voluntary reduction), settlement against realised demand, deferred-energy queue, local defer/curtail decisions, P1 models, validation and integration contracts |
| **P2** | Auction mechanism, ranking, pricing, allocation optimisation, critical-load clearing policy |
| **P3** | Grid, solar, battery, supply offers, dispatch, state of charge and physical supply state |
| **P4** | Environment and time progression, global coordinator loop, events, re-auction policy, dashboard, global metrics |

Everything in `gridweave.mocks` is a **test double** for P2/P3/P4. It is not their algorithm.

## What each workstream can rely on

**P2:**
- `Bid` fields and invariants (`critical ≤ minimum ≤ requested ≤ capacity_kw`,
  `critical + flexible = requested`, priority and flexibility ∈ [0, 1], `wtp ≤ maximum_price`).
- `SupplyOffer`, `Auctioneer.clear(...)` returning a `ClearingResult`, and `validate_clearing(...)`.
- Sample data in `data/sample/`.

**P3:**
- `SupplyOffer`, `DispatchRequest`, `DispatchResult`, and `validate_dispatch(...)`.
- Demand information via `DemandState`, `agent.demand_outlook(horizon)` and `Settlement`.
- `critical_kw` in every classification.

**P4:**
- `DemandAgent`: `observe(...)`, `generate_bid(...)` (revise with `BidContext(slot, scarcity)`), `settle(...)`,
  `abort_bid(...)` and `snapshot(...)`.
- `EnvironmentStream` with `apply_settlement(...)`, and the phase rules.
- A failed `settle` leaves the agent unchanged and `bid_pending`.
- `MockCoordinator` as a reference loop with documented failure recovery (`SettlementError`, `settle_zero`/`raise`).

## Explicit non-guarantees

- P1 does **not** guarantee critical-load service. It identifies, requests and reports; P2/P3/P4 decide.
- P1 does **not** implement market clearing, physical supply, or global re-auction policy.
- Forecasts are statistical baselines (moving average, EWMA, seasonal). They are not machine learning.
- The demand dataset is synthetic. It is not measured, and it favours seasonal forecasters.
- The value of demand response depends on P2's market mechanism. In the mock market it is negligible.
- Scalability evidence covers the current sequential, single-process simulation (up to 500 agents),
  not distributed execution.

## Change policy

After this freeze, breaking changes to the P1 contract require an explicit team decision and a
contract-version change. Feature work belonging to P2, P3 or P4 must not be added to P1 merely for
convenience. Bug fixes that keep the contract are allowed and must keep
`tests/unit/test_contract_surface.py` green.
