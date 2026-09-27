# Integration contract: how P2, P3 and P4 use the Building Intelligence subsystem

Everything below is importable today:

```bash
pip install -e ".[dev]"
```

All cross-team data types live in `gridweave.models`, and all cross-team interfaces are
`typing.Protocol`s in `gridweave.interfaces`. You never need to subclass P1 code. Any class with the
right methods fits, and `isinstance(obj, Auctioneer)` works at runtime. Every `python` block in this
file is executed by `tests/integration/test_docs_examples.py`, so the examples cannot silently go stale.

```
                 ┌──────── BidContext(slot, scarcity) ─────────┐
Observation      │                                             │
 ──────────► BuildingAgent ── Bid ──► Auctioneer (P2) ──┐   Coordinator (P4)
             ▲    (P1)                     ▲            │        │
             └──────── Allocation ─────────┼────────────┘        │
                                           └── available_supply_kw ◄── SupplyProvider (P3)
```

---

## For Person 2: auction and market

**You receive** `gridweave.models.Bid` objects. The fields, units and guaranteed invariants are in
[bid_contract.md](bid_contract.md).
**You implement** the `Auctioneer` protocol:

```python
from typing import Sequence
from gridweave.models import Allocation, Bid, TimeSlot

class MyAuction:
    def __init__(self):
        self.book: dict[TimeSlot, dict[str, Bid]] = {}

    def submit_bid(self, bid: Bid) -> None:
        # a higher revision from the same building replaces the earlier one
        self.book.setdefault(bid.time_slot, {})[bid.building_id] = bid

    def clear(self, time_slot: TimeSlot, available_supply_kw: float) -> Sequence[Allocation]:
        bids = self.book.pop(time_slot, {}).values()
        # ... your mechanism. Here: serve critical first, then by willingness to pay
        remaining, out = available_supply_kw, []
        for b in sorted(bids, key=lambda b: -b.willingness_to_pay):
            give = min(b.requested_power_kw, remaining)
            remaining -= give
            out.append(Allocation(b.bid_id, b.building_id, time_slot, give, clearing_price=b.willingness_to_pay))
        return out

from gridweave.interfaces import Auctioneer
assert isinstance(MyAuction(), Auctioneer)
```

**Rules your allocations must follow** (the agent enforces the first one):

1. Echo `bid_id`, `building_id` and `time_slot` exactly. Otherwise the agent raises `AllocationMismatchError`.
2. `allocated_power_kw ≥ 0`, and `Σ allocated ≤ available_supply_kw`.
3. If you fill in `supply_mix`, it must sum to `allocated_power_kw`.

**Testing your auction without P1's agents.** Load realistic bids straight from the sample file, or
generate fresh ones:

```python
import json
from gridweave.models import Bid

bids = [Bid.from_dict(d) for d in json.load(open("data/sample/sample_bids.json"))]
assert all(b.critical_power_kw <= b.minimum_power_kw <= b.requested_power_kw for b in bids)
```

To run your auction inside the full simulated loop, swap it for the mock:
`MockCoordinator(agents, sims, MyAuction(), MockGrid(420))` (see the P4 section). `tests/unit/test_mocks.py`
shows the property checks worth copying: supply never exceeded, no allocation above request, revisions
replace, critical tier first.

**Signals you may use:** `critical_power_kw` (hard need), `minimum_power_kw` (soft need),
`priority_score`, `willingness_to_pay` and `maximum_price` (valuation), `flexibility_score`, and
`revision` (negotiation round). How they are weighed is entirely your decision.

---

## For Person 3: energy supply (grid, solar, battery)

**You implement** the `SupplyProvider` protocol: how much power is available in a slot.

```python
from gridweave.models import TimeSlot

class CampusSupply:
    def __init__(self, grid_kw: float, solar_peak_kw: float):
        self.grid_kw, self.solar_peak_kw = grid_kw, solar_peak_kw

    def available_power_kw(self, time_slot: TimeSlot) -> float:
        hour = time_slot.start.hour + time_slot.start.minute / 60
        solar = self.solar_peak_kw * max(0.0, 1 - abs(hour - 12.5) / 5.5)   # toy bell curve
        return self.grid_kw + solar

from gridweave.interfaces import SupplyProvider
assert isinstance(CampusSupply(300, 80), SupplyProvider)
```

**What demand looks like, and how to get it:**

| Need | API | Returns |
|---|---|---|
| Next-slot demand of one building | `agent.update_state()` or `agent.demand_state` | `DemandState`: current, predicted, backlog, desired, critical, flexible, minimum and maximum kW |
| Multi-slot outlook (battery scheduling) | `agent.demand_outlook(horizon=8)` | `[DemandOutlookPoint(timestamp, predicted_demand_kw, confidence, classification)]` |
| Offline campus demand series | `build_simulators(cfg)` or `data/sample/campus_demand_7d.csv` | `DemandSample(timestamp, demand_kw)` per 15-min slot |
| Aggregate campus demand | `gridweave.simulation.total_demand(series)` | `list[DemandSample]` |

```python
from gridweave.config import load_campus_config
from gridweave.factory import build_agents, build_simulators
from gridweave.simulation import total_demand

cfg = load_campus_config()
agents, sims = build_agents(cfg), build_simulators(cfg)
for _ in range(8):                                   # replay the first 2 hours
    for building_id, sim in sims.items():
        agents[building_id].observe(sim.step())

outlook = agents["eng_lab"].demand_outlook(horizon=4)
for p in outlook:
    print(p.timestamp, round(p.predicted_demand_kw, 1), "critical", round(p.classification.critical_kw, 1))

campus = total_demand({k: s.series for k, s in sims.items()})    # 288 x 15-min aggregate samples
print("campus peak kW:", round(max(s.demand_kw for s in campus), 1))
```

**Meaning of critical and flexible load for supply planning.** `critical_kw` must be covered in every
slot; it is the load your battery reserve should protect. `flexible_kw` can be shifted: the agent
defers `deferrable_fraction` of any unserved flexible load into later slots (the backlog), which is
what demand response means here. `minimum_kw` is the lowest level the building accepts without a
comfort breach. See [demand_model.md](demand_model.md) for the formulas.

**Simulating other scenarios.** Change `configs/campus_default.json` (capacities, fractions, profiles),
or build `synthetic_campus(n)` for 10, 50 or 100+ buildings. `BuildingSimulator.demand_modifier`
lets you inject events such as a heat wave: `sim.demand_modifier = lambda ts, kw: kw * 1.2`.

---

## For Person 4: coordinator, environment and events

**You drive the lifecycle.** A Building Agent never acts on its own. One market cycle:

```python
from gridweave.config import load_campus_config
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockGrid
from gridweave.models import Allocation, BidContext

cfg = load_campus_config()
agents, sims = build_agents(cfg), build_simulators(cfg)
auction, supply = MockAuctioneer(), MockGrid(420, [(19, 22, 0.8)])   # swap in P2's / P3's classes

for building_id, sim in sims.items():                     # 1. environment -> observations
    agents[building_id].observe(sim.step())

slot = agents["hostel_a"].next_slot()                     # 2. the slot to trade
available = supply.available_power_kw(slot)
bids = {i: a.generate_bid(BidContext(slot)) for i, a in agents.items()}

requested = sum(b.requested_power_kw for b in bids.values())
scarcity = max(0.0, 1 - available / requested) if requested else 0.0
if scarcity > 0:                                          # 3. optional re-auction round
    bids = {i: a.generate_bid(BidContext(slot, scarcity=scarcity)) for i, a in agents.items()}

for b in bids.values():                                   # 4. market
    auction.submit_bid(b)
allocations = {a.building_id: a for a in auction.clear(slot, available)}

for i, agent in agents.items():                           # 5. every pending bid MUST be settled
    b = bids[i]
    outcome = agent.apply_allocation(allocations.get(i) or Allocation(b.bid_id, i, slot, 0.0))
    print(i, outcome.status.value, round(outcome.flexible_deferred_kw, 1))
```

`gridweave.mocks.MockCoordinator` is this loop packaged as a class (`run_step()` / `run(steps)` and
`summarise()`). Use it as the reference for the real coordinator.

| You want to... | Call | Notes |
|---|---|---|
| Feed a measurement | `agent.observe(Observation(building_id, ts, kw))` | Timestamps must strictly increase. Returns `True` if flagged as a spike |
| Trigger a decision cycle | `agent.generate_bid(BidContext(slot, scarcity))` | Default slot is `agent.next_slot()` |
| Run a re-auction or negotiation round | Call `generate_bid` again for the **same** slot | Returns revision +1, which replaces the pending bid |
| Send the auction result | `agent.apply_allocation(allocation)` | Returns an `AllocationOutcome`: served, deferred, curtailed, status, cost |
| Preview a result without committing | `agent.receive_allocation(allocation)` | No state change |
| Observe agent state | `agent.snapshot()` (JSON-ready), `agent.phase`, `agent.backlog_kw`, `agent.stats`, `agent.events` | For dashboards and logs |
| Detect safety events | `outcome.has_critical_shortfall`, `outcome.status` | `critical_shortfall` or `none` means critical load was not served |
| Inject environment events | `BuildingSimulator.demand_modifier`, or your own `EnvironmentStream` | e.g. heat wave, exam week, outage |

**Phase rules** (violations raise `AgentStateError`): you cannot bid before the first observation, you
cannot bid for a *different* slot while a bid is pending, and you cannot apply an allocation without
a pending bid. Settle every bid, even with a zero allocation, before moving to the next slot.

**Scarcity signal:** `BidContext.scarcity ∈ [0, 1]` is how the coordinator tells agents that supply
is short. The agent raises `willingness_to_pay` accordingly; it never changes the quantities it
requests. A reasonable definition is `max(0, 1 − available / requested)`.

---

## Stability promise

* Field names, units and invariants of `Bid`, `Allocation`, `AllocationOutcome`, `BidContext`,
  `Observation`, `DemandState` and `TimeSlot`, and the four protocols, are **frozen for schema 1.0**.
* New *optional* fields may be added. Breaking changes bump `BID_SCHEMA_VERSION` and are announced
  to the team first.
* Everything in `gridweave.mocks` is a test double and may change freely.
