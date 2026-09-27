"""MockCoordinator: a minimal decision-cycle loop (stand-in for P4).

One ``run_step`` is one market cycle::

    environment.step() -> agent.observe()          for every building
    agent.generate_bid(context)                     round 1 (scarcity 0)
    [if requested > supply: broadcast scarcity,     round 2..n (revised bids)
     agent.generate_bid(context')]
    auctioneer.submit_bid(bid); auctioneer.clear()  market
    agent.apply_allocation(allocation)              every pending bid is settled
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping

from gridweave.bidding.bid_generator import BidContext
from gridweave.interfaces import Auctioneer, DemandAgent, EnvironmentStream, SupplyProvider
from gridweave.models.allocation import Allocation, AllocationOutcome
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.utils.logging import get_logger
from gridweave.utils.validation import ValidationError, clamp

log = get_logger("mocks.coordinator")


@dataclass
class StepRecord:
    time_slot: TimeSlot
    supply_kw: float
    scarcity: float
    rounds: int
    bids: dict[str, Bid] = field(default_factory=dict)
    outcomes: dict[str, AllocationOutcome] = field(default_factory=dict)

    @property
    def requested_kw(self) -> float:
        return sum(b.requested_power_kw for b in self.bids.values())

    @property
    def critical_kw(self) -> float:
        return sum(b.critical_power_kw for b in self.bids.values())

    @property
    def served_kw(self) -> float:
        return sum(o.accepted_kw for o in self.outcomes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_slot": self.time_slot.to_dict(),
            "supply_kw": self.supply_kw,
            "scarcity": self.scarcity,
            "rounds": self.rounds,
            "requested_kw": self.requested_kw,
            "served_kw": self.served_kw,
            "bids": {k: v.to_dict() for k, v in self.bids.items()},
            "outcomes": {k: v.to_dict() for k, v in self.outcomes.items()},
        }


class MockCoordinator:
    def __init__(
        self,
        agents: Mapping[str, DemandAgent],
        environments: Mapping[str, EnvironmentStream],
        auctioneer: Auctioneer,
        supply: SupplyProvider,
        negotiation_rounds: int = 2,
        resolution_minutes: int = 15,
    ) -> None:
        if set(agents) != set(environments):
            raise ValidationError("every agent needs exactly one environment stream")
        if negotiation_rounds < 1:
            raise ValidationError("negotiation_rounds must be >= 1")
        self.agents = dict(agents)
        self.environments = dict(environments)
        self.auctioneer = auctioneer
        self.supply = supply
        self.negotiation_rounds = negotiation_rounds
        self.resolution = timedelta(minutes=resolution_minutes)
        self.records: list[StepRecord] = []

    @property
    def has_next(self) -> bool:
        return all(env.has_next for env in self.environments.values())

    def run_step(self) -> StepRecord:
        latest = None
        for building_id, env in self.environments.items():
            obs = env.step()
            self.agents[building_id].observe(obs)
            latest = obs.timestamp if latest is None else max(latest, obs.timestamp)
        slot = TimeSlot(latest + self.resolution, int(self.resolution.total_seconds() // 60))
        supply_kw = self.supply.available_power_kw(slot)

        scarcity, rounds = 0.0, 1
        bids = {bid_id: a.generate_bid(BidContext(slot, scarcity=0.0)) for bid_id, a in self.agents.items()}
        while rounds < self.negotiation_rounds:
            requested = sum(b.requested_power_kw for b in bids.values())
            new_scarcity = clamp(1.0 - supply_kw / requested) if requested > 0 else 0.0
            if new_scarcity <= scarcity:
                break
            scarcity, rounds = new_scarcity, rounds + 1
            bids = {bid_id: a.generate_bid(BidContext(slot, scarcity=scarcity)) for bid_id, a in self.agents.items()}

        for bid in bids.values():
            self.auctioneer.submit_bid(bid)
        allocations = {a.building_id: a for a in self.auctioneer.clear(slot, supply_kw)}

        record = StepRecord(slot, supply_kw, scarcity, rounds, bids=bids)
        for building_id, agent in self.agents.items():
            bid = bids[building_id]
            allocation = allocations.get(building_id) or Allocation(bid.bid_id, building_id, slot, 0.0)
            record.outcomes[building_id] = agent.apply_allocation(allocation)
        self.records.append(record)
        log.debug("%s supply=%.1f requested=%.1f served=%.1f", slot, supply_kw, record.requested_kw,
                  record.served_kw)
        return record

    def run(self, steps: int | None = None) -> list[StepRecord]:
        done = 0
        while self.has_next and (steps is None or done < steps):
            self.run_step()
            done += 1
        return self.records


def summarise(records: list[StepRecord], agents: Mapping[str, Any]) -> dict[str, Any]:
    """Campus- and building-level KPIs for a finished run."""
    if not records:
        return {"steps": 0}
    hours = records[0].time_slot.hours
    per_building = {}
    for building_id, agent in agents.items():
        s = agent.stats
        per_building[building_id] = {
            "service_ratio": round(s.service_ratio, 4),
            "served_kwh": round(s.served_energy_kwh, 2),
            "deferred_kwh": round(s.deferred_energy_kwh, 2),
            "curtailed_kwh": round(s.curtailed_energy_kwh, 2),
            "critical_shortfall_events": s.critical_shortfall_events,
            "cost": round(s.total_cost, 2),
            "final_backlog_kw": round(agent.backlog_kw, 2),
        }
    return {
        "steps": len(records),
        "requested_kwh": round(sum(r.requested_kw for r in records) * hours, 2),
        "served_kwh": round(sum(r.served_kw for r in records) * hours, 2),
        "shortage_steps": sum(1 for r in records if r.requested_kw > r.supply_kw + 1e-6),
        "renegotiated_steps": sum(1 for r in records if r.rounds > 1),
        "critical_shortfall_events": sum(v["critical_shortfall_events"] for v in per_building.values()),
        "buildings": per_building,
    }
