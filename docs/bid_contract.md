# Bid contract (schema version 1.0)

`gridweave.models.Bid` is the stable interface between Building Agents (P1) and the auction (P2).
**P1 decides what a building asks for and what it is worth to it. P2 decides how bids compete.**

## Fields

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `bid_id` | str | – | `"{building_id}:{YYYYMMDDTHHMM}:r{revision}"`. Deterministic and unique per building, slot and revision |
| `building_id` | str | – | Bidder |
| `time_slot` | `TimeSlot(start, duration_minutes)` | – | Delivery interval `[start, start + duration)`. Default 15 min |
| `created_at` | datetime | – | Decision time (the agent's latest observation) |
| `requested_power_kw` | float | kW (slot average) | Everything the building wants: forecast plus deferred backlog, capped at capacity |
| `minimum_power_kw` | float | kW | Lowest acceptable service: critical plus comfort floor |
| `critical_power_kw` | float | kW | Must-serve load (safety, servers, lab equipment). Below this is a safety event |
| `flexible_power_kw` | float | kW | `requested − critical`. Can be deferred or curtailed |
| `priority_score` | float | [0, 1] | Explainable urgency and importance (0 = can wait, 1 = essential and urgent). See [building_agent.md](building_agent.md#priority-score--0-1) |
| `flexibility_score` | float | [0, 1] | `(requested − minimum) / requested`: share of the request it can give up |
| `willingness_to_pay` | float | currency/kWh | The building's valuation this slot. Rises with priority, scarcity and deprivation |
| `maximum_price` | float | currency/kWh | Hard cap. The building never values energy above this |
| `revision` | int ≥ 0 | – | 0 for the first bid. +1 each time the agent re-bids the same slot (negotiation rounds) |
| `explanation` | dict | – | Non-contractual audit data: priority factors, weights and contributions, pricing pressure, demand state, forecast method and confidence |
| `schema_version` | str | – | `"1.0"`. Bumped on breaking changes |

Energy of a slot: `bid.requested_energy_kwh = requested_power_kw × slot.hours`.
Convenience: `bid.curtailable_power_kw = requested − minimum`.

## Guaranteed invariants

These are checked in `Bid.__post_init__`, so a `Bid` object that exists satisfies all of them. P2 may
rely on them without re-checking.

```
0 ≤ critical_power_kw ≤ minimum_power_kw ≤ requested_power_kw ≤ building capacity
critical_power_kw + flexible_power_kw == requested_power_kw          (±1e-6 kW)
0 ≤ priority_score ≤ 1,  0 ≤ flexibility_score ≤ 1
0 ≤ willingness_to_pay ≤ maximum_price
all numbers finite
```

Bids are immutable (frozen dataclasses) and safe to share, cache or hash-compare.

## Wire format

`bid.to_dict()` / `Bid.from_dict(d)` round-trip losslessly through JSON. This is a real bid from
`data/sample/sample_bids.json` (Engineering Lab, Monday 20:00; numbers rounded for display):

```json
{
  "schema_version": "1.0",
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
  "explanation": {
    "priority": {"score": 0.47,
                 "factors": {"criticality": 0.7, "importance": 0.9, "urgency": 0.0, "deprivation": 0.0},
                 "weights": {"criticality": 0.35, "importance": 0.25, "urgency": 0.2, "deprivation": 0.2},
                 "contributions": {"criticality": 0.245, "importance": 0.225, "urgency": 0.0, "deprivation": 0.0}},
    "pricing": {"base_price": 7.0, "max_price": 14.0, "scarcity": 0.0, "deprivation": 0.0, "pressure": 0.235},
    "demand": {"...": "DemandState.to_dict()"},
    "forecast": {"method": "ewma", "confidence": 0.8948}
  }
}
```

(The forecast method shows `ewma` because on Monday evening the seasonal model doesn't yet have a day
of history, so the fallback forecaster is used.)

## Allocation (the reply)

P2 answers each bid with one `gridweave.models.Allocation`:

| Field | Required | Meaning |
|---|---|---|
| `bid_id`, `building_id`, `time_slot` | yes | Must echo the bid exactly. Otherwise the agent raises `AllocationMismatchError` |
| `allocated_power_kw` | yes | ≥ 0. Values above the request are allowed; the excess is reported as `unused_allocation_kw` |
| `clearing_price` | no | currency/kWh. Used to compute `energy_cost` |
| `supply_mix` | no | e.g. `{"grid": 50, "solar": 20, "battery": 9.4}`. Must sum to `allocated_power_kw` if given |
| `metadata` | no | Free-form, e.g. mechanism name or clearing round |

## What P2 owns (and P1 must not do)

* Bid ranking, scoring and tie-breaking; the clearing and pricing rule (uniform, pay-as-bid, VCG, ...);
  optimisation.
* Deciding whether `priority_score`, `willingness_to_pay`, `critical_power_kw` or `flexibility_score`
  matter, and how much.
* Guaranteeing `Σ allocated ≤ available supply`.

The `MockAuctioneer` (critical tier pro rata, then minimum tier, then flexible greedy by priority,
pay-as-bid) exists only for demos and tests. It is not a proposal for the real mechanism.

## Versioning

Adding optional fields is backward-compatible and keeps `1.0`. Renaming or removing fields, or
changing units or semantics, is a breaking change: bump `BID_SCHEMA_VERSION` and announce it to the team.
