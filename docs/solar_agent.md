# Solar Energy Agent

`SolarEnergyAgent` models a configurable photovoltaic source and implements the source-side portion of the v2 supply contract.

## Model

`SolarProfile` uses a deterministic daylight curve between configurable sunrise and sunset, installed capacity, cloud attenuation, derating, inverter efficiency, and an optional seeded variability term. Generation is zero outside daylight and is bounded by installed capacity. Values are simulation assumptions, not calibrated campus measurements.

`SolarEnergyAgent.get_offer(slot)` is a pure query. It reports the current generation as `SupplyOffer.available_kw`; dispatch records delivered energy and curtailment. Cloud events are applied through `CampusSupplyProvider.events`, then the next offer reflects the changed condition.

## Forecasting

The common `SolarForecaster` interface has persistence, time-of-day, and weather-aware implementations. `rolling_origin_evaluation` evaluates held-out future points without using the target actual in the forecast and reports MAE and RMSE. Sudden cloud changes remain difficult because synthetic weather observations cannot predict an unannounced event.

## Example

```python
from gridweave.models.common import TimeSlot
from gridweave.supply.solar_agent import SolarEnergyAgent

solar = SolarEnergyAgent("roof", installed_capacity_kw=120, seed=7)
offer = solar.get_offer(TimeSlot(start, duration_minutes=15))
```

Use `provider.events.trigger_solar_drop(cloud_cover=0.85)` for a reproducible event and call `provider.offers(slot)` again to observe the revised supply.
