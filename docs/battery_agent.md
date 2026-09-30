# Battery Storage Agent

`BatteryStorageAgent` owns physical storage state. Power is measured in kW, stored energy in kWh, and each `TimeSlot` uses average power over `slot.hours`.

## State transition

For charging power $P_c$ and discharge power $P_d$:

$$E_{t+1}=E_t+P_c\Delta t\eta_c-P_d\Delta t/\eta_d$$

The agent clamps both actions to power limits and SOC bounds. It never charges and discharges in the same operation. Discharge offers preserve the active reserve; emergency release must be explicitly triggered through the event handler.

The configured reserve policy distinguishes physical minimum SOC, operational reserve, emergency reserve, and ordinary market-dispatchable energy. `CHARGE`, `DISCHARGE`, `IDLE`, `RESERVE`, and `UNAVAILABLE` are represented by `BatteryOperatingMode`.

## Lifecycle

- `get_offer(slot)` reports dispatchable discharge power without changing SOC.
- `dispatch(request)` executes a validated discharge and returns delivered power, remaining capacity, SOC, and efficiency loss.
- `charge(power_kw, slot)` is the internal charging path, used for surplus solar and for off-peak grid charging.
- `reset()` restores the deterministic initial state.

The marginal offer price includes the configured degradation cost. Charge and discharge histories support throughput and accounting metrics.

## Recharging

`CampusSupplyProvider` recharges batteries in two ways after each slot's market dispatch:

1. **Surplus solar**: solar generation the market did not use. On this campus this is rare, because solar is the cheapest source and the market uses all of it.
2. **Off-peak grid charging** (`grid_charge_off_peak`, default on; window `grid_charge_hours`, default 00:00–06:00): each battery charges from the grid's *spare* capacity (never displacing campus supply). It skips any slot where it discharged or is offline, and any slot where the grid is out.

Without (2) the battery emptied once on day 1 and offered 0 kW for the rest of every run.

Grid energy used for charging is recorded separately (`grid_to_battery_kwh`) and is not counted as energy delivered to campus. Its cost is included in total procurement cost. It counts toward the grid's physical import and peak. `co2_displaced_kg` excludes battery discharge that came from grid charging.

## Offer price

The battery offers at **wear cost plus the cost of the energy it holds**:

$$p_{offer} = c_{deg} + \frac{\bar c_{stored}}{\eta_d}$$

$\bar c_{stored}$ is the weighted-average cost per stored kWh of the dispatchable energy (above the physical minimum). It is updated on every charge: surplus solar costs 0, and grid energy costs the tariff. Discharge doesn't change it. The initial charge is valued at `initial_energy_cost_per_kwh` (default 0), so a battery that hasn't charged from the grid offers at its wear cost as before.

With the default tariffs, energy charged off-peak at 5/kWh is offered at about 7 + 5/(0.95 × 0.95) ≈ 12.5/kWh. The market therefore leaves it alone while the grid costs 10 and uses it at the 18/kWh evening peak, or during an outage when it is the only source. Before this change the battery offered at its wear cost alone, so the market spent grid-charged energy at 06:00 at a loss.
