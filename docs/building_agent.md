# The Building Agent

`gridweave.agents.BuildingAgent` is one class used for every building. Hostels, labs, academic blocks
and libraries differ only in their `BuildingSpec` (capacity, load ratios, importance, prices,
deferral limits) and `DemandProfile` (the environment's demand shape).

## PEAS (Review 1)

Each entry names where it lives in the code, so the table can be checked rather than taken on trust.

| | Building Agent | Where in code |
|---|---|---|
| **Performance** | Served share of realised demand (`stats.service_ratio`). Critical-shortfall events and energy, measured against **realised** critical demand. Curtailed and expired energy. Deferred energy recovered within its deadline. Unused allocation (over-forecast). Forecast MAE/RMSE on settled slots (`stats.forecast_mae_kw`). Energy cost | `AgentStatistics`, `Settlement` |
| **Environment** | The building's demand: synthetic, occupancy-shaped, time-of-day and weekday/weekend patterns, AR(1) noise, spikes. The campus market (P2, mocked), the supply side (P3, mocked), the coordinator (P4, mocked). Closed loop: settlements feed back into the environment (`apply_settlement`, optional rebound) and into the agent's deferred-energy queue | `simulation/`, `mocks/` |
| **Actuators** | Submit a `Bid` (requested, minimum, critical and flexible power, priority, willingness to pay). Revise it under scarcity by trimming flexible load (demand response). On settlement: decide how the granted power is used (critical first, then oldest deferred energy, then new flexible load), and whether unserved flexible load is deferred or curtailed | `generate_bid`, `settle` |
| **Sensors** | The realised demand of each slot (`Observation`). The coordinator's scarcity signal (`BidContext.scarcity`). The `Allocation` for its bid. Its own history of settlements | `observe`, `settle` |

**Honest scope of the actuators.** "Serve, defer, curtail" is a decision the agent records in its
settlement and deferred-energy queue. It is not device-level control. The simulator reflects it
through the agent's queue (deferred energy is part of later need) and the optional rebound of
curtailed load.

## Environment properties

| Property | Value | Why |
|---|---|---|
| Observable | **Partially** | The agent sees its own meter and the scarcity signal, not other buildings' bids or future demand. It must forecast its own next-slot demand |
| Deterministic? | **Stochastic** | Demand has AR(1) noise and spikes; the allocation depends on other agents' bids and on supply |
| Episodic? | **Sequential** | Deferred energy, deprivation and (optionally) rebound carry consequences into later slots |
| Static? | **Dynamic** | Demand evolves every 15-minute slot; supply changes with time of day and battery state |
| Discrete? | **Discrete time, continuous quantities** | 15-minute slots; power in kW, energy in kWh |
| Agents | **Multi-agent, competing for a shared resource** | Buildings compete for scarce supply through the market |
| Known? | **Known rules** | The market protocol and settlement rules are fixed and documented |

**Agent type:** a **model-based agent with parameterised decision rules.** It keeps internal state
(history, deferred-energy queue, deprivation). It uses a model (a statistical forecaster) to predict the
slot it bids for. Its decisions come from explicit, deterministic formulas: classification, priority,
willingness to pay, demand-response trimming and serving order. It does **not** optimise a utility
function, and it does **not** learn in the machine-learning sense.

It is still more than a simple reflex agent. It anticipates demand instead of bidding its current
reading. It carries deferred energy forward with deadlines. It escalates priority and price after
repeated shortages via its deprivation state.

## State

| State variable | Meaning |
|---|---|
| `history` | Bounded deque of realised `DemandSample`s (default 7 days), gap-free on the 15-min grid |
| `demand_state` | `DemandState` for the slot being bid on: current, predicted, backlog, desired, critical, flexible, minimum, maximum |
| `backlog` | FIFO queue of `DeferredEnergy(origin, energy_kwh, deadline)`. Capped at `max_backlog_kw × slot hours`. Entries expire after `max_deferral_slots` |
| `deprivation` | EWMA (α = `deprivation_alpha`) of `1 − served / realised need` over past settlements |
| `pending_bid` | The bid awaiting settlement, if any |
| `phase` | `idle → observed → bid_pending → settled` |
| `stats` | Cumulative, settlement-based energy accounting (see below) |

## Lifecycle

```
observe(obs)                  slots without a bid (warm-up); grid-aligned, contiguous timestamps only
generate_bid(context)         update_state(slot): forecast_demand() + classify_load()
                              calculate_priority() -> BidGenerator -> Bid
generate_bid(context')        same slot again: revision+1; with scarcity>0 the request is trimmed
receive_allocation(a)         optional ex-ante preview against the bid (no state change)
settle(a, realised_obs)       the slot happened: judge the allocation against REALISED demand
abort_bid(reason)             withdraw a bid (e.g. market failure) and return to observed
snapshot() / demand_outlook() observable state for P4, multi-slot outlook for P3
```

Wrong-phase calls raise `AgentStateError`: bidding before any observation, bidding for another slot
while one is pending, observing the pending slot instead of settling it, or settling without a bid.
An allocation for the wrong bid, building or slot raises `AllocationMismatchError`. Off-grid or
missing slots raise `MisalignedTimestampError` or `MissingSlotError`.

## Load model

See [demand_model.md](demand_model.md#load-classification):
`critical = min(base, max(minimum_operational_kw, critical_fraction·base))`. Deferred energy is always
flexible. `minimum = critical + min_flexible_fraction·(base − critical)`. At bid time `base` is the
forecast; at settlement it is the realised demand.

## Priority score ∈ [0, 1]

`priority = Σ wᵢ·fᵢ` with weights normalised to sum to 1 (configurable, default 0.35/0.25/0.20/0.20):

| Factor | Formula | Intuition |
|---|---|---|
| criticality | critical / base demand | How much of this slot's own demand is essential |
| importance | `spec.importance` | Static institutional importance (a policy input; P2/P4 may prefer to own it) |
| urgency | backlog / backlog limit | How much deferred energy is waiting |
| deprivation | EWMA state of the unserved share | Whether the building has recently been short of its realised need |

**Semantics:** 0 means the building can easily wait; 1 means it is essential and urgent right now. The
score is advisory for P2. Every bid carries the full breakdown in `bid.explanation["priority"]`.

## Willingness to pay

```
pressure = (wp·priority + ws·scarcity + wd·deprivation) / (wp + ws + wd)   ∈ [0, 1]
wtp      = base_price + (max_price − base_price) · pressure                ≤ max_price
```

This is a heuristic valuation, not a derived utility. Because it reuses the priority score, P2 should
not add `priority_score` and `willingness_to_pay` as if they were independent signals. Under extreme
scarcity every high-priority building saturates at its own `max_price`.

## Demand response under scarcity

When the coordinator signals `scarcity ∈ (0, 1]`, a revised bid trims flexible load:
`reduction = scarcity × scarcity_response × (requested − minimum)`, recorded as
`bid.voluntary_reduction_kw`. Critical and minimum power are never reduced. Energy that is not granted
is deferred or curtailed at settlement like any other unserved flexible load. In the mock market this
changes little (see `examples/closed_loop_demo.py`). Whether it creates value depends on how P2's
mechanism treats reductions.

## Settlement (the slot is judged against realised demand)

Given realised new demand `D` and the deferred energy `B` waiting at the start of the slot:

1. Expire queue entries past their deadline, counted as **expired**.
2. Classify `D` with the backlog: `need = min(capacity, D + B)`, with `critical` and `minimum` taken from `D`.
3. `served = min(allocated, need)`, and `unused = allocated − served` (over-forecast).
4. Critical first: `critical_served = min(served, critical)`. Any gap is a **critical shortfall**; it is
   reported, not queued.
5. Then oldest deferred energy (FIFO), then new flexible load.
6. Unserved new flexible load: `deferred = unserved × deferrable_fraction` (queued with deadline
   `slot + max_deferral_slots`); the rest is **curtailed**.
7. Cap the queue; any overflow is counted as **expired**.
8. Update the deprivation state: `α·(1 − served/need) + (1 − α)·deprivation`.

Status: `full` / `partial` (≥ minimum) / `below_minimum` (≥ critical) / `critical_shortfall` / `none`.

**Energy conservation.** Every kWh of realised demand ends in exactly one bucket:
`demand = served (same slot) + critical shortfall + curtailed + deferred`, and
`deferred = served later + expired + still queued`. `agent.energy_balance_kwh()` returns the
residual. It is checked on 40 random building configurations × 30 random settlements each, and on
every agent in the 3-day integration run.

## Who protects critical load

| Workstream | Responsibility for critical load |
|---|---|
| **P1** (this repo) | *Identifies* critical demand, requests it separately in every bid (`critical_power_kw`), and *detects and reports* critical shortfalls after settlement, against realised demand |
| **P2** | Decides whether critical demand is a hard constraint of market clearing |
| **P3** | Determines whether enough supply can physically be dispatched (and reserved) |
| **P4** | System-level response to shortfalls (events, re-auction, load-shedding policy) |

P1 cannot guarantee critical service. Even with ample supply, an under-forecast can leave realised
critical load unserved, because the market only allocates what was bid. The default 3-day mock run
reports 4 such events (0.91 kWh in total).

## Modelling assumptions

1. Time is discrete: 15-minute slots. Power values are slot averages. Every slot must be reported.
2. `Observation.measured_demand_kw` is the building's realised *new* demand (what it would draw if
   unconstrained). The energy actually delivered is `Settlement.served_kw`.
3. Deferred energy keeps its kWh and is re-requested in later slots until served or expired.
4. Deferred energy is always flexible; the comfort floor applies only to the slot's own demand.
5. The agent chooses its serving order (critical, then deferred, then new flexible). This assumes the
   building can switch its flexible loads.
6. Bid quantities are forecast-based. Whether truthful reporting is incentive-compatible depends on
   P2's mechanism.
7. The load split is parametric (fractions plus an operational floor), not a per-appliance model.
