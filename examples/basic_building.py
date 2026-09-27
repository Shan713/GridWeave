"""Minimal example: one Building Agent, hand-fed observations, one settled market cycle.

Run: python examples/basic_building.py
"""
from datetime import datetime, timedelta

from gridweave.agents import BuildingAgent
from gridweave.models import Allocation, BuildingSpec, BuildingType, Observation

spec = BuildingSpec(
    building_id="hostel_a",
    name="Hostel A",
    building_type=BuildingType.HOSTEL,
    capacity_kw=120,
    minimum_operational_kw=10,
    critical_fraction=0.6,       # 60 % of demand is critical ...
    deferrable_fraction=1.0,     # ... and all unmet flexible load is deferred
)
agent = BuildingAgent(spec)      # default forecaster: EWMA (a statistical baseline, not ML)

# 1. observe: the environment reports realised demand every 15 minutes
t = datetime(2026, 1, 5, 18, 0)
for i, kw in enumerate([34.0, 37.0, 39.5, 41.0]):
    agent.observe(Observation("hostel_a", t + i * timedelta(minutes=15), kw))

# 2-6. forecast -> classify -> priority -> bid (one call)
bid = agent.generate_bid()
print(f"Bid {bid.bid_id} for slot {bid.time_slot}")
print(f"  requested {bid.requested_power_kw:.1f} kW  (critical {bid.critical_power_kw:.1f}, "
      f"flexible {bid.flexible_power_kw:.1f}, minimum {bid.minimum_power_kw:.1f}, capacity {bid.capacity_kw:.0f})")
print(f"  priority {bid.priority_score:.3f}  willingness to pay {bid.willingness_to_pay:.2f}/kWh "
      f"(max {bid.maximum_price:.2f})")
print(f"  priority factors: {bid.explanation['priority']['factors']}")

# 7. the market (P2) grants 32 kW; 8. the slot happens and demand turns out to be 44 kW
allocation = Allocation(bid.bid_id, "hostel_a", bid.time_slot, 32.0, clearing_price=7.5)
s = agent.settle(allocation, Observation("hostel_a", bid.time_slot.start, 44.0))
print(f"\nForecast {s.forecast_demand_kw:.1f} kW, actual {s.actual_demand_kw:.1f} kW "
      f"(error {s.forecast_error_kw:+.1f}), allocated {s.allocated_kw:.1f} kW -> status {s.status.value}")
print(f"  critical: needed {s.actual_critical_kw:.1f}, served {s.critical_served_kw:.1f}, "
      f"shortfall {s.critical_shortfall_kw:.1f} kW")
print(f"  flexible: served {s.new_flexible_served_kw:.1f}, deferred {s.deferred_kw:.1f}, "
      f"curtailed {s.curtailed_kw:.1f} kW")
print(f"  deferred energy queued: {agent.backlog_energy_kwh:.2f} kWh (must be served within "
      f"{spec.max_deferral_slots} slots); cost {s.energy_cost:.2f}")
