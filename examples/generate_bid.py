"""Config-driven example: build the default campus, replay a day, emit bids as JSON.

This is exactly what P2 receives. Run: python examples/generate_bid.py
"""
import json

from gridweave.config import load_campus_config
from gridweave.factory import build_agents, build_simulators

cfg = load_campus_config()
agents, sims = build_agents(cfg), build_simulators(cfg)

for _ in range(19 * 4 + 3):                          # replay Monday 00:00 .. 19:30
    for building_id, sim in sims.items():
        agents[building_id].observe(sim.step())

bids = [agent.generate_bid() for agent in agents.values()]
for bid in bids:
    print(f"{bid.building_id:<15} slot {bid.time_slot}  req {bid.requested_power_kw:6.1f} kW  "
          f"crit {bid.critical_power_kw:5.1f}  min {bid.minimum_power_kw:5.1f}  "
          f"prio {bid.priority_score:.3f}  wtp {bid.willingness_to_pay:5.2f}")

print("\nWire format of the first bid (Bid.to_dict()):")
payload = bids[0].to_dict()
payload["explanation"] = {k: "..." for k in payload["explanation"]}  # abbreviated for display
print(json.dumps(payload, indent=2))
