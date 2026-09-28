# Supply Offers and Dispatch

P3 exposes `CampusSupplyProvider`, which structurally implements `gridweave.interfaces.SupplyProvider`:

```python
offers(slot) -> Sequence[SupplyOffer]
dispatch(requests) -> Sequence[DispatchResult]
```

## Cycle

1. P4 requests offers for a slot.
2. P2 receives the offers and returns `ClearingResult.dispatch`.
3. P4 passes those `DispatchRequest` values to P3.
4. P3 validates and executes the batch, returning actual `DispatchResult` values.

Offers are pure queries. Dispatch requests must use the current offered slot, name a known source, be unique per source, and not exceed the accepted offer. A stale request, mixed-slot batch, duplicate settled slot, unknown source, or excessive request raises `ValidationError` or `DispatchExecutionError` before source mutation.

`SupplyDispatcher` pre-validates the complete batch. `SupplyAccountant` records grid import, solar generation/use/curtailment, battery charge/discharge/losses, delivery, and cost. `provider.snapshot()`, `provider.metrics()`, and `provider.reset()` are the P4 telemetry and lifecycle hooks.

Dispatch quantities are average kW over the slot. Convert to energy with `power_kw * slot.hours`.
