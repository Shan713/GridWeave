# GridWeave

**Multi-agent auction-based campus energy management and demand response simulation system.**

A campus of hostels, labs, academic blocks and libraries shares limited electricity from several
sources (grid, solar, battery). Each building is an autonomous agent. It forecasts its own demand,
separates critical from flexible load, and bids into a campus energy market. After each 15-minute
slot, the agent settles the market's allocation against the demand that actually occurred, defers or
curtails what could not be served, and carries the consequences into the next slot.

This repository contains **Workstream 1: Building Intelligence & Demand Management**, plus the
contracts, protocols and mocks that the other three workstreams build against.

| Workstream | Owner | Scope | Status |
|---|---|---|---|
| 1 | P1 | Building agents, demand simulation, forecasting, load classification, priority, bid generation, settlement, local response | **Implemented (this repo)** |
| 2 | P2 | Auction / market mechanism, bid ranking, pricing, allocation, critical-load constraint policy | Protocol + mock provided |
| 3 | P3 | Grid, solar and battery agents, supply offers, dispatch, state of charge | Protocol + mocks provided |
| 4 | P4 | Environment loop, events, re-auction policy, dashboard, global metrics | Protocol + reference loop provided |

## 1. Architecture

```
Observation ─► BuildingAgent (P1) ── Bid ──► Auction (P2) ◄── SupplyOffers ── Supply (P3)
      ▲             ▲                           │                    ▲
      │             └──── Allocation ◄──────────┤── DispatchRequests ┘ (DispatchResults, SOC)
      │                                         │
  Environment ◄── Settlement ◄── agent.settle(allocation, realised demand)     orchestrated by P4
```

* `gridweave.models`: immutable, self-validating data contracts shared by all workstreams.
* `gridweave.interfaces`: `Protocol`s `DemandAgent`, `Auctioneer`, `SupplyProvider`,
  `EnvironmentStream` (contract version 2.0). `gridweave.contracts` checks cross-party consistency.
* The core is **pure standard-library Python**: deterministic, seeded, with no LLM and no machine
  learning.

Details: [docs/architecture.md](docs/architecture.md). The design lessons taken from RescueSync are in
[docs/design_notes_rescuesync.md](docs/design_notes_rescuesync.md).

## 2. The Person 1 subsystem

```
history → forecast → critical/flexible split → priority → bid → [revised bid under scarcity]
      → market (P2) → allocation → realised demand → settlement (serve / defer / curtail) → next state
```

| Package | Responsibility |
|---|---|
| `models/` | `BuildingSpec`, `Observation`, `DemandState`, `BidContext`, `Bid`, `Allocation`, `Settlement`, `DeferredEnergy`, `SupplyOffer`, `DispatchRequest`, `DispatchResult`, `ClearingResult`, `TimeSlot` |
| `simulation/` | Building-type `DemandProfile`s, seeded synthetic `DemandGenerator`, `BuildingSimulator` (with feedback hook), CSV I/O |
| `forecasting/` | `BaseForecaster`: moving average, EWMA, seasonal naive, seasonal EWMA, fallback (statistical baselines); MAE/RMSE/MAPE; rolling backtest |
| `classification/` | `LoadClassifier`: critical, flexible and minimum load |
| `bidding/` | `PriorityModel` (explainable score in [0, 1]), `BidGenerator` + `PricingPolicy` |
| `agents/` | `BuildingAgent`: lifecycle, settlement against realised demand, deferred-energy queue, deprivation state |
| `config/`, `factory.py` | Typed JSON campus config (packaged default), `synthetic_campus(n)`, config → agents/simulators |
| `mocks/` | `MockAuctioneer`, `MockGrid`/`MockSolar`/`MockBattery`/`MockSupply`, `MockCoordinator`: **test doubles**, not P2/P3/P4's systems |

## 3. Setup

Requires Python ≥ 3.10. There are no runtime dependencies.

```bash
git clone https://github.com/Shan713/GridWeave.git
cd GridWeave
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

`requirements.txt` installs the package in editable mode with the `[dev]` extras (pytest, pytest-cov,
ruff). A plain `pip install .` also works; the default campus config ships as package data. On
Windows, activate with `.venv\Scripts\activate`. Optional environment variables are listed in
`.env.example`: `GRIDWEAVE_CONFIG`, `GRIDWEAVE_SEED` and `GRIDWEAVE_LOG_LEVEL`.

## 4. Running

```bash
python examples/basic_building.py            # one agent: bid, allocation, realised demand, settlement
python examples/mock_market_cycle.py         # one slot: offers -> bids -> revised bids -> clearing -> dispatch -> settlement
python examples/closed_loop_demo.py          # good vs poor forecast, with/without demand response
python examples/generate_profiles.py         # demand shape of every building type
python examples/generate_bid.py              # config-driven campus -> bids (the P2 wire format)
python scripts/run_building_simulation.py    # 5 buildings x 3 days, closed loop, mock grid+solar+battery
python scripts/run_forecast_experiment.py    # forecasting comparison on development and held-out seeds
python scripts/benchmark_scaling.py          # runtime and memory vs number of buildings
python scripts/generate_sample_data.py       # regenerate data/sample/
```

A minimal agent in code:

```python
from datetime import datetime, timedelta
from gridweave.agents import BuildingAgent
from gridweave.models import Allocation, BuildingSpec, Observation

spec = BuildingSpec("hostel_a", "Hostel A", "hostel", capacity_kw=120, critical_fraction=0.6)
agent = BuildingAgent(spec)
t0 = datetime(2026, 1, 5, 18, 0)
for i, kw in enumerate([34.0, 37.0, 39.5, 41.0]):
    agent.observe(Observation("hostel_a", t0 + i * timedelta(minutes=15), kw))

bid = agent.generate_bid()                        # forecast -> classify -> priority -> bid
allocation = Allocation(bid.bid_id, "hostel_a", bid.time_slot, 32.0)              # from the market (P2)
realised = Observation("hostel_a", bid.time_slot.start, 44.0)                     # what actually happened
settlement = agent.settle(allocation, realised)   # judged against realised demand, not the bid
print(settlement.status.value, settlement.forecast_error_kw, settlement.deferred_kw, agent.backlog_kw)
```

## 5. Data model

| Model | Key invariants (enforced on construction) |
|---|---|
| `BuildingSpec` | capacity > 0; `minimum_operational_kw ≤ capacity`; fractions and importance ∈ [0, 1]; `forecast_horizon ≥ 1`; `max_deferral_slots ≥ 1` |
| `TimeSlot`, `Observation` (in the agent) | start on the 15-min grid; observations contiguous (no missing slots) |
| `Bid` | `critical ≤ minimum ≤ requested`; `critical + flexible = requested`; priority, flexibility ∈ [0, 1]; `wtp ≤ max_price`; `requested ≤ capacity_kw` when `capacity_kw` is set (always, for agent bids) |
| `Settlement` | `served = min(allocated, realised need)`; `critical served + shortfall = realised critical`; `new flexible served + deferred + curtailed = realised new flexible` |
| `ClearingResult` + `validate_clearing` | one allocation per bid; allocation ≤ request; Σ allocated ≤ Σ offered; dispatch within offers; Σ dispatched = Σ allocated |
| `DispatchResult` | `delivered ≤ requested` |

## 6. Building Agent lifecycle

`observe → generate_bid (update_state, forecast, classify, priority) → [revised generate_bid] →
settle(allocation, realised) → snapshot`, with `abort_bid` for market failures. PEAS (mapped to
code), environment properties, the priority, price and demand-response formulas, settlement rules,
and who is responsible for critical load are in [docs/building_agent.md](docs/building_agent.md).
The demand and load model is in [docs/demand_model.md](docs/demand_model.md).

**Critical load.** P1 identifies critical demand, requests it separately, and detects and reports
critical shortfalls against realised demand. P1 does **not** guarantee critical service: that
depends on P2's clearing rules, P3's supply and P4's system response. The default 3-day mock run
reports 4 small critical-shortfall events (0.91 kWh), all caused by under-forecasting.

## 7. Forecasting

Statistical time-series baselines behind one interface, evaluated by rolling-origin backtest on
held-out seeds (101–105) of the synthetic data. Model selection used seeds 42–44.

| Method | MAE h=1 (15 min) | MAE h=4 (1 h) | RMSE h=4 |
|---|---:|---:|---:|
| EWMA (α=0.6) | **4.74** | 7.81 | 12.64 |
| Seasonal EWMA (default) | 5.04 | **5.89** | **9.31** |
| Moving average (4) | 6.50 | 9.33 | 14.82 |
| Seasonal naive | 8.16 | 8.19 | 15.81 |
| *noise-free generator template (oracle)* | 3.01 | 3.01 | 4.66 |

These results hold on **this synthetic dataset**, whose repeating daily template favours seasonal
methods. They are not a general claim about which model is best. Full tables and limitations:
[docs/forecasting.md](docs/forecasting.md).

## 8. Contracts

* Bid: [docs/bid_contract.md](docs/bid_contract.md): fields, units, invariants and their limits,
  wire format, the `ClearingResult` reply, and the P1/P2 boundary.
* Everything for P2, P3 and P4: [docs/integration_contract.md](docs/integration_contract.md), with
  runnable code for each role.
* Sample bids, offers and a clearing result: `data/sample/`.

## 9. Testing

```bash
pytest                                   # 292 tests: unit + integration + runnable doc examples
pytest --cov=gridweave                   # coverage
ruff check src tests scripts examples    # lint
```

The tests cover:
- model validation, and the 15-minute grid (off-grid and missing slots rejected);
- every forecaster, including on irregular input and held-out seeds;
- load-classification boundaries, bid generation (including capacity), and supply/dispatch/clearing
  contracts;
- settlement against realised demand (allocation below, equal to and above actual; critical and
  flexible shortfall; deferral, expiry and FIFO order), and an energy-conservation property on
  random cases;
- coordinator failure handling (over-allocation, duplicate or missing allocations, auction exceptions
  and recovery, under-delivering sources);
- closed-loop integration: forecast error reaches outcomes, allocation changes future state, deferred
  load re-enters demand, rebound, battery SOC;
- reproducibility, and runs with 10–100 buildings.

All randomness is seeded. CI runs lint and tests on Python 3.10, 3.11, 3.12 and 3.13.

## 10. Integration guide for teammates

**Start with [docs/integration_contract.md](docs/integration_contract.md).**

* **P2 (market):** implement `clear(slot, bids, offers) -> ClearingResult` (allocations + dispatch).
  Test against `data/sample/sample_bids.json` and `validate_clearing`.
* **P3 (supply):** implement `offers(slot) -> [SupplyOffer]` and
  `dispatch(requests) -> [DispatchResult]` (update SOC). `MockBattery` shows the minimum.
* **P4 (coordinator):** drive `observe → generate_bid → offers → [revised bids] → clear → dispatch →
  settle(allocation, realised) → apply_settlement`. `MockCoordinator` is the validated reference loop.

## 11. Scalability (measured, not proven)

The current sequential, single-process simulation loop was run with up to 500 building agents
(`scripts/benchmark_scaling.py`). Runtime grew linearly (about 0.2 ms per agent per 15-minute
cycle on an arm64 laptop, Python 3.13), and per-agent memory is bounded. There is no parallel or
distributed execution. See [docs/architecture.md](docs/architecture.md#scalability-what-has-actually-been-measured).

## 12. Known limitations

* Demand data is **synthetic** (hand-designed timetables with AR(1) noise), not measured. It favours
  seasonal forecasters.
* Forecasters are statistical baselines. The agent's adaptation is an EWMA deprivation state and a
  deferred-energy queue, not machine learning.
* The market, supply and coordinator are mocks. In the mock market, voluntary demand response changes
  outcomes very little; its value depends on P2's mechanism.
* Critical-load protection is not guaranteed by P1 (see section 6).
* Load classification is parametric (fractions plus an operational floor), not a per-appliance model.
  Rebound is a single fraction, not a thermal model.
* Forecast confidence is heuristic and unused in decisions. Spike detection only flags and counts.

## Repository layout

```
src/gridweave/   core package (see table above)        data/sample/   committed synthetic samples
tests/           unit/ + integration/                  docs/          architecture, agent, demand,
examples/        runnable walkthroughs                                forecasting, bid + integration
scripts/         simulation, experiment, benchmark,                   contracts, RescueSync note
                 sample-data generation
```

## License

MIT. See [LICENSE](LICENSE).
