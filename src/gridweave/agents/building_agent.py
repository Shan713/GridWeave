"""The Building Agent: one class for every building, configured by data.

Lifecycle per market slot (driven by the coordinator, P4)::

    observe(observation)              # warm-up / any slot without a bid
    generate_bid(context)             # update_state -> forecast_demand ->
                                      #   classify_load -> calculate_priority -> Bid
    [generate_bid(context') ...]      # optional revision (same slot), e.g. under scarcity
    settle(allocation, realised_obs)  # the slot happened: compare the allocation with
                                      #   REALISED demand, serve critical first, then
                                      #   backlog, then new flexible load; defer or curtail
                                      #   the rest; update backlog and deprivation
    snapshot()                        # observable state for dashboards / P4

The agent owns no auction logic and no supply logic. It depends only on the
abstract forecaster interface and the models in :mod:`gridweave.models`.
Service metrics come exclusively from settlements (realised demand), never
from bids.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from gridweave.agents.allocation_response import check_allocation_matches, respond_to_allocation
from gridweave.agents.base_agent import BaseAgent
from gridweave.bidding.bid_generator import BidGenerator
from gridweave.bidding.priority import PriorityBreakdown, PriorityModel
from gridweave.classification.load_classifier import LoadClassifier
from gridweave.forecasting.anomaly import SpikeDetector
from gridweave.forecasting.base_forecaster import BaseForecaster, Forecast
from gridweave.forecasting.ewma import EWMAForecaster
from gridweave.models.allocation import Allocation, AllocationOutcome
from gridweave.models.bid import Bid
from gridweave.models.building import BuildingSpec
from gridweave.models.common import DEFAULT_RESOLUTION_MINUTES, MissingSlotError, TimeSlot, require_aligned
from gridweave.models.context import BidContext
from gridweave.models.demand import DemandSample, DemandState, LoadClassification, Observation
from gridweave.models.settlement import DeferredEnergy, Settlement, settlement_status
from gridweave.utils.validation import POWER_TOLERANCE_KW, ValidationError, require_fraction


class AgentPhase(str, Enum):
    IDLE = "idle"                # no observation yet
    OBSERVED = "observed"        # has fresh data, no bid outstanding
    BID_PENDING = "bid_pending"  # bid submitted, waiting for the slot to be settled
    SETTLED = "settled"          # the last bid's slot has been settled


class AgentStateError(RuntimeError):
    """A lifecycle method was called in the wrong phase."""


@dataclass
class AgentStatistics:
    """Cumulative, settlement-based counters (energy in kWh, cost in currency).

    Every kWh of realised demand ends up in exactly one bucket, so
    ``demand = served_new + critical_shortfall + curtailed + expired + backlog``
    where ``served_new = served - backlog_served`` for energy served in the same
    slot and backlog serving draws down energy that was counted as demand
    earlier. :meth:`BuildingAgent.energy_balance_kwh` checks this.
    """

    slots_settled: int = 0
    demand_kwh: float = 0.0
    served_kwh: float = 0.0
    backlog_served_kwh: float = 0.0
    deferred_kwh: float = 0.0
    curtailed_kwh: float = 0.0
    expired_kwh: float = 0.0
    critical_shortfall_kwh: float = 0.0
    critical_shortfall_events: int = 0
    unused_allocation_kwh: float = 0.0
    total_cost: float = 0.0
    anomalies_detected: int = 0
    forecast_abs_error_sum_kw: float = 0.0
    forecast_sq_error_sum_kw2: float = 0.0

    @property
    def service_ratio(self) -> float:
        """Energy served / realised demand (1.0 before any demand)."""
        return 1.0 if self.demand_kwh <= 0 else self.served_kwh / self.demand_kwh

    @property
    def forecast_mae_kw(self) -> float | None:
        return None if not self.slots_settled else self.forecast_abs_error_sum_kw / self.slots_settled

    @property
    def forecast_rmse_kw(self) -> float | None:
        return None if not self.slots_settled else math.sqrt(self.forecast_sq_error_sum_kw2 / self.slots_settled)


@dataclass(frozen=True)
class DemandOutlookPoint:
    """Forecast demand for one future slot, already split into load classes."""

    timestamp: datetime
    predicted_demand_kw: float
    confidence: float
    classification: LoadClassification


class BuildingAgent(BaseAgent):
    def __init__(
        self,
        spec: BuildingSpec,
        forecaster: BaseForecaster | None = None,
        priority_model: PriorityModel | None = None,
        bid_generator: BidGenerator | None = None,
        *,
        resolution_minutes: int = DEFAULT_RESOLUTION_MINUTES,
        history_limit: int = 96 * 7,
        deprivation_alpha: float = 0.3,
        spike_detector: SpikeDetector | None = None,
    ) -> None:
        super().__init__(spec.building_id)
        if resolution_minutes <= 0:
            raise ValidationError("resolution_minutes must be > 0")
        if history_limit < 1:
            raise ValidationError("history_limit must be >= 1")
        self.spec = spec
        self.forecaster = forecaster or EWMAForecaster(alpha=0.5)
        self.priority_model = priority_model or PriorityModel()
        self.bid_generator = bid_generator or BidGenerator()
        self.classifier = LoadClassifier(spec)
        self.spike_detector = spike_detector
        self.resolution_minutes = resolution_minutes
        self.deprivation_alpha = require_fraction("deprivation_alpha", deprivation_alpha)
        self._history: deque[DemandSample] = deque(maxlen=history_limit)
        self._phase = AgentPhase.IDLE
        self._backlog: deque[DeferredEnergy] = deque()
        self._deprivation = 0.0
        self._last_forecast: Forecast | None = None
        self._demand_state: DemandState | None = None
        self._pending_bid: Bid | None = None
        self._pending_state: DemandState | None = None
        self._last_settlement: Settlement | None = None
        self.stats = AgentStatistics()

    # ================================================================ state
    @property
    def building_id(self) -> str:
        return self.spec.building_id

    @property
    def phase(self) -> AgentPhase:
        return self._phase

    @property
    def history(self) -> tuple[DemandSample, ...]:
        return tuple(self._history)

    @property
    def backlog(self) -> tuple[DeferredEnergy, ...]:
        return tuple(self._backlog)

    @property
    def backlog_energy_kwh(self) -> float:
        return sum(e.energy_kwh for e in self._backlog)

    @property
    def backlog_kw(self) -> float:
        """Deferred energy expressed as the power needed to serve it within one slot."""
        return self.backlog_energy_kwh / self._hours

    @property
    def deprivation(self) -> float:
        return self._deprivation

    @property
    def pending_bid(self) -> Bid | None:
        return self._pending_bid

    @property
    def demand_state(self) -> DemandState | None:
        return self._demand_state

    @property
    def last_settlement(self) -> Settlement | None:
        return self._last_settlement

    @property
    def step(self) -> timedelta:
        return timedelta(minutes=self.resolution_minutes)

    @property
    def _hours(self) -> float:
        return self.resolution_minutes / 60.0

    # ============================================================ 1. observe
    def observe(self, observation: Observation) -> bool:
        """Record a measurement for a slot that has no pending bid.

        Returns ``True`` if it was flagged as a spike. The slot a pending bid
        is for must be closed with :meth:`settle`, not observed.
        """
        if self._pending_bid is not None and observation.timestamp >= self._pending_bid.time_slot.start:
            raise AgentStateError(
                f"{self.building_id}: slot {self._pending_bid.time_slot} has a pending bid; "
                f"close it with settle(allocation, realised_observation) (or abort_bid() first)"
            )
        anomaly = self._record(observation)
        if self._phase is not AgentPhase.BID_PENDING:
            self._phase = AgentPhase.OBSERVED
        return anomaly

    def _record(self, observation: Observation) -> bool:
        if observation.building_id != self.building_id:
            raise ValidationError(
                f"observation for {observation.building_id!r} sent to agent {self.building_id!r}"
            )
        require_aligned(observation.timestamp, self.resolution_minutes)
        if self._history and observation.timestamp <= self._history[-1].timestamp:
            raise ValidationError(
                f"observations must be strictly increasing in time: {observation.timestamp} "
                f"after {self._history[-1].timestamp}"
            )
        if self._history and observation.timestamp != self._history[-1].timestamp + self.step:
            raise MissingSlotError(
                f"{self.building_id}: expected the observation for {self._history[-1].timestamp + self.step}, "
                f"got {observation.timestamp}. Every slot must be reported; impute a missing meter reading "
                f"upstream (and flag it in Observation.metadata) rather than skipping it."
            )
        anomaly = bool(
            self.spike_detector
            and self.spike_detector.is_anomaly([s.demand_kw for s in self._history], observation.measured_demand_kw)
        )
        if anomaly:
            self.stats.anomalies_detected += 1
            self.record("anomaly", observation.timestamp, demand_kw=observation.measured_demand_kw)
        self._history.append(observation.as_sample())
        self.record("observe", observation.timestamp, demand_kw=observation.measured_demand_kw)
        return anomaly

    def next_slot(self) -> TimeSlot:
        """The slot immediately after the latest observation."""
        self._require_history()
        return TimeSlot(self._history[-1].timestamp + self.step, self.resolution_minutes)

    # ===================================================== 2. forecast_demand
    def forecast_demand(self, horizon: int | None = None) -> Forecast:
        self._require_history()
        horizon = self.spec.forecast_horizon if horizon is None else horizon
        self._last_forecast = self.forecaster.forecast(list(self._history), horizon, self.resolution_minutes)
        return self._last_forecast

    # ======================================================= 3. classify_load
    def classify_load(self, forecast_kw: float, backlog_kw: float | None = None) -> LoadClassification:
        return self.classifier.classify(forecast_kw, self.backlog_kw if backlog_kw is None else backlog_kw)

    # ======================================================== 4. update_state
    def update_state(self, time_slot: TimeSlot | None = None) -> DemandState:
        """Forecast demand for ``time_slot`` (default: next slot) and classify it,
        including deferred energy that will still be valid in that slot."""
        self._require_history()
        slot = time_slot or self.next_slot()
        steps_ahead = self._steps_ahead(slot)
        forecast = self.forecast_demand(max(steps_ahead, self.spec.forecast_horizon))
        predicted = forecast.points[steps_ahead - 1].predicted_demand_kw
        backlog_kw = self._valid_backlog_kwh(slot.start) / self._hours
        cls = self.classify_load(predicted, backlog_kw)
        self._demand_state = DemandState(
            timestamp=slot.start,
            current_demand_kw=self._history[-1].demand_kw,
            predicted_demand_kw=predicted,
            backlog_kw=backlog_kw,
            desired_demand_kw=cls.total_kw,
            critical_demand_kw=cls.critical_kw,
            flexible_demand_kw=cls.flexible_kw,
            minimum_demand_kw=cls.minimum_kw,
            maximum_demand_kw=self.spec.capacity_kw,
        )
        return self._demand_state

    # ================================================== 5. calculate_priority
    def calculate_priority(self, state: DemandState | None = None) -> PriorityBreakdown:
        state = state or self._demand_state or self.update_state()
        factors = self.priority_model.factors(
            critical_kw=state.critical_demand_kw,
            base_demand_kw=min(state.predicted_demand_kw, state.desired_demand_kw),
            importance=self.spec.importance,
            backlog_kw=state.backlog_kw,
            backlog_limit_kw=self.spec.backlog_limit_kw,
            deprivation=self._deprivation,
        )
        return self.priority_model.score(factors)

    # ======================================================== 6. generate_bid
    def generate_bid(self, context: BidContext | None = None) -> Bid:
        """Run the decision pipeline and return a bid for ``context.time_slot``.

        Calling it again for the same slot before settlement produces a
        *revised* bid (``revision + 1``) that replaces the pending one. With
        ``context.scarcity > 0`` the building trims its request by
        ``scarcity * spec.scarcity_response * (requested - minimum)`` of
        flexible load (demand response); critical and minimum load are never
        trimmed. This is the P1 capability behind P2/P4 re-auction rounds.
        """
        self._require_history()
        context = context or BidContext(time_slot=self.next_slot())
        slot = context.time_slot
        if slot.duration_minutes != self.resolution_minutes:
            raise ValidationError(
                f"slot duration {slot.duration_minutes} != agent resolution {self.resolution_minutes}"
            )
        if slot.start < self.next_slot().start:
            raise ValidationError(f"{self.building_id}: cannot bid for {slot}; it is not after the latest observation")
        revision = 0
        if self._pending_bid is not None:
            if self._pending_bid.time_slot != slot:
                raise AgentStateError(f"{self.building_id}: bid for {self._pending_bid.time_slot} is not settled yet")
            revision = self._pending_bid.revision + 1
        state = self.update_state(slot)
        priority = self.calculate_priority(state)
        reduction = context.scarcity * self.spec.scarcity_response * (
            state.desired_demand_kw - state.minimum_demand_kw
        )
        forecast_point = self._last_forecast.points[self._steps_ahead(slot) - 1]
        bid = self.bid_generator.generate(
            spec=self.spec,
            state=state,
            priority=priority,
            context=context,
            created_at=self._history[-1].timestamp,
            deprivation=self._deprivation,
            revision=revision,
            voluntary_reduction_kw=reduction,
            extra_explanation={
                "forecast": {"method": self._last_forecast.method, "confidence": forecast_point.confidence}
            },
        )
        self._pending_bid, self._pending_state = bid, state
        self._phase = AgentPhase.BID_PENDING
        self.record("bid", slot.start, bid_id=bid.bid_id, requested_kw=bid.requested_power_kw,
                    reduction_kw=reduction, priority=bid.priority_score, wtp=bid.willingness_to_pay)
        return bid

    def abort_bid(self, reason: str = "") -> Bid:
        """Withdraw the pending bid (e.g. the auction failed and P4 will re-run it).

        The slot itself still happens: afterwards P4 should either bid again
        and settle, or report the realised demand with :meth:`observe` (that
        slot is then not included in settlement statistics).
        """
        if self._pending_bid is None:
            raise AgentStateError(f"{self.building_id}: no pending bid to abort")
        bid = self._pending_bid
        self._pending_bid = self._pending_state = None
        self._phase = AgentPhase.OBSERVED
        self.record("abort", bid.time_slot.start, bid_id=bid.bid_id, reason=reason)
        return bid

    # ================================================== 7. receive_allocation
    def receive_allocation(self, allocation: Allocation) -> AllocationOutcome:
        """Ex-ante preview of an allocation against the pending **bid** (no state change).
        This is not the service outcome; see :meth:`settle`."""
        if self._pending_bid is None:
            raise AgentStateError(f"{self.building_id}: no pending bid")
        return respond_to_allocation(self._pending_bid, allocation, self.spec.deferrable_fraction)

    # ============================================================ 8. settle
    def settle(self, allocation: Allocation, realised: Observation) -> Settlement:
        """Close the pending bid's slot against the demand that actually occurred.

        ``realised.measured_demand_kw`` is the building's actual *new* demand in
        the slot (it is also appended to the history used for forecasting).
        Serving order: critical load, then deferred backlog (oldest first),
        then new flexible load. Unserved new flexible load is split into
        deferred (``deferrable_fraction``, queued with a deadline) and
        curtailed. Unserved critical load is a shortfall, reported, not queued.
        """
        bid, state = self._pending_bid, self._pending_state
        if bid is None or state is None:
            raise AgentStateError(f"{self.building_id}: no pending bid to settle")
        check_allocation_matches(bid, allocation)
        slot = bid.time_slot
        if realised.timestamp != slot.start:
            raise ValidationError(f"realised observation {realised.timestamp} is not for the settled slot {slot}")
        if self._history and self._history[-1].timestamp + self.step != slot.start:
            raise MissingSlotError(f"{self.building_id}: slots before {slot} were not observed; cannot settle")
        spec, h = self.spec, slot.hours
        actual = realised.measured_demand_kw
        if actual > spec.capacity_kw + POWER_TOLERANCE_KW:
            raise ValidationError(f"realised demand {actual} kW exceeds capacity {spec.capacity_kw} kW")
        actual = min(actual, spec.capacity_kw)
        self._record(realised)

        expired_kwh = self._expire_backlog(slot.start)
        backlog_available_kw = self.backlog_energy_kwh / h
        cls = self.classifier.classify(actual, backlog_available_kw)
        backlog_attempted = cls.total_kw - actual

        allocated = allocation.allocated_power_kw
        served = min(allocated, cls.total_kw)
        critical_served = min(served, cls.critical_kw)
        flexible_served = served - critical_served
        backlog_served = min(flexible_served, backlog_attempted)
        new_flexible_served = flexible_served - backlog_served
        new_unserved = max(0.0, (actual - cls.critical_kw) - new_flexible_served)
        deferred = new_unserved * spec.deferrable_fraction
        curtailed = new_unserved - deferred

        self._consume_backlog(backlog_served * h)
        if deferred > POWER_TOLERANCE_KW:
            self._backlog.append(
                DeferredEnergy(slot.start, deferred * h, slot.start + spec.max_deferral_slots * self.step)
            )
        expired_kwh += self._cap_backlog(spec.backlog_limit_kw * h)

        price = allocation.clearing_price
        settlement = Settlement(
            bid_id=bid.bid_id,
            building_id=self.building_id,
            time_slot=slot,
            status=settlement_status(served, cls.total_kw, cls.minimum_kw, cls.critical_kw),
            forecast_demand_kw=state.predicted_demand_kw,
            actual_demand_kw=actual,
            backlog_available_kw=backlog_available_kw,
            backlog_attempted_kw=backlog_attempted,
            actual_total_kw=cls.total_kw,
            actual_critical_kw=cls.critical_kw,
            actual_minimum_kw=cls.minimum_kw,
            requested_kw=bid.requested_power_kw,
            allocated_kw=allocated,
            served_kw=served,
            unused_allocation_kw=allocated - served,
            critical_served_kw=critical_served,
            critical_shortfall_kw=cls.critical_kw - critical_served,
            backlog_served_kw=backlog_served,
            new_flexible_served_kw=new_flexible_served,
            deferred_kw=deferred,
            curtailed_kw=curtailed,
            backlog_expired_kwh=expired_kwh,
            backlog_after_kwh=self.backlog_energy_kwh,
            clearing_price=price,
            energy_cost=None if price is None else round(served * h * price, 6),
        )

        # Deprivation state: EWMA of the unserved share of realised need.
        a = self.deprivation_alpha
        self._deprivation = a * (1.0 - settlement.satisfaction_ratio) + (1.0 - a) * self._deprivation

        s = self.stats
        err = settlement.forecast_error_kw
        s.slots_settled += 1
        s.demand_kwh += actual * h
        s.served_kwh += served * h
        s.backlog_served_kwh += backlog_served * h
        s.deferred_kwh += deferred * h
        s.curtailed_kwh += curtailed * h
        s.expired_kwh += expired_kwh
        s.critical_shortfall_kwh += settlement.critical_shortfall_kw * h
        s.critical_shortfall_events += int(settlement.has_critical_shortfall)
        s.unused_allocation_kwh += settlement.unused_allocation_kw * h
        s.total_cost += settlement.energy_cost or 0.0
        s.forecast_abs_error_sum_kw += abs(err)
        s.forecast_sq_error_sum_kw2 += err * err

        self._last_settlement = settlement
        self._pending_bid = self._pending_state = None
        self._phase = AgentPhase.SETTLED
        self.record("settle", slot.start, status=settlement.status.value, actual_kw=actual, served_kw=served,
                    deferred_kw=deferred, backlog_kwh=settlement.backlog_after_kwh)
        if settlement.has_critical_shortfall:
            self.logger.warning("%s critical shortfall %.2f kW in %s", self.building_id,
                                settlement.critical_shortfall_kw, slot)
        return settlement

    def energy_balance_kwh(self) -> float:
        """Residual of the energy conservation law (should be ~0):
        ``demand - (served - backlog_served) - critical_shortfall - curtailed - deferred``.
        Deferred energy is then either served later (``backlog_served``), expired, or still queued:
        ``deferred = backlog_served + expired + backlog``."""
        s = self.stats
        same_slot = s.demand_kwh - (s.served_kwh - s.backlog_served_kwh) - s.critical_shortfall_kwh - s.curtailed_kwh
        queue = s.deferred_kwh - s.backlog_served_kwh - s.expired_kwh - self.backlog_energy_kwh
        return abs(same_slot - s.deferred_kwh) + abs(queue)

    # ------------------------------------------------------- backlog queue
    def _valid_backlog_kwh(self, at: datetime) -> float:
        return sum(e.energy_kwh for e in self._backlog if e.deadline >= at)

    def _expire_backlog(self, at: datetime) -> float:
        expired = sum(e.energy_kwh for e in self._backlog if e.deadline < at)
        if expired:
            self._backlog = deque(e for e in self._backlog if e.deadline >= at)
        return expired

    def _consume_backlog(self, energy_kwh: float) -> None:
        while energy_kwh > 1e-12 and self._backlog:
            head = self._backlog[0]
            if head.energy_kwh <= energy_kwh + 1e-12:
                energy_kwh -= head.energy_kwh
                self._backlog.popleft()
            else:
                self._backlog[0] = DeferredEnergy(head.origin, head.energy_kwh - energy_kwh, head.deadline)
                energy_kwh = 0.0

    def _cap_backlog(self, limit_kwh: float) -> float:
        dropped = 0.0
        while self.backlog_energy_kwh > limit_kwh + 1e-12 and self._backlog:
            excess = self.backlog_energy_kwh - limit_kwh
            head = self._backlog[0]
            take = min(excess, head.energy_kwh)
            dropped += take
            if take >= head.energy_kwh - 1e-12:
                self._backlog.popleft()
            else:
                self._backlog[0] = DeferredEnergy(head.origin, head.energy_kwh - take, head.deadline)
        return dropped

    # ============================================================ helpers
    def demand_outlook(self, horizon: int | None = None) -> list[DemandOutlookPoint]:
        """Forecast + classification for the next ``horizon`` slots (for P3's
        storage / supply planning). Backlog is attributed to the first slot."""
        forecast = self.forecast_demand(horizon)
        out = []
        for i, point in enumerate(forecast.points):
            backlog = self.backlog_kw if i == 0 else 0.0
            out.append(DemandOutlookPoint(point.timestamp, point.predicted_demand_kw, point.confidence,
                                          self.classify_load(point.predicted_demand_kw, backlog)))
        return out

    def snapshot(self) -> dict[str, Any]:
        stats = asdict(self.stats)
        stats.update(service_ratio=self.stats.service_ratio, forecast_mae_kw=self.stats.forecast_mae_kw,
                     forecast_rmse_kw=self.stats.forecast_rmse_kw)
        return {
            "building_id": self.building_id,
            "name": self.spec.name,
            "building_type": self.spec.building_type.value,
            "phase": self._phase.value,
            "current_demand_kw": self._history[-1].demand_kw if self._history else None,
            "last_observation_at": self._history[-1].timestamp.isoformat() if self._history else None,
            "backlog_kw": self.backlog_kw,
            "backlog_kwh": self.backlog_energy_kwh,
            "backlog": [{"origin": e.origin.isoformat(), "energy_kwh": e.energy_kwh,
                         "deadline": e.deadline.isoformat()} for e in self._backlog],
            "deprivation": self._deprivation,
            "demand_state": self._demand_state.to_dict() if self._demand_state else None,
            "pending_bid": self._pending_bid.to_dict() if self._pending_bid else None,
            "last_settlement": self._last_settlement.to_dict() if self._last_settlement else None,
            "stats": stats,
        }

    def _require_history(self) -> None:
        if not self._history:
            raise AgentStateError(f"{self.building_id}: no observations yet; call observe() first")

    def _steps_ahead(self, slot: TimeSlot) -> int:
        delta = slot.start - self._history[-1].timestamp
        return max(1, math.ceil(delta / self.step))

    def __repr__(self) -> str:
        return f"BuildingAgent({self.building_id!r}, phase={self._phase.value}, forecaster={self.forecaster!r})"
