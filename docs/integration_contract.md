# Integration contract (version 2): how P2, P3 and P4 use the Building Intelligence subsystem

```bash
pip install -e ".[dev]"
```

All cross-team data types live in `gridweave.models`, and all cross-team interfaces are
`typing.Protocol`s in `gridweave.interfaces` (`CONTRACT_VERSION = "2.0"`). You never subclass P1 code:
any class with the right methods fits, and `isinstance(obj, Auctioneer)` works at runtime.
`gridweave.contracts.validate_clearing` / `validate_dispatch` check that what one workstream returns is
consistent with what another sent. Every `python` block in this file is executed by
`tests/integration/test_docs_examples.py`.

## Ownership

| Workstream | Owns | P1 provides |
|---|---|---|
| **P1** Building intelligence | Building Agent, demand simulation, forecasting, load classification, priority, bid, **settlement against realised demand**, local response (defer/curtail, backlog) | this repository |
| **P2** Market | Auction mechanism, bid ranking, pricing, allocation optimisation, **whether critical demand is a hard constraint** | `Bid`, `SupplyOffer` in; `ClearingResult` out |
| **P3** Supply | Grid, solar and battery models, **supply offers**, **dispatch**, state of charge | `SupplyOffer`, `DispatchRequest`, `DispatchResult` |
| **P4** Coordination | Environment and time progression, events, re-auction policy, dashboard, global metrics | `DemandAgent`, `EnvironmentStream` protocols, `MockCoordinator` reference loop |

The mocks in `gridweave.mocks` (`MockAuctioneer`, `MockGrid`, `MockSolar`, `MockBattery`, `MockSupply`,
`MockCoordinator`) are **test doubles**, not proposals for the real P2/P3/P4 components.

## One slot, end to end

```
P4  env.step() -> agent.observe()             history up to slot t-1 (warm-up / non-bid slots)
P1  agent.generate_bid(BidContext(t))         forecast-based Bid
P3  supply.offers(t)                          [SupplyOffer]  per source: kW limit + marginal price
P4  if Σ requested > Σ offered:
P1      agent.generate_bid(BidContext(t, scarcity))   revised Bid: flexible load trimmed (demand response)
P2  auction.clear(t, bids, offers)            ClearingResult: one Allocation per bid + DispatchRequests
P4  validate_clearing(...)
P3  supply.dispatch(requests)                 [DispatchResult]: delivered kW, new state (e.g. SOC)
P4  env.step()                                realised Observation for slot t
P1  agent.settle(allocation, realised)        Settlement: served, critical shortfall, deferred, curtailed
P4  env.apply_settlement(settlement)          environment state for t+1 (closed loop)
```

The **bid is a forecast**; the **settlement is the truth**. Service metrics (served energy, critical
shortfall, deferral) come only from settlements, so forecast errors have consequences: a building
that forecast 30 kW, bid 30 kW and received 30 kW but actually needed 40 kW is 10 kW short.

---

## For Person 2: market

**You implement** `Auctioneer.clear(time_slot, bids, offers) -> ClearingResult`:

```python
from typing import Sequence
from gridweave.models import Allocation, Bid, ClearingResult, DispatchRequest, SupplyOffer, TimeSlot

class MeritOrderAuction:
    """Toy example: serve critical load first, then by willingness to pay; dispatch cheapest sources."""

    def clear(self, time_slot: TimeSlot, bids: Sequence[Bid], offers: Sequence[SupplyOffer]) -> ClearingResult:
        remaining = sum(o.available_kw for o in offers)
        granted = {}
        for b in bids:                                           # critical first (a policy choice P2 owns)
            granted[b.bid_id] = min(b.critical_power_kw, remaining)
            remaining -= granted[b.bid_id]
        for b in sorted(bids, key=lambda b: -b.willingness_to_pay):
            extra = min(b.requested_power_kw - granted[b.bid_id], remaining)
            granted[b.bid_id] += extra
            remaining -= extra
        need, dispatch = sum(granted.values()), []
        for o in sorted(offers, key=lambda o: o.marginal_price):  # merit order
            take = min(o.available_kw, need)
            if take > 0:
                dispatch.append(DispatchRequest(o.source_id, time_slot, take))
                need -= take
        allocations = tuple(Allocation(b.bid_id, b.building_id, time_slot, granted[b.bid_id]) for b in bids)
        return ClearingResult(time_slot, allocations, tuple(dispatch))

from gridweave.interfaces import Auctioneer
assert isinstance(MeritOrderAuction(), Auctioneer)
```

**Rules** (checked by `gridweave.contracts.validate_clearing`):

1. Exactly one `Allocation` per bid (zero is allowed), echoing `bid_id`, `building_id` and `time_slot`.
2. No allocation above its bid's `requested_power_kw`; total allocation within total offered supply.
3. Dispatch only to sources that offered, within each offer, and with Σ dispatch = Σ allocation.
4. If you fill `Allocation.supply_mix`, it must sum to `allocated_power_kw`.

**Test without P1 or P3:**

```python
import json
from gridweave.contracts import validate_clearing
from gridweave.models import Bid, SourceType, SupplyOffer

bids = [Bid.from_dict(d) for d in json.load(open("data/sample/sample_bids.json"))]
slot = bids[0].time_slot
offers = [SupplyOffer("grid", SourceType.GRID, slot, 250, 10.0),
          SupplyOffer("solar", SourceType.SOLAR, slot, 0, 0.0),
          SupplyOffer("battery", SourceType.BATTERY, slot, 30, 7.0)]
result = MeritOrderAuction().clear(slot, bids, offers)
validate_clearing(result, bids, offers)
assert all(b.requested_power_kw <= b.capacity_kw for b in bids)
```

**Signals you may use:** `critical_power_kw` (hard need), `minimum_power_kw` (soft need),
`priority_score` and `willingness_to_pay` (both derived partly from the same priority factors, so do
not simply add them), `flexibility_score`, `voluntary_reduction_kw` and `revision` (demand-response
round), and each offer's `marginal_price`. Whether critical demand is a **hard constraint** of clearing is
your design decision; P1 only identifies it and reports shortfalls afterwards.

---

## For Person 3: supply

**You implement** `SupplyProvider.offers(slot)` and `SupplyProvider.dispatch(requests)`:

```python
from datetime import datetime
from gridweave.models import DispatchRequest, DispatchResult, SourceType, SupplyOffer, TimeSlot

class CampusSupply:
    def __init__(self, grid_kw: float, battery_kwh: float, battery_kw: float, soc: float = 0.8):
        self.grid_kw, self.battery_kwh, self.battery_kw, self.soc = grid_kw, battery_kwh, battery_kw, soc

    def _battery_kw(self, slot: TimeSlot) -> float:
        return min(self.battery_kw, max(0.0, self.soc - 0.2) * self.battery_kwh / slot.hours)

    def offers(self, time_slot: TimeSlot):
        return [SupplyOffer("grid", SourceType.GRID, time_slot, self.grid_kw, 10.0),
                SupplyOffer("battery", SourceType.BATTERY, time_slot, self._battery_kw(time_slot), 7.0,
                            constraints={"soc": self.soc})]

    def dispatch(self, requests):
        results = []
        for r in requests:
            if r.source_id == "battery":
                delivered = min(r.requested_kw, self._battery_kw(r.time_slot))
                self.soc -= delivered * r.time_slot.hours / self.battery_kwh    # SOC falls with discharge
                results.append(DispatchResult("battery", r.time_slot, r.requested_kw, delivered,
                                              self._battery_kw(r.time_slot), {"soc": self.soc}))
            else:
                delivered = min(r.requested_kw, self.grid_kw)
                results.append(DispatchResult(r.source_id, r.time_slot, r.requested_kw, delivered,
                                              self.grid_kw - delivered))
        return results

from gridweave.interfaces import SupplyProvider
supply = CampusSupply(grid_kw=300, battery_kwh=100, battery_kw=40, soc=0.8)
assert isinstance(supply, SupplyProvider)
slot = TimeSlot(datetime(2026, 1, 5, 19, 0))
result = supply.dispatch([DispatchRequest("battery", slot, 40)])[0]
assert round(result.state["soc"], 3) == 0.7                              # 10 kWh out of 100 kWh
```

`DispatchResult.delivered_kw` may be below `requested_kw` (e.g. the battery hit its minimum SOC).
The reference coordinator then scales allocations down pro rata, so buildings settle against
power that was actually delivered.

**What demand looks like, and how to get it:**

| Need | API | Returns |
|---|---|---|
| Next-slot demand of one building | `agent.update_state()` / `agent.demand_state` | `DemandState`: current, predicted, backlog, desired, critical, flexible, minimum, maximum kW |
| Multi-slot outlook (battery scheduling) | `agent.demand_outlook(horizon=8)` | `[DemandOutlookPoint(timestamp, predicted_demand_kw, confidence, classification)]` |
| What actually happened | `agent.last_settlement`, `StepRecord.settlements` | `Settlement` (realised demand, served, shortfall) |
| Offline demand series | `build_simulators(cfg)` or `data/sample/campus_demand_7d.csv` | `DemandSample(timestamp, demand_kw)` per 15-min slot |

`critical_kw` is the load your reserve strategy should protect. `flexible_kw` can be shifted:
unserved flexible energy is deferred (`deferrable_fraction`) into a queue with a deadline
(`max_deferral_slots`) and re-requested in later slots, or curtailed.

---

## For Person 4: coordinator and environment

**You drive the loop** (this is what `gridweave.mocks.MockCoordinator.run_step` does, with validation
and a failure path; use it as the reference):

```python
from gridweave.config import load_campus_config
from gridweave.contracts import validate_clearing, validate_dispatch
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockSupply
from gridweave.models import BidContext

cfg = load_campus_config()
agents, envs = build_agents(cfg), build_simulators(cfg)
auction, supply = MockAuctioneer(), MockSupply.from_config(cfg.supply)   # swap in P2's / P3's classes

for building_id, env in envs.items():                    # warm-up: first slot is observed only
    agents[building_id].observe(env.step())

slot = agents["hostel_a"].next_slot()
offers = supply.offers(slot)
available = sum(o.available_kw for o in offers)
bids = {i: a.generate_bid(BidContext(slot)) for i, a in agents.items()}
requested = sum(b.requested_power_kw for b in bids.values())
if requested > available:                                # demand-response round: revised bids trim flexible load
    scarcity = 1 - available / requested
    bids = {i: a.generate_bid(BidContext(slot, scarcity=scarcity)) for i, a in agents.items()}

clearing = auction.clear(slot, list(bids.values()), offers)
validate_clearing(clearing, list(bids.values()), offers)
results = supply.dispatch(clearing.dispatch)
validate_dispatch(clearing.dispatch, results)

allocations = {a.building_id: a for a in clearing.allocations}
for building_id, agent in agents.items():
    realised = envs[building_id].step()                  # the slot happens: realised demand
    settlement = agent.settle(allocations[building_id], realised)
    envs[building_id].apply_settlement(settlement)       # closed loop
    print(building_id, settlement.status.value, round(settlement.forecast_error_kw, 1), round(settlement.deferred_kw, 1))
```

| You want to... | Call | Notes |
|---|---|---|
| Feed a measurement for a slot without a bid | `agent.observe(Observation(...))` | Timestamps must be on the 15-min grid and contiguous (`MisalignedTimestampError`, `MissingSlotError`). Impute missing meter readings upstream and flag them in `metadata` |
| Trigger a decision cycle | `agent.generate_bid(BidContext(slot, scarcity))` | Slot must be after the latest observation |
| Run a demand-response / re-auction round | Call `generate_bid` again for the **same** slot with `scarcity > 0` | Revision +1; `requested` drops by `scarcity × scarcity_response × (requested − minimum)`; critical and minimum never drop |
| Close a slot | `agent.settle(allocation, realised_observation)` | Returns a `Settlement`; the realised demand also joins the forecasting history |
| Withdraw a bid (auction failed, will re-run) | `agent.abort_bid(reason)` | Back to `observed`; then bid again, or `observe()` the realised slot |
| Preview an allocation against the bid | `agent.receive_allocation(allocation)` | Ex-ante only, no state change, not a service metric |
| Observe agent state | `agent.snapshot()`, `agent.stats`, `agent.backlog`, `agent.events` | JSON-ready |
| Check accounting | `agent.energy_balance_kwh()` | ≈ 0: every kWh of realised demand is served, short, curtailed, expired or queued |
| Inject environment dynamics | `EnvironmentStream.apply_settlement`, `BuildingSimulator.demand_modifier`, `rebound_fraction` | e.g. curtailed HVAC load rebounds next slot |

**Phase rules** (violations raise `AgentStateError`): no bid before the first observation; no bid for a
different slot while one is pending; the pending slot must be closed with `settle` (or `abort_bid`),
not `observe`. A `settle` that raises a validation error (e.g. realised demand above capacity, wrong
slot) changes nothing: the agent stays `bid_pending` with its bid intact until the coordinator acts.

**Failure recovery in the reference coordinator** (`MockCoordinator`; P4 may choose other policies,
but must never leave an agent `bid_pending`):

| Failure | When | Reference behaviour |
|---|---|---|
| Market or dispatch failure, contract violation | before realised demand is revealed | `on_failure="settle_zero"`: every agent settles with a zero allocation. `"raise"`: every bid is aborted, then the error is re-raised; environments have not advanced, so the same slot can be re-run |
| Settlement failure (`env.step()` or `agent.settle` raises, e.g. demand above capacity) | after realised demand | Never swallowed. All other agents are settled normally. Each failed agent's bid is aborted and the slot is reported to it with a carried-forward observation flagged `{"imputed": True}` (no settlement is fabricated). The step is recorded with `settlement_failures`, then `SettlementError` is raised, chained to the original error |

After either path, every agent is `observed` or `settled` and synchronised on the same slot, so the
next cycle runs normally (tested in `tests/unit/test_coordinator.py`).

---

## Stability promise

* Contract version 2.0: field names, units and invariants of `Bid` (schema 1.1), `Allocation`,
  `Settlement`, `SupplyOffer`, `DispatchRequest`, `DispatchResult`, `ClearingResult`, `BidContext`,
  `Observation`, `DemandState` and `TimeSlot`, and the four protocols.
* New optional fields may be added. Breaking changes bump the version and are announced first.
* Everything in `gridweave.mocks` is a test double and may change freely.
