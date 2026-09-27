# Demand model

## Synthetic demand generation

`DemandGenerator` produces one realistic 15-minute series per building:

```
activity(t) ∈ [0,1]   from the building's DemandProfile (weekday or weekend shape), smoothed
expected(t) = capacity · (base_fraction + (peak_fraction − base_fraction) · activity(t))
noise(t)    = φ·noise(t−1) + √(1−φ²)·σ·N(0,1)            AR(1): temporally correlated deviations
spike(t)    = with probability p: + magnitude·capacity·U(0.5,1) for d slots
demand(t)   = clip(expected(t)·(1+noise(t)) + spike(t), 0, capacity)
```

* **Time of day:** profiles are declared as readable activity periods, e.g. `(19.0, 23.0, 1.0)` means
  "19:00–23:00 at full activity". A circular moving average (`smoothing_slots`) turns steps into ramps.
* **Weekday vs weekend:** either a separate `weekend_periods` shape (hostel, library) or a
  `weekend_factor` scaling (lab 0.3, academic 0.1, admin 0.05).
* **Building type:** each type has its own built-in profile (`gridweave.simulation.profiles`). A custom
  profile can be given inline in the campus JSON, or a built-in one tweaked with `profile_overrides`.
* **Reproducibility:** each generator owns a `random.Random(seed)`. Per-building seeds are derived
  from the master seed and the building id via CRC32, so adding a building never changes another
  building's series.

### Built-in profiles (fractions of capacity)

| Type | Base | Peak | Weekday shape | Weekend | Noise σ | Spikes |
|---|---|---|---|---|---|---|
| hostel | 0.12 | 0.85 | low night, morning bump 06–09, low while students are in class, **evening peak 19–23** | own shape: occupied all day | 0.06 | 1 % |
| lab | 0.30 | 0.90 | always-on base (servers, fridges, fume hoods), **09:30–17:30 experiments** | ×0.3 | 0.04 | 2 %, 20 % |
| academic | 0.06 | 0.80 | **08:30–17:00 lectures**, lunch dip | ×0.1 | 0.05 | – |
| library | 0.10 | 0.70 | daytime, **17–22 study rush** | own shape | 0.04 | – |
| admin | 0.08 | 0.75 | 09–17:30 office hours | ×0.05 | 0.03 | – |

Run `python examples/generate_profiles.py` to see sparklines of every profile.

### Sample data

`data/sample/campus_demand_7d.csv` (long format: `timestamp,building_id,demand_kw`) contains
7 days (Mon–Sun) × 96 slots × 5 buildings = 3,360 rows for Hostel A/B/C, Engineering Lab and Academic
Block (seed 42). Regenerate it with `python scripts/generate_sample_data.py`. The default campus
peaks at about 440 kW aggregate, with a weekday median of about 345 kW.

## Load classification

For the target slot, with forecast `F`, backlog `B` and capacity `C`:

```
total    = min(C, F + B)                 desired demand
base     = min(F, total)                 the slot's own demand
critical = min(base, max(minimum_operational_kw, critical_fraction · base))
flexible = total − critical              (includes all backlog)
minimum  = critical + min_flexible_fraction · (base − critical)
```

Invariants (enforced by `LoadClassification` / `DemandState` validation and checked on 500 random
configurations in `test_invariants_hold_for_random_configurations`):

```
0 ≤ critical ≤ minimum ≤ desired ≤ capacity          critical + flexible = desired
```

| Parameter | Meaning | Example |
|---|---|---|
| `critical_fraction` | Share of demand that must never be curtailed (servers, safety lighting, refrigeration, lab equipment) | lab 0.70, hostel 0.35 |
| `flexible_fraction` | Accepted in config as `1 − critical_fraction`; both may be given if they sum to 1 | hostel 0.65 |
| `minimum_operational_kw` | Absolute critical floor, whatever the fraction | lab 35 kW |
| `min_flexible_fraction` | Comfort floor: part of flexible load the building won't give up (e.g. minimum HVAC) | academic 0.3 |
| `deferrable_fraction` | Share of unserved flexible load that is shifted (water heating, laundry, EV charging) rather than lost (HVAC comfort) | hostel 0.8, academic 0.4 |
