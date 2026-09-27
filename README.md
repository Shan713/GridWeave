# GridWeave

**Multi-agent auction-based campus energy management and demand response simulation system.**

A campus of hostels, labs, academic blocks and libraries shares limited electricity. Each building is
an autonomous agent. It forecasts its own demand, separates critical from flexible load, and bids into
a campus energy market. When supply is short, it defers or curtails flexible load while protecting
critical load.

This repository currently contains **Workstream 1: Building Intelligence & Demand Management**, plus
the contracts, protocols and mocks that the other three workstreams build against.

| Workstream | Owner | Scope | Status |
|---|---|---|---|
| 1 | P1 | Building agents, demand simulation, forecasting, load classification, priority, bid generation, allocation response | **Implemented (this repo)** |
| 2 | P2 | Auction / market mechanism, bid evaluation, allocation | Protocol + mock provided |
| 3 | P3 | Grid, solar and battery agents, supply modelling | Protocol + mock provided |
| 4 | P4 | Grid coordinator, dynamic environment, events, re-auction, dashboard | Protocol + reference loop provided |

## 1. Architecture

```
Observation ─► BuildingAgent (P1) ── Bid ──► Auctioneer (P2) ◄── supply kW ── SupplyProvider (P3)
                   ▲                              │
                   └────────── Allocation ────────┘          all orchestrated by the Coordinator (P4)
```

* `gridweave.models` holds the immutable, self-validating data contracts shared by all workstreams.
* `gridweave.interfaces` holds `Protocol`s: `DemandAgent`, `Auctioneer`, `SupplyProvider`, `EnvironmentStream`.
* The core is **pure standard-library Python**: deterministic, seeded and LLM-free. Frameworks
  (dashboards, LangGraph and similar) wrap it but are never the source of truth.

Details: [docs/architecture.md](docs/architecture.md). The design lessons taken from RescueSync are in
[docs/design_notes_rescuesync.md](docs/design_notes_rescuesync.md).

## 2. The Person 1 subsystem

```
historical demand → forecasting → critical/flexible split → demand state → priority → bid
      → auction (P2) → allocation → local response (serve/defer/curtail) → updated state
```

| Package | Responsibility |
|---|---|
| `models/` | `BuildingSpec`, `Observation`, `DemandSample`, `DemandState`, `LoadClassification`, `BidContext`, `Bid`, `Allocation`, `AllocationOutcome`, `TimeSlot` |
| `simulation/` | Building-type `DemandProfile`s, seeded `DemandGenerator` (time-of-day, weekday/weekend, AR(1) noise, spikes), `BuildingSimulator`, CSV I/O |
| `forecasting/` | `BaseForecaster`; moving average, EWMA, seasonal naive, seasonal EWMA, fallback; MAE/RMSE/MAPE; rolling backtest; spike detector |
| `classification/` | `LoadClassifier`: critical, flexible and minimum load |
| `bidding/` | `PriorityModel` (explainable score in [0, 1]), `BidGenerator` + `PricingPolicy` |
| `agents/` | `BuildingAgent`: lifecycle, state, backlog, learning; allocation response |
| `config/`, `factory.py` | Typed JSON campus config, `synthetic_campus(n)`, config → agents/simulators |
| `mocks/` | `MockAuctioneer`, `MockGrid`, `MockCoordinator`, for standalone demos (**not** production) |

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
ruff). On Windows, activate with `.venv\Scripts\activate`. Optional environment variables are listed
in `.env.example`: `GRIDWEAVE_CONFIG`, `GRIDWEAVE_SEED` and `GRIDWEAVE_LOG_LEVEL`.

## 4. Running

```bash
python examples/basic_building.py            # one agent, one market cycle, explained
python examples/generate_profiles.py         # demand shape of every building type
python examples/generate_bid.py              # config-driven campus -> bids (the P2 wire format)
python examples/mock_market_cycle.py         # full loop written out step by step
python scripts/run_building_simulation.py    # 5 buildings x 3 days vs mock auction and grid
python scripts/run_building_simulation.py --buildings 100 --steps 96   # scalability
python scripts/run_forecast_experiment.py    # forecasting comparison (MAE / RMSE / MAPE)
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

bid = agent.generate_bid()                         # forecast -> classify -> priority -> bid
outcome = agent.apply_allocation(Allocation(bid.bid_id, "hostel_a", bid.time_slot, 32.0))
print(outcome.status.value, outcome.flexible_deferred_kw, agent.backlog_kw)
```

## 5. Data model

| Model | Key invariants (enforced on construction) |
|---|---|
| `BuildingSpec` | capacity > 0; `minimum_operational_kw ≤ capacity`; fractions and importance ∈ [0, 1]; `forecast_horizon ≥ 1`; `base_price ≤ max_price` |
| `DemandState` | `critical ≤ minimum ≤ desired ≤ maximum (capacity)`; `critical + flexible = desired`; all ≥ 0 |
| `Bid` | `critical ≤ minimum ≤ requested`; `critical + flexible = requested`; priority, flexibility ∈ [0, 1]; `wtp ≤ max_price` |
| `Allocation` | `allocated ≥ 0`; `supply_mix` sums to `allocated` |
| `AllocationOutcome` | conservation: `critical_served + shortfall = critical`, `flex_served + deferred + curtailed = flexible`, `served = min(allocated, requested)` |

All models are frozen dataclasses with `to_dict()` for JSON. `Bid` and `Allocation` also have `from_dict()`.

## 6. Building Agent lifecycle

`observe → update_state (forecast_demand, classify_load) → calculate_priority → generate_bid →
receive_allocation / apply_allocation → snapshot`. Re-bidding the same slot produces a revision,
which is the hook for re-auctions. PEAS, environment properties, the priority and price formulas,
and the allocation-response rules are in [docs/building_agent.md](docs/building_agent.md). The demand
and load model is in [docs/demand_model.md](docs/demand_model.md).

## 7. Forecasting

Four interchangeable forecasters behind one interface, evaluated by rolling-origin backtest on
5 buildings × 7 days × 3 seeds (480 origins per series):

| Method | MAE h=1 (15 min) | MAE h=4 (1 h) | RMSE h=4 |
|---|---:|---:|---:|
| EWMA (α=0.6) | **4.68** | 7.74 | 12.64 |
| Seasonal EWMA (default) | 4.94 | **5.85** | **9.50** |
| Moving average (4) | 6.44 | 9.27 | 14.87 |
| Seasonal naive | 8.23 | 8.25 | 16.04 |

Full tables, interpretation and limitations: [docs/forecasting.md](docs/forecasting.md).

## 8. Bid contract

See [docs/bid_contract.md](docs/bid_contract.md): the fields, units, guaranteed invariants, the JSON
wire format, the `Allocation` reply, and the P1/P2 boundary. Real sample bids and allocations are in
`data/sample/`.

## 9. Testing

```bash
pytest                                   # 219 tests: unit + integration + doc examples
pytest --cov=gridweave                   # coverage
ruff check src tests scripts examples    # lint
```

The tests cover model validation (invalid demand, capacity, priority), every forecaster on constant,
changing, zero, noisy and insufficient history, load-classification boundaries, bid generation under
normal, high and low demand and extreme shortage, and allocation response (full, partial, critical
only, below minimum, zero). Domain invariants are checked on hundreds of seeded random cases. The
integration tests cover the end-to-end workflow, reproducibility and scaling to 100 buildings. All
randomness is seeded. CI runs the suite on every push (`.github/workflows/tests.yml`).

## 10. Integration guide for teammates

**Start here: [docs/integration_contract.md](docs/integration_contract.md).** It contains runnable code
for each role:

* **P2 (auction):** implement `submit_bid(bid)` and `clear(slot, supply_kw) -> [Allocation]`. Test
  with `data/sample/sample_bids.json` or inside `MockCoordinator`.
* **P3 (supply):** implement `available_power_kw(slot)`. Read demand via `agent.demand_state`,
  `agent.demand_outlook(h)`, `build_simulators(cfg)` or the sample CSV.
* **P4 (coordinator):** drive `observe → generate_bid(BidContext) → auction → apply_allocation`. Read
  `agent.snapshot()`. Use `MockCoordinator` as the reference loop.

## 11. Known limitations

* Demand data is synthetic. The profiles are plausible campus timetables, not calibrated to real
  meter data.
* An outcome is evaluated against the bid, not against the demand later realised in the slot.
  Forecast error does not yet feed back into served energy.
* Forecasting uses only daily seasonality (no weekly season, weather or timetable inputs), and the
  forecast confidence is a heuristic, not a calibrated probability.
* Load classification is parametric (fractions plus an operational floor), not a per-appliance model.
* The mock auction, grid and coordinator are deliberately naive stand-ins for P2, P3 and P4.

## Repository layout

```
src/gridweave/   core package (see table above)      configs/     campus JSON configuration
tests/           unit/ + integration/                 data/sample/ committed sample CSV / bids / allocations
docs/            architecture, agent, demand, forecasting, bid + integration contracts
examples/        runnable walkthroughs                scripts/     simulation, experiment, data generation
```

## License

MIT. See [LICENSE](LICENSE).
