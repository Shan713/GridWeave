"""Full loop against the mocks, written out step by step (what P4 will automate).

Run: python examples/mock_market_cycle.py
"""
from gridweave.bidding import BidContext
from gridweave.config import load_campus_config
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockGrid

cfg = load_campus_config()
agents, sims = build_agents(cfg), build_simulators(cfg)
auction, grid = MockAuctioneer(), MockGrid(330)       # tight supply to force demand response

for cycle in range(20 * 4 + 1):                         # run up to the 20:15 slot
    for building_id, sim in sims.items():
        agents[building_id].observe(sim.step())         # 1. observe
    slot = next(iter(agents.values())).next_slot()
    supply = grid.available_power_kw(slot)
    bids = {i: a.generate_bid(BidContext(slot)) for i, a in agents.items()}          # 2. bid
    requested = sum(b.requested_power_kw for b in bids.values())
    scarcity = max(0.0, 1 - supply / requested) if requested else 0.0
    if scarcity > 0:                                                                  # 3. re-bid
        bids = {i: a.generate_bid(BidContext(slot, scarcity=scarcity)) for i, a in agents.items()}
    for b in bids.values():
        auction.submit_bid(b)                                                         # 4. auction
    allocations = auction.clear(slot, supply)
    outcomes = {al.building_id: agents[al.building_id].apply_allocation(al) for al in allocations}  # 5. respond

print(f"Slot {slot}: supply {supply:.0f} kW, requested {requested:.0f} kW, scarcity {scarcity:.2f}\n")
print(f"{'building':<16}{'req':>7}{'crit':>7}{'prio':>7}{'wtp':>7}{'alloc':>8}  status            deferred  backlog")
for i, o in outcomes.items():
    b = bids[i]
    print(f"{i:<16}{b.requested_power_kw:>7.1f}{b.critical_power_kw:>7.1f}{b.priority_score:>7.3f}"
          f"{b.willingness_to_pay:>7.2f}{o.allocated_kw:>8.1f}  {o.status.value:<18}"
          f"{o.flexible_deferred_kw:>7.1f}{agents[i].backlog_kw:>8.1f}")
