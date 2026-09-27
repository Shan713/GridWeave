"""One market cycle written out step by step (what P4 will automate).

Run: python examples/mock_market_cycle.py
"""
from gridweave.config import load_campus_config
from gridweave.contracts import validate_clearing, validate_dispatch
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockSupply
from gridweave.models import BidContext

cfg = load_campus_config()
agents, envs = build_agents(cfg), build_simulators(cfg)
supply, auction = MockSupply.from_config(cfg.supply), MockAuctioneer()

for _ in range(20 * 4 + 1):                               # observe Monday 00:00 .. 20:00 (no market)
    for building_id, env in envs.items():
        agents[building_id].observe(env.step())

slot = agents["hostel_a"].next_slot()                     # the 20:15 slot
offers = supply.offers(slot)                              # 1. P3 offers
available = sum(o.available_kw for o in offers)
bids = {i: a.generate_bid(BidContext(slot)) for i, a in agents.items()}              # 2. P1 bids
first = sum(b.requested_power_kw for b in bids.values())
scarcity = max(0.0, 1 - available / first) if first else 0.0
if scarcity > 0:                                          # 3. demand-response round
    bids = {i: a.generate_bid(BidContext(slot, scarcity=scarcity)) for i, a in agents.items()}
clearing = auction.clear(slot, list(bids.values()), offers)                           # 4. P2 clears
validate_clearing(clearing, list(bids.values()), offers)
results = supply.dispatch(clearing.dispatch)                                          # 5. P3 dispatches
validate_dispatch(clearing.dispatch, results)
allocations = {a.building_id: a for a in clearing.allocations}
settlements = {i: agents[i].settle(allocations[i], envs[i].step()) for i in agents}   # 6. realised + settle

print(f"Slot {slot}")
print("offers:   " + ", ".join(f"{o.source_id} {o.available_kw:.0f} kW @ {o.marginal_price:.0f}" for o in offers))
print("dispatch: " + ", ".join(f"{r.source_id} {r.delivered_kw:.1f} kW" + (f" (SOC -> {r.state['soc']:.2f})"
                                                                              if 'soc' in r.state else "")
                               for r in results))
print(f"requested {first:.0f} kW -> {sum(b.requested_power_kw for b in bids.values()):.0f} kW after the "
      f"demand-response round (scarcity {scarcity:.2f}); supply {available:.0f} kW\n")
print(f"{'building':<16}{'forecast':>9}{'actual':>8}{'req':>7}{'alloc':>7}{'served':>8}  {'status':<19}{'deferred':>8}")
for i, s in settlements.items():
    print(f"{i:<16}{s.forecast_demand_kw:>9.1f}{s.actual_demand_kw:>8.1f}{s.requested_kw:>7.1f}{s.allocated_kw:>7.1f}"
          f"{s.served_kw:>8.1f}  {s.status.value:<19}{s.deferred_kw:>8.1f}")
