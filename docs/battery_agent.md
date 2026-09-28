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
- `charge(power_kw, slot)` is the internal surplus-solar charging path.
- `reset()` restores the deterministic initial state.

The marginal offer price includes the configured degradation cost. Charge and discharge histories support throughput and accounting metrics.
