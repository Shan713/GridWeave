# The Building Agent

`gridweave.agents.BuildingAgent` is one class used for every building. Hostels, labs, academic blocks
and libraries differ only in their `BuildingSpec` (capacity, load ratios, importance, prices) and
`DemandProfile` (the environment's demand shape).

## PEAS (Review 1)

| | Building Agent |
|---|---|
| **Performance** | Critical load always served (zero critical-shortfall events). High service ratio (served / requested energy). Low curtailed energy. Deferred demand recovered quickly (small backlog). Low energy cost. Accurate forecasts (MAE/RMSE) |
| **Environment** | The building's electrical load (occupancy-driven demand following time-of-day and weekday/weekend patterns, with noise and spikes), the campus energy market (auction, P2), the supply side (grid/solar/battery, P3) and the coordinator (P4) |
| **Actuators** | Submit or revise a `Bid` (requested, minimum, critical, flexible power, priority, willingness to pay). Apply an allocation: serve critical load, serve, defer or curtail flexible load |
| **Sensors** | Metered demand each slot (`Observation`). Market context (`BidContext`: slot, scarcity). The `Allocation` received. Its own history of allocations (deprivation) |

## Environment properties

| Property | Value | Why |
|---|---|---|
| Observable | **Partially** | The agent sees its own meter and the scarcity signal, not other buildings' bids or the true future demand |
| Deterministic? | **Stochastic** | Demand has AR(1) noise and random spikes. The allocation depends on other agents' bids |
| Episodic? | **Sequential** | Deferred load becomes the next slot's backlog. Deprivation accumulates over slots |
| Static? | **Dynamic** | Demand evolves each 15-minute slot, and supply can drop (shortage windows) |
| Discrete? | **Discrete time, continuous quantities** | 15-minute slots; power in kW |
| Agents | **Multi-agent, competitive for a shared resource**, cooperative at system level | Buildings compete for scarce supply through the auction. Truthful bidding and priority serve the campus objective |
| Known? | **Known rules** | The market protocol and the allocation response rules are fixed and documented |

**Agent type:** a *model-based, utility-oriented* agent. It keeps internal state (history, backlog,
deprivation), uses a model (the forecaster) to predict the next slot, and trades off values (priority
and willingness to pay) instead of following fixed condition-action rules. A simple reflex agent that
bids its current reading would under-bid ahead of the evening ramp. It would also forget deferred load
and could not escalate after repeated shortages.

## State

| State variable | Meaning |
|---|---|
| `history` | Bounded deque of `DemandSample`s (default 7 days) |
| `demand_state` | `DemandState` for the slot being bid on: current, predicted, backlog, desired, critical, flexible, minimum, maximum |
| `backlog_kw` | Flexible demand deferred from earlier slots, capped at `max_backlog_kw` (default: capacity) |
| `deprivation` | EWMA (α = `deprivation_alpha`) of `1 − satisfaction_ratio` over past allocations |
| `pending_bid` | The bid awaiting allocation, if any |
| `phase` | `idle → observed → bid_pending → settled` |
| `stats` | Cumulative requested, served, deferred, curtailed and dropped energy, critical-shortfall events, cost, anomalies |

## Lifecycle and actions

```
observe(obs)                 validate building id and time order, spike check, append to history
generate_bid(context)        update_state(slot):
                                 forecast_demand()     BaseForecaster (strategy)
                                 classify_load()       LoadClassifier
                             calculate_priority()      PriorityModel
                             BidGenerator.generate()   → Bid (revision+1 if re-bidding same slot)
receive_allocation(a)        pure preview of the response (no state change)
apply_allocation(a)          serve / defer / curtail, update backlog, deprivation, stats
snapshot()                   JSON-serialisable state for P4 dashboards
demand_outlook(h)            multi-slot forecast + classification (for P3 planning)
```

Wrong-phase calls raise `AgentStateError`, for example applying an allocation with no pending bid, or
bidding for a new slot while one is unsettled. An allocation for the wrong bid, building or slot
raises `AllocationMismatchError`.

## Load model

See [demand_model.md](demand_model.md#load-classification). In short:
`critical = min(base, max(minimum_operational_kw, critical_fraction·base))`. Backlog is always flexible.
`minimum = critical + min_flexible_fraction·(base − critical)`.

## Priority score ∈ [0, 1]

`priority = Σ wᵢ·fᵢ` with weights normalised to sum to 1 (configurable, default 0.35/0.25/0.20/0.20):

| Factor | Formula | Intuition |
|---|---|---|
| criticality | critical / base demand | How much of this slot's demand is essential |
| importance | `spec.importance` | Static institutional importance (lab 0.9 > hostel 0.6 > admin 0.4) |
| urgency | backlog / backlog limit | How much postponed load is waiting |
| deprivation | EWMA of unserved share | "I have been short-changed recently" (historical learning) |

**Semantics:** 0 means the building can easily wait; 1 means it is essential and urgent right now. The
score is advisory for P2; the auction decides how to use it. Each bid carries the full breakdown in
`bid.explanation["priority"]`.

## Willingness to pay

```
pressure = (wp·priority + ws·scarcity + wd·deprivation) / (wp + ws + wd)   ∈ [0, 1]
wtp      = base_price + (max_price − base_price) · pressure                ≤ max_price
```

Scarcity comes from the coordinator (`BidContext.scarcity`). This is the adaptive-bidding feature:
shortage and deprivation raise the price the building offers. They never inflate the physical
quantities it requests.

## Allocation response

Given `accepted = min(allocated, requested)`:

1. `critical_served = min(accepted, critical)`, and any shortfall is a safety event.
2. `flexible_served = min(accepted − critical_served, flexible)`.
3. Unserved flexible load is split: `deferred = unserved·deferrable_fraction`, and the rest is `curtailed`.
4. `unused = allocated − accepted`.
5. `backlog ← (backlog not included this slot) + deferred`, capped at the limit. Overflow is counted as dropped.
6. `deprivation ← α·(1 − accepted/requested) + (1 − α)·deprivation`.

Status: `full` / `partial` (≥ minimum) / `below_minimum` (≥ critical) / `critical_shortfall` / `none`.

Worked example from the brief (requested 40, critical 25, flexible 15, allocated 32):
critical served 25, flexible served 7, deferred 8. This is covered by `test_brief_example_partial_allocation`.

## Modelling assumptions

1. Time is discrete: 15-minute slots. Power values are slot averages.
2. The agent bids for the next slot. Its outcome is evaluated against its bid, not against the demand
   later realised in that slot.
3. Deferred load keeps its kW magnitude and is re-requested in the next slot (equal slot lengths
   conserve energy).
4. Backlog is always flexible, and the comfort floor applies only to the slot's own demand.
5. Bidding is truthful in quantities. Strategy is expressed only through price.
6. The load split is parametric (fractions plus an operational floor). It is not a device-level
   appliance model.
