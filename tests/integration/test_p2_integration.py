"""Integration tests verifying Person 2 Auction Subsystem with Person 1 Building Agents."""
from __future__ import annotations

import pytest

from gridweave.auction import (
    AuctionEngine,
    GreedyAllocationStrategy,
    OptimizedAllocationStrategy,
)
from gridweave.config import load_campus_config
from gridweave.contracts import validate_clearing
from gridweave.factory import build_agents, build_simulators
from gridweave.interfaces import Auctioneer
from gridweave.mocks import MockCoordinator, MockSupply
from gridweave.models import BidContext


def test_auction_engine_satisfies_auctioneer_contract():
    engine = AuctionEngine(strategy=GreedyAllocationStrategy())
    assert isinstance(engine, Auctioneer)


def test_p1_p2_closed_loop_single_step():
    cfg = load_campus_config()
    agents = build_agents(cfg)
    envs = build_simulators(cfg)
    supply = MockSupply.from_config(cfg.supply)
    engine = AuctionEngine(strategy=GreedyAllocationStrategy())

    # 1. Warm-up observation
    for b_id, env in envs.items():
        agents[b_id].observe(env.step())

    # 2. Slot progression and bid generation
    slot = next(iter(agents.values())).next_slot()
    offers = supply.offers(slot)
    bids = [a.generate_bid(BidContext(slot)) for a in agents.values()]

    # 3. Market clearing with P2 AuctionEngine
    clearing = engine.clear(slot, bids, offers)

    # 4. Cross-workstream validation
    validate_clearing(clearing, bids, offers)
    assert len(clearing.allocations) == len(bids)

    # 5. Dispatch and settlement with P1 agents
    alloc_map = {a.building_id: a for a in clearing.allocations}
    for b_id, agent in agents.items():
        realised = envs[b_id].step()
        settlement = agent.settle(alloc_map[b_id], realised)
        assert settlement.bid_id == alloc_map[b_id].bid_id
        # Conservation of energy
        assert agent.energy_balance_kwh() == pytest.approx(0.0, abs=1e-6)


def test_p2_with_mock_coordinator_greedy_strategy():
    cfg = load_campus_config()
    agents = build_agents(cfg)
    sims = build_simulators(cfg, periods=16)  # 4 hours (16 periods)
    supply = MockSupply.from_config(cfg.supply)
    engine = AuctionEngine(strategy=GreedyAllocationStrategy())

    coord = MockCoordinator(
        agents=agents,
        environments=sims,
        auctioneer=engine,
        supply=supply,
        resolution_minutes=cfg.simulation.resolution_minutes,
    )
    coord.run(15)

    assert len(coord.records) == 15
    for r in coord.records:
        assert r.failure is None
        validate_clearing(r.clearing, list(r.bids.values()), r.offers)

    for agent in agents.values():
        assert agent.energy_balance_kwh() == pytest.approx(0.0, abs=1e-6)


def test_p2_with_mock_coordinator_optimized_strategy():
    cfg = load_campus_config()
    agents = build_agents(cfg)
    sims = build_simulators(cfg, periods=16)
    supply = MockSupply.from_config(cfg.supply)
    engine = AuctionEngine(strategy=OptimizedAllocationStrategy())

    coord = MockCoordinator(
        agents=agents,
        environments=sims,
        auctioneer=engine,
        supply=supply,
        resolution_minutes=cfg.simulation.resolution_minutes,
    )
    coord.run(15)

    assert len(coord.records) == 15
    for r in coord.records:
        assert r.failure is None
        validate_clearing(r.clearing, list(r.bids.values()), r.offers)

    for agent in agents.values():
        assert agent.energy_balance_kwh() == pytest.approx(0.0, abs=1e-6)
