# P3 Integration Guide

Person 4 can instantiate P3 without importing source-agent internals:

```python
from gridweave.supply.provider import CampusSupplyProvider

provider = CampusSupplyProvider.from_config({
    "sources": [
        {"type": "grid", "source_id": "grid_main", "nominal_capacity_kw": 350.0},
        {"type": "solar", "source_id": "solar_roof", "installed_capacity_kw": 160.0},
        {"type": "battery", "source_id": "battery_main", "capacity_kwh": 120.0,
         "initial_soc": 0.70},
    ]
})
offers = provider.offers(slot)
clearing = auction.clear(slot, bids, offers)
results = provider.dispatch(clearing.dispatch)
```

P1 demand forecasts can be adapted through its public `demand_outlook(horizon)` method; P3 does not import private building-agent state. P2 consumes the shared `SupplyOffer` and produces shared `DispatchRequest` models. P4 owns sequencing, settlement, re-auction policy, and dashboards.

For events, use `provider.events` methods such as `trigger_grid_outage`, `trigger_grid_restoration`, `trigger_solar_drop`, `trigger_tariff_spike`, `trigger_battery_derate`, `trigger_battery_outage`, `trigger_battery_restoration`, and `trigger_emergency_reserve_release`. Request fresh offers after each event. Use `snapshot()` for source state and accounting, `metrics()` for KPIs, and `reset()` for deterministic reruns.

The current repository contains a production `AuctionEngine`; `tests/integration/test_p3_integration.py` exercises the P2/P3 boundary. The P3 standalone demonstration is:

```bash
python examples/p3_supply_demo.py
```
