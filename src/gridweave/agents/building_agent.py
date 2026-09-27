"""The Building Agent: one class for every building, configured by data.

Lifecycle per market slot (driven by the coordinator, P4)::

    observe(observation)            # perceive measured demand
      -> generate_bid(context)      # = update_state -> forecast_demand ->
                                    #   classify_load -> calculate_priority -> bid
      -> apply_allocation(alloc)    # local response: serve critical, then
                                    #   flexible; defer / curtail the rest;
                                    #   learn from the outcome
      -> snapshot()                 # observable state for dashboards / P4

The agent owns no auction logic and no supply logic. It depends only on the
abstract forecaster interface and on the Bid / Allocation contracts.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from gridweave.agents.allocation_response import respond_to_allocation
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
from gridweave.models.common import DEFAULT_RESOLUTION_MINUTES, TimeSlot
from gridweave.models.context import BidContext
from gridweave.models.demand import DemandSample, DemandState, LoadClassification, Observation
from gridweave.utils.validation import ValidationError, require_fraction


class AgentPhase(str, Enum):
    IDLE = "idle"                # no observation yet
    OBSERVED = "observed"        # has fresh data, no bid outstanding
    BID_PENDING = "bid_pending"  # bid submitted, waiting for allocation
    SETTLED = "settled"          # allocation applied for the last bid


class AgentStateError(RuntimeError):
    """A lifecycle method was called in the wrong phase."""


@dataclass
class AgentStatistics:
    """Cumulative performance counters (energy in kWh, cost in currency)."""

    slots_settled: int = 0
    requested_energy_kwh: float = 0.0
    served_energy_kwh: float = 0.0
    deferred_energy_kwh: float = 0.0
    curtailed_energy_kwh: float = 0.0
    dropped_backlog_energy_kwh: float = 0.0
    critical_shortfall_energy_kwh: float = 0.0
    critical_shortfall_events: int = 0
    total_cost: float = 0.0
    anomalies_detected: int = 0

    @property
    def service_ratio(self) -> float:
        return 1.0 if self.requested_energy_kwh <= 0 else self.served_energy_kwh / self.requested_energy_kwh


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
        self._backlog_kw = 0.0
        self._deprivation = 0.0
        self._last_observation: Observation | None = None
        self._last_forecast: Forecast | None = None
        self._demand_state: DemandState | None = None
        self._pending_bid: Bid | None = None
        self._pending_state: DemandState | None = None
        self._last_outcome: AllocationOutcome | None = None
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
    def backlog_kw(self) -> float:
        return self._backlog_kw

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
    def last_forecast(self) -> Forecast | None:
        return self._last_forecast

    @property
    def last_outcome(self) -> AllocationOutcome | None:
        return self._last_outcome

    @property
    def last_observation(self) -> Observation | None:
        return self._last_observation

    @property
    def step(self) -> timedelta:
        return timedelta(minutes=self.resolution_minutes)

    # ============================================================ 1. observe
    def observe(self, observation: Observation) -> bool:
        """Record a measurement. Returns ``True`` if it was flagged as a spike."""
        if observation.building_id != self.building_id:
            raise ValidationError(
                f"observation for {observation.building_id!r} sent to agent {self.building_id!r}"
            )
        if self._history and observation.timestamp <= self._history[-1].timestamp:
            raise ValidationError(
                f"observations must be strictly increasing in time: {observation.timestamp} "
                f"after {self._history[-1].timestamp}"
            )
        anomaly = bool(
            self.spike_detector
            and self.spike_detector.is_anomaly([s.demand_kw for s in self._history], observation.measured_demand_kw)
        )
        if anomaly:
            self.stats.anomalies_detected += 1
            self.record("anomaly", observation.timestamp, demand_kw=observation.measured_demand_kw)
        self._history.append(observation.as_sample())
        self._last_observation = observation
        if self._phase is not AgentPhase.BID_PENDING:
            self._phase = AgentPhase.OBSERVED
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
        return self.classifier.classify(forecast_kw, self._backlog_kw if backlog_kw is None else backlog_kw)

    # ======================================================== 4. update_state
    def update_state(self, time_slot: TimeSlot | None = None) -> DemandState:
        """Forecast demand for ``time_slot`` (default: next slot) and classify it."""
        self._require_history()
        slot = time_slot or self.next_slot()
        steps_ahead = self._steps_ahead(slot)
        forecast = self.forecast_demand(max(steps_ahead, self.spec.forecast_horizon))
        predicted = forecast.points[steps_ahead - 1].predicted_demand_kw
        cls = self.classify_load(predicted)
        self._demand_state = DemandState(
            timestamp=slot.start,
            current_demand_kw=self._history[-1].demand_kw,
            predicted_demand_kw=predicted,
            backlog_kw=self._backlog_kw,
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
        """Run the full decision pipeline and return a bid for ``context.time_slot``.

        Calling it again for the same slot before an allocation arrives
        produces a *revised* bid (``revision + 1``) that replaces the pending
        one — this is the hook for P4's re-auction / negotiation rounds.
        """
        self._require_history()
        context = context or BidContext(time_slot=self.next_slot())
        state = self.update_state(context.time_slot)
        priority = self.calculate_priority(state)
        revision = 0
        if self._pending_bid is not None:
            if self._pending_bid.time_slot != context.time_slot:
                raise AgentStateError(
                    f"{self.building_id}: bid for {self._pending_bid.time_slot} still awaiting allocation"
                )
            revision = self._pending_bid.revision + 1
        forecast_point = self._last_forecast.points[self._steps_ahead(context.time_slot) - 1]
        bid = self.bid_generator.generate(
            spec=self.spec,
            state=state,
            priority=priority,
            context=context,
            created_at=self._history[-1].timestamp,
            deprivation=self._deprivation,
            revision=revision,
            extra_explanation={
                "forecast": {"method": self._last_forecast.method, "confidence": forecast_point.confidence}
            },
        )
        self._pending_bid, self._pending_state = bid, state
        self._phase = AgentPhase.BID_PENDING
        self.record("bid", context.time_slot.start, bid_id=bid.bid_id, requested_kw=bid.requested_power_kw,
                    priority=bid.priority_score, wtp=bid.willingness_to_pay)
        return bid

    # ================================================== 7. receive_allocation
    def receive_allocation(self, allocation: Allocation) -> AllocationOutcome:
        """Evaluate an allocation against the pending bid *without* changing state."""
        if self._pending_bid is None:
            raise AgentStateError(f"{self.building_id}: no pending bid to apply an allocation to")
        return respond_to_allocation(self._pending_bid, allocation, self.spec.deferrable_fraction)

    # ==================================================== 8. apply_allocation
    def apply_allocation(self, allocation: Allocation) -> AllocationOutcome:
        """Apply the auction result: serve, defer, curtail, and learn."""
        outcome = self.receive_allocation(allocation)
        state = self._pending_state
        assert state is not None
        hours = allocation.time_slot.hours

        # Backlog: the part that did not fit in this request is carried over,
        # plus whatever flexible load was deferred this slot.
        included = self.classifier.backlog_included(state.predicted_demand_kw, state.backlog_kw)
        new_backlog = (state.backlog_kw - included) + outcome.flexible_deferred_kw
        limit = self.spec.backlog_limit_kw
        dropped = max(0.0, new_backlog - limit)
        self._backlog_kw = min(new_backlog, limit)

        # Historical learning: smoothed share of demand left unserved.
        a = self.deprivation_alpha
        self._deprivation = a * (1.0 - outcome.satisfaction_ratio) + (1.0 - a) * self._deprivation

        s = self.stats
        s.slots_settled += 1
        s.requested_energy_kwh += outcome.requested_kw * hours
        s.served_energy_kwh += outcome.accepted_kw * hours
        s.deferred_energy_kwh += outcome.flexible_deferred_kw * hours
        s.curtailed_energy_kwh += outcome.flexible_curtailed_kw * hours
        s.dropped_backlog_energy_kwh += dropped * hours
        s.critical_shortfall_energy_kwh += outcome.critical_shortfall_kw * hours
        s.critical_shortfall_events += int(outcome.has_critical_shortfall)
        s.total_cost += outcome.energy_cost or 0.0

        self._last_outcome = outcome
        self._pending_bid = self._pending_state = None
        self._phase = AgentPhase.SETTLED
        self.record("allocation", allocation.time_slot.start, status=outcome.status.value,
                    allocated_kw=outcome.allocated_kw, deferred_kw=outcome.flexible_deferred_kw,
                    backlog_kw=self._backlog_kw)
        if outcome.has_critical_shortfall:
            self.logger.warning("%s critical shortfall %.2f kW in %s", self.building_id,
                                outcome.critical_shortfall_kw, allocation.time_slot)
        return outcome

    # ============================================================ helpers
    def decide(self, observation: Observation, context: BidContext | None = None) -> Bid:
        """Convenience: ``observe`` then ``generate_bid`` in one call."""
        self.observe(observation)
        return self.generate_bid(context)

    def demand_outlook(self, horizon: int | None = None) -> list[DemandOutlookPoint]:
        """Forecast + classification for the next ``horizon`` slots (for P3's
        storage / supply planning). Backlog is attributed to the first slot."""
        forecast = self.forecast_demand(horizon)
        out = []
        for i, point in enumerate(forecast.points):
            backlog = self._backlog_kw if i == 0 else 0.0
            out.append(DemandOutlookPoint(point.timestamp, point.predicted_demand_kw, point.confidence,
                                          self.classify_load(point.predicted_demand_kw, backlog)))
        return out

    def snapshot(self) -> dict[str, Any]:
        return {
            "building_id": self.building_id,
            "name": self.spec.name,
            "building_type": self.spec.building_type.value,
            "phase": self._phase.value,
            "current_demand_kw": self._history[-1].demand_kw if self._history else None,
            "last_observation_at": self._history[-1].timestamp.isoformat() if self._history else None,
            "backlog_kw": self._backlog_kw,
            "deprivation": self._deprivation,
            "demand_state": self._demand_state.to_dict() if self._demand_state else None,
            "pending_bid": self._pending_bid.to_dict() if self._pending_bid else None,
            "last_outcome": self._last_outcome.to_dict() if self._last_outcome else None,
            "stats": {**asdict(self.stats), "service_ratio": self.stats.service_ratio},
        }

    def _require_history(self) -> None:
        if not self._history:
            raise AgentStateError(f"{self.building_id}: no observations yet; call observe() first")

    def _steps_ahead(self, slot: TimeSlot) -> int:
        delta = slot.start - self._history[-1].timestamp
        return max(1, math.ceil(delta / self.step))

    def __repr__(self) -> str:
        return f"BuildingAgent({self.building_id!r}, phase={self._phase.value}, forecaster={self.forecaster!r})"
