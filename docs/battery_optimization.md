# Battery Control and Optimization

Two explainable strategies are provided in `gridweave.supply.optimization`.

## Rule-based baseline

`RuleBasedBatteryStrategy` charges from solar surplus when headroom exists, discharges during a deficit when the tariff or shortage policy justifies it, and otherwise remains idle. It respects the battery's active reserve and power limits.

## Forecast-aware strategy

`ForecastAwareBatteryStrategy` evaluates a bounded rolling horizon using predicted demand, forecast solar, tariff rates, grid capacity, SOC bounds, efficiency, degradation, shortage penalty, and terminal SOC penalty. It preserves energy when a higher future tariff and demand are visible, and discharges when current value is at least as high as future value. This is a deterministic heuristic scheduler, not a proof of global optimality.

The objective evaluated by `evaluate_schedule_cost` is:

$$C=C_{grid}+C_{degradation}+C_{shortage}+C_{terminal\ reserve}$$

The strategy returns a plan and decision reason; the caller still clips the first action to the live offer before creating a dispatch request. This separation keeps optimization advisory and physical constraints authoritative in the battery agent.
