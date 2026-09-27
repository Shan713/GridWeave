# Bid contract (schema version 1.1)

`gridweave.models.Bid` is the interface between Building Agents (P1) and the market (P2).
**P1 decides what a building asks for and what it is worth to it. P2 decides how bids compete.**

A bid is a **forecast-based request**. What the building actually needed is only known after the slot
and is reported in a `Settlement` (see [building_agent.md](building_agent.md#settlement-the-slot-is-judged-against-realised-demand)).

## Fields

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `bid_id` | str | – | `"{building_id}:{YYYYMMDDTHHMM}:r{revision}"`. Deterministic and unique per building, slot and revision |
| `building_id` | str | – | Bidder |
| `time_slot` | `TimeSlot(start, duration_minutes)` | – | Delivery interval `[start, start + duration)`. Default 15 min; `start` must be on the slot grid |
| `created_at` | datetime | – | Decision time (the agent's latest observation) |
| `requested_power_kw` | float | kW (slot average) | Forecast demand plus valid deferred energy, capped at capacity, minus any voluntary reduction |
| `minimum_power_kw` | float | kW | Lowest acceptable service: critical load plus comfort floor |
| `critical_power_kw` | float | kW | Forecast must-serve load (safety, servers, lab equipment) |
| `flexible_power_kw` | float | kW | `requested − critical`. Can be deferred or curtailed |
| `priority_score` | float | [0, 1] | Explainable urgency and importance (0 = can wait, 1 = essential and urgent) |
| `flexibility_score` | float | [0, 1] | `(requested − minimum) / requested` |
| `willingness_to_pay` | float | currency/kWh | Heuristic valuation. Rises with priority, scarcity and deprivation. Partly derived from the same factors as `priority_score` |
| `maximum_price` | float | currency/kWh | Hard cap on the building's valuation |
| `revision` | int ≥ 0 | – | 0 for the first bid; +1 for each revision of the same slot (demand-response rounds) |
| `capacity_kw` | float or null | kW | The building's connection capacity. **Always set by `BuildingAgent`** (new in 1.1) |
| `voluntary_reduction_kw` | float ≥ 0 | kW | Flexible load the building offered to give up under scarcity (new in 1.1) |
| `explanation` | dict | – | Non-contractual audit data: priority breakdown, pricing pressure, demand state, forecast method and confidence |
| `schema_version` | str | – | `"1.1"` |

## Invariants checked on construction

`Bid.__post_init__` enforces these, so any `Bid` object that exists satisfies them:

```
0 ≤ critical_power_kw ≤ minimum_power_kw ≤ requested_power_kw
critical_power_kw + flexible_power_kw == requested_power_kw          (±1e-6 kW)
0 ≤ priority_score ≤ 1,  0 ≤ flexibility_score ≤ 1
0 ≤ willingness_to_pay ≤ maximum_price
requested_power_kw ≤ capacity_kw            only if capacity_kw is set (always true for agent-generated bids)
voluntary_reduction_kw ≥ 0;  all numbers finite
```

**Limits of these guarantees.**
- A `Bid` cannot know a building's capacity by itself, so hand-built bids without `capacity_kw` are not
  capacity-checked. P2 may reject such bids, or check them against its own registry.
- Frozen dataclasses stop accidental mutation, but not deliberate `object.__setattr__`.

## Wire format

`bid.to_dict()` / `Bid.from_dict(d)` round-trip losslessly through JSON. This is a real bid from
`data/sample/sample_bids.json` (Engineering Lab, Monday 20:00; synthetic data; numbers rounded for
display):

```json
{
  "schema_version": "1.1",
  "bid_id": "eng_lab:20260105T2000:r0",
  "building_id": "eng_lab",
  "time_slot": {"start": "2026-01-05T20:00:00", "duration_minutes": 15},
  "created_at": "2026-01-05T19:45:00",
  "requested_power_kw": 79.367,
  "minimum_power_kw": 60.319,
  "critical_power_kw": 55.557,
  "flexible_power_kw": 23.81,
  "priority_score": 0.47,
  "flexibility_score": 0.24,
  "willingness_to_pay": 8.645,
  "maximum_price": 14.0,
  "revision": 0,
  "capacity_kw": 150.0,
  "voluntary_reduction_kw": 0.0,
  "explanation": {
    "priority": {"score": 0.47, "factors": {"criticality": 0.7, "importance": 0.9, "urgency": 0.0, "deprivation": 0.0}},
    "pricing": {"base_price": 7.0, "max_price": 14.0, "scarcity": 0.0, "deprivation": 0.0, "pressure": 0.235},
    "voluntary_reduction_kw": 0.0,
    "demand": {"...": "DemandState.to_dict()"},
    "forecast": {"method": "ewma", "confidence": 0.8948}
  }
}
```

(The forecast method is `ewma` because on Monday evening the seasonal model does not yet have a
day of history, so the fallback is used.) `data/sample/sample_offers.json` and
`data/sample/sample_clearing.json` hold the matching mock supply offers and mock clearing.

## What P2 returns: `ClearingResult`

`Auctioneer.clear(time_slot, bids, offers) -> ClearingResult(time_slot, allocations, dispatch, clearing_price, metadata)`:

| Part | Rules (checked by `gridweave.contracts.validate_clearing`) |
|---|---|
| `allocations` | Exactly one `Allocation` per bid, echoing `bid_id`, `building_id` and `time_slot`. `0 ≤ allocated ≤ requested`. `supply_mix`, if given, sums to `allocated_power_kw` |
| `dispatch` | `DispatchRequest(source_id, time_slot, requested_kw)`, only to sources that offered and within each offer, at most one per source |
| totals | Σ allocated ≤ Σ offered supply; Σ dispatched = Σ allocated |

## What P2 owns (and P1 must not do)

* Bid ranking, scoring and tie-breaking; the clearing and pricing rule (uniform price, pay-as-bid, VCG, ...);
  allocation optimisation; merit-order dispatch across sources.
* Whether critical demand is a **hard constraint** of clearing, and how `priority_score`,
  `willingness_to_pay`, `flexibility_score` and `voluntary_reduction_kw` are used.

`MockAuctioneer` (critical tier pro rata, then minimum tier, then flexible greedy by priority,
merit-order dispatch, pay-as-bid) exists only for demos and tests. It is not a proposal for the real
mechanism.

## Versioning

1.1 added `capacity_kw` and `voluntary_reduction_kw`, both optional with defaults, so it is
backward-compatible with 1.0 JSON. Renaming or removing fields, or changing units or semantics, is a
breaking change: bump `BID_SCHEMA_VERSION` and announce it first.
