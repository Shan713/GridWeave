"""GridWeave Coordinator — real closed-loop campus energy simulation.

The :class:`Coordinator` orchestrates one 15-minute slot at a time through
the full bidding → clearing → dispatch → settlement → feedback cycle, applying scheduled
supply events from a :class:`~gridweave.coordinator.scenarios.Scenario` at the
right slot, and producing a canonical :class:`~gridweave.coordinator.records.SimulationResult`
that all dashboards and metrics consume.

Lifecycle per slot (via :meth:`run_step`)::

    1. Apply scheduled supply events           (P3 events API)
    2. Obtain supply offers                    (P3 CampusSupplyProvider.offers)
    3. Generate bids — round 1                 (P1 BuildingAgent.generate_bid)
    4. Detect scarcity; generate revised bids  (P1 BuildingAgent.generate_bid scarcity>0)
    5. Clear the market                        (P2 AuctionEngine.clear)
    6. Validate clearing                       (contracts.validate_clearing)
    7. Dispatch                                (P3 CampusSupplyProvider.dispatch)
    8. Validate dispatch                       (contracts.validate_dispatch)
    9. Scale allocations if a source under-delivers
    10. Step environments → reveal realised demand
    11. Settle every building agent             (P1 BuildingAgent.settle)
    12. Apply settlement feedback to environments (BuildingSimulator.apply_settlement)
    13. Record the completed slot               (SlotRecord)

Failure handling follows the same semantics as :class:`~gridweave.mocks.coordinator.MockCoordinator`
(the validated reference loop), keeping every agent synchronised.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from gridweave.contracts import validate_clearing, validate_dispatch
from gridweave.coordinator.records import (
    BuildingSummary,
    EventRecord,
    SimulationResult,
    SlotRecord,
)
from gridweave.coordinator.scenarios import Scenario
from gridweave.interfaces import Auctioneer, DemandAgent, EnvironmentStream, SupplyProvider
from gridweave.models.allocation import Allocation
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.context import BidContext
from gridweave.models.demand import Observation
from gridweave.models.settlement import Settlement
from gridweave.models.supply import SupplyOffer
from gridweave.utils.logging import get_logger
from gridweave.utils.validation import clamp

log = get_logger("coordinator")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CoordinatorError(RuntimeError):
    """Fatal configuration or programming error in the coordinator."""


class SettlementError(RuntimeError):
    """One or more agents could not be settled for a slot.

    ``record`` holds the (already saved) :class:`SlotRecord`; ``failures``
    maps building_id to the original exception.  The first original exception
    is the ``__cause__``.
    """

    def __init__(self, record: SlotRecord, failures: dict[str, BaseException]) -> None:
        self.record = record
        self.failures = failures
        detail = "; ".join(f"{k}: {type(e).__name__}: {e}" for k, e in failures.items())
        super().__init__(f"settlement failed for slot {record.time_slot}: {detail}")


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class Coordinator:
    """Full campus energy simulation coordinator.

    Parameters
    ----------
    agents:
        ``{building_id: DemandAgent}`` — one per building.
    environments:
        ``{building_id: EnvironmentStream}`` — must match ``agents`` exactly.
    auctioneer:
        P2 :class:`~gridweave.interfaces.Auctioneer` implementation.
    supply:
        P3 :class:`~gridweave.interfaces.SupplyProvider` implementation.
    scenario:
        :class:`~gridweave.coordinator.scenarios.Scenario` carrying the event
        schedule and run parameters.  ``None`` means no events, unlimited run.
    negotiation_rounds:
        Maximum bidding rounds per slot (1 = no re-auction, ≥2 enables it).
        Overridden by ``scenario.negotiation_rounds`` when a scenario is given.
    resolution_minutes:
        Slot duration.  Must match the agents' ``resolution_minutes``.
    on_failure:
        ``"settle_zero"`` (default) or ``"raise"``.  Controls what happens when
        P2/P3 raise an exception *before* any realised demand is revealed.
    """

    def __init__(
        self,
        agents: Mapping[str, DemandAgent],
        environments: Mapping[str, EnvironmentStream],
        auctioneer: Auctioneer,
        supply: SupplyProvider,
        scenario: Scenario | None = None,
        negotiation_rounds: int = 2,
        resolution_minutes: int = 15,
        on_failure: str = "settle_zero",
    ) -> None:
        if set(agents) != set(environments):
            raise CoordinatorError(
                "Every agent needs exactly one environment stream and vice versa. "
                f"Mismatch: agents={sorted(agents)}, envs={sorted(environments)}"
            )
        if negotiation_rounds < 1:
            raise CoordinatorError("negotiation_rounds must be >= 1")
        if on_failure not in ("settle_zero", "raise"):
            raise CoordinatorError("on_failure must be 'settle_zero' or 'raise'")

        self.agents: dict[str, DemandAgent] = dict(agents)
        self.environments: dict[str, EnvironmentStream] = dict(environments)
        self.auctioneer: Auctioneer = auctioneer
        self.supply: SupplyProvider = supply
        self.scenario: Scenario | None = scenario
        self.resolution_minutes: int = resolution_minutes
        self.on_failure: str = on_failure

        # Scenario overrides negotiation_rounds if provided
        self.negotiation_rounds: int = (
            scenario.negotiation_rounds if scenario is not None else negotiation_rounds
        )

        # Mutable run state
        self._slots: list[SlotRecord] = []
        self._event_log: list[EventRecord] = []
        self._step_index: int = 0          # how many slots have been run
        self._warm_up_done: bool = False
        self._run_start: float = 0.0

    # ---------------------------------------------------------------- properties
    @property
    def has_next(self) -> bool:
        """True if all environments still have unconsumed slots."""
        return all(env.has_next for env in self.environments.values())

    @property
    def steps_run(self) -> int:
        return self._step_index

    @property
    def records(self) -> list[SlotRecord]:
        """Read-only view of completed slot records (same list object)."""
        return self._slots

    @property
    def event_log(self) -> list[EventRecord]:
        return self._event_log

    # ---------------------------------------------------------------- warm-up
    def warm_up(self, slots: int = 1) -> None:
        """Give every agent ``slots`` initial observations (no bids placed).

        Called automatically by :meth:`run_step` if no history is present.
        """
        for _ in range(slots):
            for building_id, env in self.environments.items():
                obs = env.step()
                self.agents[building_id].observe(obs)
                log.debug("warm-up: %s obs %s demand=%.1f kW",
                          building_id, obs.timestamp, obs.measured_demand_kw)
        self._warm_up_done = True

    # ---------------------------------------------------------------- internals
    def _current_slot(self) -> TimeSlot:
        """Determine the next time slot from agent histories (must be unanimous)."""
        slots = {a.next_slot() for a in self.agents.values()}
        if len(slots) != 1:
            raise CoordinatorError(
                f"Agents are not synchronised — next slots: {sorted(map(str, slots))}"
            )
        return slots.pop()

    def _generate_bids(self, slot: TimeSlot, scarcity: float) -> dict[str, Bid]:
        return {
            bid: agent.generate_bid(BidContext(slot, scarcity=scarcity))
            for bid, agent in self.agents.items()
        }

    def _apply_events(self, slot_index: int, time_slot: TimeSlot) -> list[str]:
        """Apply all events scheduled for *slot_index* and return their names."""
        if self.scenario is None:
            return []
        events_here = self.scenario.events_at(slot_index)
        if not events_here:
            return []

        event_api = getattr(self.supply, "events", None)
        applied: list[str] = []
        for ev in events_here:
            evt_name = f"{ev.event_type}"
            trigger = None
            if event_api is not None:
                trigger = getattr(event_api, f"trigger_{ev.event_type}", None)

            desc = f"Slot {slot_index}: {ev.event_type}"
            params = dict(ev.params)
            if ev.source_id:
                params.setdefault("source_id", ev.source_id)

            try:
                if trigger is not None:
                    trigger(**params)
                    applied.append(evt_name)
                    log.info("slot %s event: %s params=%s", slot_index, ev.event_type, params)
                else:
                    log.warning("slot %s: no trigger for event_type=%s (skipping)",
                                slot_index, ev.event_type)
                    applied.append(f"{evt_name}(no-op)")
            except Exception as exc:  # noqa: BLE001
                log.error("slot %s: event %s failed: %s", slot_index, ev.event_type, exc)
                applied.append(f"{evt_name}(ERROR:{exc})")

            self._event_log.append(EventRecord(
                slot_index=slot_index,
                time_slot_start=time_slot.start,
                event_type=ev.event_type,
                description=desc,
                parameters=params,
            ))
        return applied

    @staticmethod
    def _recover_agent(
        agent: DemandAgent,
        slot: TimeSlot,
        reason: str,
    ) -> None:
        """Return an agent that failed to settle to a usable synchronised state.

        The pending bid is aborted and, if the slot start is not yet in the
        agent's history, a carried-forward observation (flagged ``imputed``) is
        injected so the agent stays clock-synchronised.
        """
        if getattr(agent, "pending_bid", None) is not None:
            agent.abort_bid(reason)
        history = getattr(agent, "history", ())
        if history and history[-1].timestamp < slot.start:
            agent.observe(Observation(
                agent.building_id, slot.start, history[-1].demand_kw,
                metadata={"imputed": True, "reason": reason},
            ))

    # ---------------------------------------------------------------- core step
    def run_step(self) -> SlotRecord:
        """Execute one simulation slot and return its :class:`SlotRecord`.

        Side effects
        ------------
        * P3 event triggers may fire.
        * All building agents are either settled or recovered.
        * All building environments advance by one slot.
        * The result is appended to :attr:`records`.
        """
        # ---- warm-up if first call ----
        if any(not getattr(a, "history", ()) for a in self.agents.values()):
            self.warm_up()

        slot_index = self._step_index
        slot = self._current_slot()

        # ---- 1. events ----
        events_triggered = self._apply_events(slot_index, slot)

        # ---- 2. supply offers ----
        offers: list[SupplyOffer] = list(self.supply.offers(slot))
        available_kw = sum(o.available_kw for o in offers)

        # ---- 3. bids (round 1) ----
        scarcity = 0.0
        rounds = 1
        bids = self._generate_bids(slot, 0.0)
        first_requested = sum(b.requested_power_kw for b in bids.values())

        # ---- 4. re-auction / scarcity response ----
        if self.negotiation_rounds > 1 and first_requested > available_kw:
            scarcity = clamp(1.0 - available_kw / first_requested)
            rounds = 2
            bids = self._generate_bids(slot, scarcity)
            log.debug("slot %s: scarcity=%.3f, revised bids requested=%.1f kW",
                      slot, scarcity, sum(b.requested_power_kw for b in bids.values()))

        record = SlotRecord(
            slot_index=slot_index,
            time_slot=slot,
            offers=offers,
            bids=bids,
            clearing=None,
            dispatch_results=[],
            settlements={},
            scarcity=scarcity,
            rounds=rounds,
            first_round_requested_kw=first_requested,
            events_triggered=events_triggered,
        )

        # ---- 5-9. market clearing + dispatch ----
        allocations: dict[str, Allocation]
        try:
            bid_list = list(bids.values())
            clearing = self.auctioneer.clear(slot, bid_list, offers)
            validate_clearing(clearing, bid_list, offers)

            results = list(self.supply.dispatch(clearing.dispatch))
            validate_dispatch(clearing.dispatch, results)

            record.clearing = clearing
            record.dispatch_results = results

            # Capture rich MarketResult if the auctioneer exposes it
            if hasattr(self.auctioneer, "get_last_result"):
                record.market_result = self.auctioneer.get_last_result()

            allocations = {a.building_id: a for a in clearing.allocations}

            # Scale down allocations pro-rata if any source under-delivered
            dispatched = clearing.total_dispatched_kw
            delivered  = sum(r.delivered_kw for r in results)
            if dispatched > 0 and delivered < dispatched - 1e-6:
                ratio = delivered / dispatched
                log.warning("slot %s: delivery shortfall %.1f kW → %.1f kW; scaling allocations by %.4f",
                            slot, dispatched, delivered, ratio)
                allocations = {
                    k: Allocation(
                        a.bid_id, a.building_id, slot,
                        a.allocated_power_kw * ratio,
                        clearing_price=a.clearing_price,
                        metadata={"scaled_by": ratio, **dict(a.metadata)},
                    )
                    for k, a in allocations.items()
                }

        except Exception as exc:  # noqa: BLE001  — keep all agents consistent
            record.failure = f"{type(exc).__name__}: {exc}"
            log.error("slot %s: market/dispatch failure: %s  (policy=%s)",
                      slot, record.failure, self.on_failure)
            if self.on_failure == "raise":
                for agent in self.agents.values():
                    self._recover_agent(agent, slot, record.failure)
                self._slots.append(record)
                self._step_index += 1
                raise
            # settle_zero: give every building a zero allocation
            allocations = {
                i: Allocation(b.bid_id, i, slot, 0.0,
                              metadata={"failure": record.failure})
                for i, b in bids.items()
            }

        # ---- 10-12. reveal demand + settle + apply feedback ----
        settlement_failures: dict[str, BaseException] = {}
        for building_id, agent in self.agents.items():
            env = self.environments[building_id]
            try:
                realised: Observation = env.step()
                settlement: Settlement = agent.settle(allocations[building_id], realised)
                env.apply_settlement(settlement)
                record.settlements[building_id] = settlement
                log.debug("slot %s %s: served=%.1f/%.1f kW status=%s",
                          slot, building_id, settlement.served_kw,
                          settlement.actual_total_kw, settlement.status.value)
            except Exception as exc:  # noqa: BLE001
                settlement_failures[building_id] = exc
                reason = f"{type(exc).__name__}: {exc}"
                record.settlement_failures[building_id] = reason
                self._recover_agent(agent, slot, reason)
                log.error("slot %s: settlement failed for %s: %s",
                          slot, building_id, reason)

        # ---- 13. record ----
        self._slots.append(record)
        self._step_index += 1

        if settlement_failures:
            raise SettlementError(record, settlement_failures) from next(
                iter(settlement_failures.values())
            )
        return record

    # ---------------------------------------------------------------- run
    def run(self, steps: int | None = None) -> SimulationResult:
        """Run the simulation for *steps* slots (or until environments exhausted).

        Parameters
        ----------
        steps:
            Number of slots to simulate.  ``None`` runs until :attr:`has_next`
            is ``False`` (environment exhausted) or the scenario slot limit is
            reached.

        Returns
        -------
        SimulationResult
            Canonical result object consumed by dashboards and metrics.
        """
        self._run_start = time.perf_counter()

        # Determine how many slots to run
        limit = steps
        if limit is None and self.scenario is not None:
            limit = self.scenario.n_slots

        done = 0
        while self.has_next and (limit is None or done < limit):
            try:
                self.run_step()
            except SettlementError:
                pass  # already recorded; continue simulation
            done += 1

        return self._build_result()

    # ---------------------------------------------------------------- result
    def _build_result(self) -> SimulationResult:
        run_end = time.perf_counter()
        run_duration = run_end - self._run_start

        scenario_name = self.scenario.name if self.scenario else "custom"
        seed = self.scenario.seed if self.scenario else 0

        # Per-building summaries from agent.stats (authoritative per P1 contract)
        summaries: dict[str, BuildingSummary] = {}
        for building_id, agent in self.agents.items():
            s = agent.stats
            spec = getattr(agent, "spec", None)
            summaries[building_id] = BuildingSummary(
                building_id=building_id,
                name=getattr(spec, "name", building_id) if spec else building_id,
                building_type=getattr(spec, "building_type", None).value
                if spec and hasattr(spec, "building_type") else "unknown",
                slots_settled=s.slots_settled,
                demand_kwh=s.demand_kwh,
                served_kwh=s.served_kwh,
                backlog_served_kwh=s.backlog_served_kwh,
                deferred_kwh=s.deferred_kwh,
                curtailed_kwh=s.curtailed_kwh,
                expired_kwh=s.expired_kwh,
                critical_shortfall_kwh=s.critical_shortfall_kwh,
                critical_shortfall_events=s.critical_shortfall_events,
                unused_allocation_kwh=s.unused_allocation_kwh,
                forecast_mae_kw=s.forecast_mae_kw,
                forecast_rmse_kw=s.forecast_rmse_kw,
                total_cost=s.total_cost,
                final_backlog_kwh=getattr(agent, "backlog_energy_kwh", 0.0),
            )

        # Supply metrics from P3
        supply_metrics_obj = getattr(self.supply, "metrics", None)
        supply_metrics = supply_metrics_obj().to_dict() if callable(supply_metrics_obj) else {}
        supply_snap_obj = getattr(self.supply, "snapshot", None)
        supply_snapshot = supply_snap_obj() if callable(supply_snap_obj) else {}

        # Start/end times from slot records
        start_time = self._slots[0].time_slot.start if self._slots else None
        end_time = self._slots[-1].time_slot.start if self._slots else None
        # If no slots exist yet, fall back to something safe
        if start_time is None:
            from datetime import datetime
            start_time = end_time = datetime.utcnow()

        return SimulationResult(
            scenario_name=scenario_name,
            seed=seed,
            building_ids=list(self.agents),
            resolution_minutes=self.resolution_minutes,
            start_time=start_time,
            end_time=end_time,
            run_duration_s=run_duration,
            slots=list(self._slots),
            building_summaries=summaries,
            supply_metrics=supply_metrics,
            supply_snapshot=supply_snapshot,
            event_log=list(self._event_log),
            metadata={
                "negotiation_rounds": self.negotiation_rounds,
                "on_failure": self.on_failure,
                "total_steps": self._step_index,
                "total_market_failures": sum(1 for r in self._slots if r.failure),
            },
        )

    # ---------------------------------------------------------------- utilities
    def reset(self) -> None:
        """Reset coordinator run state so :meth:`run` can be called again.

        Does **not** reset P3 supply state (battery SOC, events) or P1 agent
        history — call ``supply.reset()`` and rebuild agents for a fresh run.
        """
        self._slots.clear()
        self._event_log.clear()
        self._step_index = 0
        self._warm_up_done = False
        self._run_start = 0.0

    def snapshot(self) -> dict[str, Any]:
        """Current coordinator state snapshot for debugging / dashboards."""
        return {
            "step_index": self._step_index,
            "has_next": self.has_next,
            "scenario": self.scenario.name if self.scenario else None,
            "negotiation_rounds": self.negotiation_rounds,
            "on_failure": self.on_failure,
            "slots_run": len(self._slots),
            "events_fired": len(self._event_log),
            "market_failures": sum(1 for r in self._slots if r.failure),
            "agents": {bid: {"phase": getattr(a, "phase", None)} for bid, a in self.agents.items()},
        }
