# GridWeave: Person 2 Integration Guide (For P1, P3, and P4)

This document provides exact integration instructions for teammates connecting to the Person 2 Auction & Market Subsystem.

---

## 1. Integration with Person 1 (Building Intelligence)

Person 2 **directly consumes** P1's frozen models (`gridweave.models`, Contract v2.0):
* `Bid`: The auction takes standard P1 `Bid` objects (Schema 1.1).
* `Allocation`: The auction returns standard P1 `Allocation` objects with `allocated_power_kw`, `clearing_price`, and `supply_mix`.
* `BuildingAgent`: Building agents directly receive P2 allocations via `agent.settle(allocation, realised_observation)`.

### Code Example:
```python
from gridweave.auction import AuctionEngine
from gridweave.models import BidContext

# P1 agent generates standard bid
bid = agent.generate_bid(BidContext(slot))

# P2 auction clears the slot
clearing = engine.clear(slot, [bid], offers)
alloc = clearing.allocations[0]

# P1 agent settles ex-post against realised observation
settlement = agent.settle(alloc, realised_observation)
assert agent.energy_balance_kwh() == 0.0
```

---

## 2. Integration with Person 3 (Supply Provider: Grid, Solar, Battery)

Person 3 provides available power generation and storage capacity to the market.

### What P3 Must Provide to P2:
A sequence of `SupplyOffer` objects for the clearing slot:
```python
from gridweave.models import SourceType, SupplyOffer

offers = [
    SupplyOffer(source_id="grid", source_type=SourceType.GRID, time_slot=slot, available_kw=100.0, marginal_price=10.0),
    SupplyOffer(source_id="solar", source_type=SourceType.SOLAR, time_slot=slot, available_kw=30.0, marginal_price=0.0),
    SupplyOffer(source_id="battery", source_type=SourceType.BATTERY, time_slot=slot, available_kw=20.0, marginal_price=7.0, constraints={"soc": 0.85}),
]
```

### What P2 Returns to P3:
P2 produces `DispatchRequest` objects in `clearing.dispatch`:
```python
# Each dispatch request instructs source to deliver requested_kw
for req in clearing.dispatch:
    print(req.source_id, req.requested_kw)  # e.g. "solar", 30.0 kW

# P3 executes dispatch and updates internal state (e.g. Battery SOC)
results = supply.dispatch(clearing.dispatch)
```

### Guarantees P2 Provides to P3:
1. **Never Dispatches Unoffered Sources**: Dispatch requests are generated *only* for sources that submitted a valid `SupplyOffer`.
2. **Never Exceeds Available Capacity**: For every source $j$, `requested_kw` $\le$ `offer.available_kw`.
3. **Exact Energy Conservation**: $\sum \text{dispatched\_kw} == \sum \text{allocated\_kw}$.
4. **Merit Order**: Sources with lower `marginal_price` are always dispatched before expensive sources.

---

## 3. Integration with Person 4 (Campus Coordinator)

Person 4 orchestrates the simulation clock, environment events, and market clearing.
P2 provides **two complementary interfaces** for P4:

### Option A: Stateless Functional Interface (`Auctioneer` Protocol)
```python
from gridweave.auction import AuctionEngine, OptimizedAllocationStrategy
from gridweave.contracts import validate_clearing

engine = AuctionEngine(strategy=OptimizedAllocationStrategy())

# Call clear each slot
clearing = engine.clear(time_slot=slot, bids=list(bids.values()), offers=offers)

# P4 validates contract consistency
validate_clearing(clearing, list(bids.values()), offers)
```

### Option B: Stateful Market Lifecycle Interface
For dynamic environments with multi-round negotiation or dynamic shortages:
```python
# 1. Open market for slot
engine.open_market(slot)

# 2. Collect bids and offers
for bid in bids.values():
    engine.submit_bid(bid)
for offer in offers:
    engine.submit_offer(offer)

# 3. Clear market
result = engine.clear_market()

# 4. If supply changes dynamically (e.g. cloud cover or generator trip):
reduced_offers = supply.offers(slot)
new_result = engine.re_auction(new_offers=reduced_offers)

# 5. Access rich audit metadata and explainability traces for dashboard:
trace = new_result.get_trace("hostel_a")
print(trace.reason)
```

---

## 4. Contract Verification

Every clearing result returned by `AuctionEngine` automatically satisfies `gridweave.contracts.validate_clearing`:
* Exactly one allocation per bid.
* No allocation exceeds requested power.
* Critical demands are protected whenever total supply $\ge$ total critical.
* Energy conservation holds to within $10^{-6}$ kW.
