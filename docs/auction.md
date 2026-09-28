# GridWeave: Auction & Market Mechanism Subsystem (Person 2)

## 1. Subsystem Overview & Role

In GridWeave, **Person 2 (Market Mechanism & Allocation Engine)** connects autonomous Building Agents (Person 1) to available campus energy supplies (Person 3) under the orchestration of the Campus Coordinator (Person 4).

```
                     ┌─────────────────────────── P4 Coordinator ───────────────────────────┐
                     │                                                                       │
    P1 Building Agents ──Bid(slot, requested, crit, min, prio, wtp)──► P2 AuctionEngine     │
                     ▲                                                    │      ▲           │
                     │                                                    │      │ Offers    │
                     │                                                    ▼      │           │
                     └─────────────── Allocation(allocated_kw) ───────────┴─ P3 Supply       │
                                                                             grid/solar/bat  │
```

The auction subsystem solves the fundamental problem of **campus resource contention**:
when total building energy demand exceeds available campus generation and battery reserves, the auction deterministically decides how scarce power is allocated, ensuring **life-safety critical loads are strictly protected**, **operational minimums are prioritized**, and **social welfare and fairness are maximized**.

---

## 2. PEAS Formulation (Review 1 Rubric)

| Dimension | Specification |
|---|---|
| **Performance Measure (P)** | • Zero critical load shortfalls ($\text{Shortfall}_{\text{crit}} = 0$ whenever feasible)<br>• Maximized social welfare / utility of served demand<br>• High service equity (Jain's Fairness Index $J \ge 0.95$)<br>• Optimal supply utilization ($\sum x_i / S \to 1.0$)<br>• Sub-millisecond clearing latency ($< 1$ ms for campus scale) |
| **Environment (E)** | • Multi-agent discrete 15-minute time slots (`TimeSlot`)<br>• Constrained shared capacity with fluctuating renewable supply (solar, battery, grid)<br>• Heterogeneous autonomous facilities (hostels, research laboratories, academic blocks, libraries)<br>• Dynamic shortages and re-auction rounds |
| **Actuators (A)** | • `ClearingResult` containing exact `Allocation` per building<br>• Economic merit-order `DispatchRequest` per generation source<br>• Transparent, deterministic `DecisionTrace` rationale per building |
| **Sensors (S)** | • Validated `Bid` stream from P1 Building Agents<br>• Available `SupplyOffer` stream from P3 Supply Provider<br>• Historical multi-slot service records from `FairnessTracker` |

---

## 3. Why Multi-Agent Auction Over Centralized Control?

1. **Decentralized Local Knowledge**: Building agents possess private operational constraints, occupancy dynamics, and load deferral deadlines that a central entity cannot model accurately without massive telemetry overhead.
2. **Autonomous Valuation**: Facilities value electricity differently depending on immediate context (e.g., an ongoing chemistry experiment vs. unoccupied study halls). Bids allow agents to express strategic urgency without exposing raw sensor data.
3. **Transparent Coordination**: Auctions replace arbitrary administrative rationing with deterministic, explainable rules that buildings can anticipate and plan around (e.g., shifting deferrable loads to future off-peak slots).

---

## 4. Market Lifecycle State Machine

The `AuctionEngine` manages a rigorous state machine preventing race conditions, stale revisions, and out-of-order execution:

```
    IDLE ──► OPEN ──► COLLECTING_BIDS ──► VALIDATING ──► CLEARING ──► SETTLED
               ▲                                ▲                   │
               │                                └──── RE_AUCTION ───┘
               └────────────────────────────────────────────────────┘
```

1. **OPEN**: Coordinator initializes market for target `TimeSlot`.
2. **COLLECTING_BIDS**: Building agents submit initial bids; supply providers submit capacity offers.
3. **VALIDATING**: `BidValidator` enforces physical invariants ($0 \le \text{crit} \le \text{min} \le \text{req}$), numerical sanity (no NaN/inf), slot alignment, and revision order.
4. **CLEARING**: Selected strategy (`GreedyAllocationStrategy` or `OptimizedAllocationStrategy`) executes resource allocation and merit-order supply dispatch.
5. **SETTLED**: Returns validated `ClearingResult` and rich `MarketResult` with decision traces.
6. **RE_AUCTION**: When supply changes dynamically (e.g., solar cloud cover) or coordinator requests a demand response round, active bids and offers are re-cleared.

---

## 5. Architectural Components

The P2 subsystem is modularized into distinct, single-responsibility components under `gridweave.auction`:

* **`BidValidator` (`validator.py`)**: Comprehensive invariant checker and revision manager. Rejects malformed quantities and stale revisions while permitting newer revisions ($r_1 > r_0$).
* **`BidScorer` (`scoring.py`)**: Multi-criteria evaluation model computing explainable score breakdowns from criticality, priority, economic valuation, and fairness factors.
* **`AllocationStrategy` (`strategies.py`)**: Strategy protocol with four pluggable implementations:
  * `GreedyAllocationStrategy`: Tiered sequential allocation guaranteeing critical loads.
  * `OptimizedAllocationStrategy`: Exact bounded social welfare maximization knapsack optimizer.
  * `ProportionalAllocationStrategy`: Baseline uncoordinated rationing.
  * `PriorityAllocationStrategy`: Baseline single-dimension priority allocation.
* **`ConstraintValidator` & `EmergencyPolicy` (`constraints.py`)**: Enforces mathematical invariants and handles severe supply deficits through equitable critical rationing.
* **`FairnessTracker` (`fairness.py`)**: Multi-slot deprivation tracker computing Jain's Fairness Index and dynamic priority boosts.
* **`MarketMetricsCalculator` (`metrics.py`)**: Derives cost, utilization, shortfall, and runtime metrics.
* **`AuctionEngine` (`engine.py`)**: Main facade implementing the `Auctioneer` protocol.
