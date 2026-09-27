"""MockCoordinator: a minimal, *validated* closed-loop cycle (stand-in for P4).

One ``run_step`` is one slot::

    (warm-up once: env.step() -> agent.observe())
    offers   = supply.offers(slot)                           P3
    bids     = agent.generate_bid(BidContext(slot))          P1, round 1
    [if requested > supply: bids = revised bids with scarcity (agents trim flexible load)]
    clearing = auctioneer.clear(slot, bids, offers)          P2   -> validate_clearing
    results  = supply.dispatch(clearing.dispatch)            P3   -> validate_dispatch
    [if a source under-delivers: scale allocations down pro rata]
    realised = env.step()                                    environment reveals the slot
    settle   = agent.settle(allocation, realised)            P1
    env.apply_settlement(settle)                             closed loop

Failure handling (no agent is ever left ``bid_pending``):

* **Market or dispatch failure / contract violation** (before any realised
  demand is revealed). ``on_failure`` decides: ``"settle_zero"`` (default)
  settles every agent with a zero allocation (no power was cleared, so
  shortfalls are reported honestly); ``"raise"`` aborts every pending bid and
  re-raises. Environments have not advanced, so the same slot can be re-run.
* **Settlement failure** (``env.step()`` or ``agent.settle`` raises, e.g. the
  environment reports demand above a building's capacity). This is a data
  error, never swallowed and independent of ``on_failure``. Every other agent
  is still settled normally; each failed agent's bid is aborted and the slot
  is reported to it with a carried-forward observation flagged
  ``{"imputed": True}`` (excluded from settlement statistics), so all agents
  stay synchronised and usable. The step is recorded, then
  :class:`SettlementError` is raised, chained to the first original error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping

from gridweave.contracts import validate_clearing, validate_dispatch
from gridweave.interfaces import Auctioneer, DemandAgent, EnvironmentStream, SupplyProvider
from gridweave.models.allocation import Allocation
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.context import BidContext
from gridweave.models.demand import Observation
from gridweave.models.settlement import Settlement
from gridweave.models.supply import ClearingResult, DispatchResult, SupplyOffer
from gridweave.utils.logging import get_logger
from gridweave.utils.validation import ValidationError, clamp

log = get_logger("mocks.coordinator")


class SettlementError(RuntimeError):
    """One or more agents could not be settled for a slot.

    ``record`` is the (already recorded) step; ``failures`` maps building id to
    the original exception. The first original exception is ``__cause__``.
    """

    def __init__(self, record: "StepRecord", failures: dict[str, BaseException]) -> None:
        self.record = record
        self.failures = failures
        detail = "; ".join(f"{k}: {type(e).__name__}: {e}" for k, e in failures.items())
        super().__init__(f"settlement failed for slot {record.time_slot}: {detail}")


@dataclass
class StepRecord:
    time_slot: TimeSlot
    offers: list[SupplyOffer]
    scarcity: float
    rounds: int
    bids: dict[str, Bid] = field(default_factory=dict)
    first_round_requested_kw: float = 0.0
    clearing: ClearingResult | None = None
    dispatch_results: list[DispatchResult] = field(default_factory=list)
    settlements: dict[str, Settlement] = field(default_factory=dict)
    failure: str | None = None
    settlement_failures: dict[str, str] = field(default_factory=dict)

    @property
    def supply_kw(self) -> float:
        return sum(o.available_kw for o in self.offers)

    @property
    def requested_kw(self) -> float:
        return sum(b.requested_power_kw for b in self.bids.values())

    @property
    def actual_demand_kw(self) -> float:
        return sum(s.actual_total_kw for s in self.settlements.values())

    @property
    def served_kw(self) -> float:
        return sum(s.served_kw for s in self.settlements.values())

    @property
    def delivered_kw(self) -> float:
        return sum(r.delivered_kw for r in self.dispatch_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_slot": self.time_slot.to_dict(),
            "supply_kw": self.supply_kw,
            "scarcity": self.scarcity,
            "rounds": self.rounds,
            "first_round_requested_kw": self.first_round_requested_kw,
            "requested_kw": self.requested_kw,
            "actual_demand_kw": self.actual_demand_kw,
            "served_kw": self.served_kw,
            "failure": self.failure,
            "settlement_failures": dict(self.settlement_failures),
            "offers": [o.to_dict() for o in self.offers],
            "clearing": self.clearing.to_dict() if self.clearing else None,
            "dispatch_results": [r.to_dict() for r in self.dispatch_results],
            "settlements": {k: v.to_dict() for k, v in self.settlements.items()},
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
        on_failure: str = "settle_zero",
        keep_records: bool = True,
    ) -> None:
        if set(agents) != set(environments):
            raise ValidationError("every agent needs exactly one environment stream")
        if negotiation_rounds < 1:
            raise ValidationError("negotiation_rounds must be >= 1")
        if on_failure not in ("settle_zero", "raise"):
            raise ValidationError("on_failure must be 'settle_zero' or 'raise'")
        self.agents = dict(agents)
        self.environments = dict(environments)
        self.auctioneer = auctioneer
        self.supply = supply
        self.negotiation_rounds = negotiation_rounds
        self.resolution = timedelta(minutes=resolution_minutes)
        self.on_failure = on_failure
        self.keep_records = keep_records
        self.records: list[StepRecord] = []
        self.steps_run = 0
        self.failures = 0

    @property
    def has_next(self) -> bool:
        return all(env.has_next for env in self.environments.values())

    def warm_up(self, slots: int = 1) -> None:
        """Give every agent ``slots`` observations before the first market."""
        for _ in range(slots):
            for building_id, env in self.environments.items():
                self.agents[building_id].observe(env.step())

    def _slot(self) -> TimeSlot:
        slots = {a.next_slot() for a in self.agents.values()}
        if len(slots) != 1:
            raise ValidationError(f"agents are not synchronised: next slots {sorted(map(str, slots))}")
        return slots.pop()

    def _bids(self, slot: TimeSlot, scarcity: float) -> dict[str, Bid]:
        return {i: a.generate_bid(BidContext(slot, scarcity=scarcity)) for i, a in self.agents.items()}

    def run_step(self) -> StepRecord:
        if any(not a.history for a in self.agents.values()):
            self.warm_up()
        slot = self._slot()
        offers = list(self.supply.offers(slot))
        available = sum(o.available_kw for o in offers)

        scarcity, rounds = 0.0, 1
        bids = self._bids(slot, 0.0)
        first_requested = sum(b.requested_power_kw for b in bids.values())
        if self.negotiation_rounds > 1 and first_requested > available:
            scarcity, rounds = clamp(1.0 - available / first_requested), 2
            bids = self._bids(slot, scarcity)  # revised bids: agents trim flexible load
        record = StepRecord(slot, offers, scarcity, rounds, bids=bids, first_round_requested_kw=first_requested)

        allocations: dict[str, Allocation]
        try:
            bid_list = list(bids.values())
            clearing = self.auctioneer.clear(slot, bid_list, offers)
            validate_clearing(clearing, bid_list, offers)
            results = list(self.supply.dispatch(clearing.dispatch))
            validate_dispatch(clearing.dispatch, results)
            record.clearing, record.dispatch_results = clearing, results
            allocations = {a.building_id: a for a in clearing.allocations}
            dispatched = clearing.total_dispatched_kw
            delivered = sum(r.delivered_kw for r in results)
            if dispatched > 0 and delivered < dispatched - 1e-6:  # a source under-delivered
                ratio = delivered / dispatched
                allocations = {k: Allocation(a.bid_id, a.building_id, slot, a.allocated_power_kw * ratio,
                                             clearing_price=a.clearing_price, metadata={"scaled_by": ratio})
                               for k, a in allocations.items()}
        except Exception as exc:  # noqa: BLE001 - any P2/P3 failure must leave agents consistent
            self.failures += 1
            record.failure = f"{type(exc).__name__}: {exc}"
            log.error("slot %s: market failure (%s), policy=%s", slot, record.failure, self.on_failure)
            if self.on_failure == "raise":
                for agent in self.agents.values():
                    agent.abort_bid(record.failure)
                raise
            allocations = {i: Allocation(b.bid_id, i, slot, 0.0, metadata={"failure": record.failure})
                           for i, b in bids.items()}

        failures: dict[str, BaseException] = {}
        for building_id, agent in self.agents.items():
            env = self.environments[building_id]
            try:
                settlement = agent.settle(allocations[building_id], env.step())
                env.apply_settlement(settlement)
                record.settlements[building_id] = settlement
            except Exception as exc:  # noqa: BLE001 - recorded, recovered, and re-raised below
                failures[building_id] = exc
                record.settlement_failures[building_id] = f"{type(exc).__name__}: {exc}"
                self._recover(agent, slot, record.settlement_failures[building_id])
        self.steps_run += 1
        if self.keep_records:
            self.records.append(record)
        if failures:
            self.failures += 1
            log.error("slot %s: settlement failed for %s", slot, sorted(failures))
            raise SettlementError(record, failures) from next(iter(failures.values()))
        return record

    @staticmethod
    def _recover(agent: Any, slot: TimeSlot, reason: str) -> None:
        """Return an agent whose slot could not be settled to a usable, synchronised state.

        The pending bid is aborted (no settlement is fabricated). If the slot is
        not yet in the agent's history, it is reported with the last observed
        value, flagged as imputed. Imputation is this reference coordinator's
        (P4's) policy; P1 only requires that every slot be reported.
        """
        if agent.pending_bid is not None:
            agent.abort_bid(reason)
        last = agent.history[-1]
        if last.timestamp < slot.start:
            agent.observe(Observation(agent.building_id, slot.start, last.demand_kw,
                                      metadata={"imputed": True, "reason": reason}))

    def run(self, steps: int | None = None) -> list[StepRecord]:
        done = 0
        while self.has_next and (steps is None or done < steps):
            if any(not a.history for a in self.agents.values()):
                self.warm_up()
                if not self.has_next:
                    break
            self.run_step()
            done += 1
        return self.records


def summarise(records: list[StepRecord], agents: Mapping[str, Any]) -> dict[str, Any]:
    """Campus- and building-level KPIs, all computed from settlements (realised demand)."""
    per_building = {}
    for building_id, agent in agents.items():
        s = agent.stats
        per_building[building_id] = {
            "service_ratio": round(s.service_ratio, 4),
            "demand_kwh": round(s.demand_kwh, 2),
            "served_kwh": round(s.served_kwh, 2),
            "deferred_kwh": round(s.deferred_kwh, 2),
            "backlog_served_kwh": round(s.backlog_served_kwh, 2),
            "curtailed_kwh": round(s.curtailed_kwh, 2),
            "expired_kwh": round(s.expired_kwh, 2),
            "critical_shortfall_kwh": round(s.critical_shortfall_kwh, 3),
            "critical_shortfall_events": s.critical_shortfall_events,
            "unused_allocation_kwh": round(s.unused_allocation_kwh, 2),
            "forecast_mae_kw": None if s.forecast_mae_kw is None else round(s.forecast_mae_kw, 3),
            "cost": round(s.total_cost, 2),
            "final_backlog_kwh": round(agent.backlog_energy_kwh, 2),
        }
    total = lambda key: round(sum(v[key] for v in per_building.values()), 2)  # noqa: E731
    return {
        "steps": len(records),
        "demand_kwh": total("demand_kwh"),
        "served_kwh": total("served_kwh"),
        "deferred_kwh": total("deferred_kwh"),
        "curtailed_kwh": total("curtailed_kwh"),
        "expired_kwh": total("expired_kwh"),
        "unused_allocation_kwh": total("unused_allocation_kwh"),
        "critical_shortfall_kwh": total("critical_shortfall_kwh"),
        "critical_shortfall_events": sum(v["critical_shortfall_events"] for v in per_building.values()),
        "shortage_steps": sum(1 for r in records if r.first_round_requested_kw > r.supply_kw + 1e-6),
        "revised_bid_steps": sum(1 for r in records if r.rounds > 1),
        "market_failures": sum(1 for r in records if r.failure),
        "buildings": per_building,
    }
