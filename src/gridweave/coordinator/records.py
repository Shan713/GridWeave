"""Immutable record types for the Coordinator.

Every simulation slot produces a ``SlotRecord``; a complete run produces a
``SimulationResult``.  These types are the single canonical representation of
what happened — dashboards, exports and metrics all read from them.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.settlement import Settlement
from gridweave.models.supply import ClearingResult, DispatchResult, SupplyOffer


# ---------------------------------------------------------------------------
# Event record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EventRecord:
    """One supply or demand event that was triggered during the simulation."""

    slot_index: int
    time_slot_start: datetime
    event_type: str
    description: str
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_index": self.slot_index,
            "time_slot_start": self.time_slot_start.isoformat(),
            "event_type": self.event_type,
            "description": self.description,
            "parameters": self.parameters,
        }


# ---------------------------------------------------------------------------
# Per-slot record
# ---------------------------------------------------------------------------

@dataclass
class SlotRecord:
    """Complete record for one 15-minute simulation slot.

    Built incrementally during :meth:`Coordinator.run_step` and appended to
    :attr:`SimulationResult.slots`.  Every field is derived exclusively from
    the P1/P2/P3 APIs — no business logic lives here.
    """

    slot_index: int
    time_slot: TimeSlot
    offers: list[SupplyOffer]
    bids: dict[str, Bid]
    clearing: ClearingResult | None
    dispatch_results: list[DispatchResult]
    settlements: dict[str, Settlement]
    scarcity: float
    rounds: int
    first_round_requested_kw: float
    events_triggered: list[str]
    failure: str | None = None
    settlement_failures: dict[str, str] = field(default_factory=dict)
    market_result: Any = None  # MarketResult if the stateful AuctionEngine path was used

    # ---------------------------------------------------------------- computed
    @property
    def supply_kw(self) -> float:
        return sum(o.available_kw for o in self.offers)

    @property
    def requested_kw(self) -> float:
        return sum(b.requested_power_kw for b in self.bids.values())

    @property
    def allocated_kw(self) -> float:
        return self.clearing.total_allocated_kw if self.clearing else 0.0

    @property
    def delivered_kw(self) -> float:
        return sum(r.delivered_kw for r in self.dispatch_results)

    @property
    def actual_demand_kw(self) -> float:
        """Realised new demand (excluding backlog) summed over all buildings."""
        return sum(s.actual_demand_kw for s in self.settlements.values())

    @property
    def actual_total_kw(self) -> float:
        """Realised need including backlog for all buildings."""
        return sum(s.actual_total_kw for s in self.settlements.values())

    @property
    def served_kw(self) -> float:
        return sum(s.served_kw for s in self.settlements.values())

    @property
    def critical_shortfall_kw(self) -> float:
        return sum(s.critical_shortfall_kw for s in self.settlements.values())

    @property
    def deferred_kw(self) -> float:
        return sum(s.deferred_kw for s in self.settlements.values())

    @property
    def curtailed_kw(self) -> float:
        return sum(s.curtailed_kw for s in self.settlements.values())

    @property
    def clearing_price(self) -> float | None:
        return self.clearing.clearing_price if self.clearing else None

    @property
    def service_ratio(self) -> float:
        tot = self.actual_total_kw
        return self.served_kw / tot if tot > 0 else 1.0

    @property
    def has_failure(self) -> bool:
        return self.failure is not None or bool(self.settlement_failures)

    @property
    def is_scarcity_slot(self) -> bool:
        return self.first_round_requested_kw > self.supply_kw + 1e-6

    # ---------------------------------------------------------------- export
    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_index": self.slot_index,
            "time_slot": self.time_slot.to_dict(),
            "supply_kw": round(self.supply_kw, 3),
            "requested_kw": round(self.requested_kw, 3),
            "first_round_requested_kw": round(self.first_round_requested_kw, 3),
            "allocated_kw": round(self.allocated_kw, 3),
            "delivered_kw": round(self.delivered_kw, 3),
            "actual_demand_kw": round(self.actual_demand_kw, 3),
            "actual_total_kw": round(self.actual_total_kw, 3),
            "served_kw": round(self.served_kw, 3),
            "critical_shortfall_kw": round(self.critical_shortfall_kw, 3),
            "deferred_kw": round(self.deferred_kw, 3),
            "curtailed_kw": round(self.curtailed_kw, 3),
            "service_ratio": round(self.service_ratio, 4),
            "scarcity": round(self.scarcity, 4),
            "rounds": self.rounds,
            "clearing_price": self.clearing_price,
            "events_triggered": list(self.events_triggered),
            "failure": self.failure,
            "settlement_failures": dict(self.settlement_failures),
            "settlements": {bid: s.to_dict() for bid, s in self.settlements.items()},
        }


# ---------------------------------------------------------------------------
# Per-building summary
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BuildingSummary:
    """Cumulative settlement-based metrics for one building over the full run."""

    building_id: str
    name: str
    building_type: str
    slots_settled: int
    demand_kwh: float
    served_kwh: float
    backlog_served_kwh: float
    deferred_kwh: float
    curtailed_kwh: float
    expired_kwh: float
    critical_shortfall_kwh: float
    critical_shortfall_events: int
    unused_allocation_kwh: float
    forecast_mae_kw: float | None
    forecast_rmse_kw: float | None
    total_cost: float
    final_backlog_kwh: float

    @property
    def service_ratio(self) -> float:
        return self.served_kwh / self.demand_kwh if self.demand_kwh > 0 else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "building_id": self.building_id,
            "name": self.name,
            "building_type": self.building_type,
            "slots_settled": self.slots_settled,
            "demand_kwh": round(self.demand_kwh, 2),
            "served_kwh": round(self.served_kwh, 2),
            "backlog_served_kwh": round(self.backlog_served_kwh, 2),
            "deferred_kwh": round(self.deferred_kwh, 2),
            "curtailed_kwh": round(self.curtailed_kwh, 2),
            "expired_kwh": round(self.expired_kwh, 2),
            "critical_shortfall_kwh": round(self.critical_shortfall_kwh, 3),
            "critical_shortfall_events": self.critical_shortfall_events,
            "unused_allocation_kwh": round(self.unused_allocation_kwh, 2),
            "forecast_mae_kw": round(self.forecast_mae_kw, 3) if self.forecast_mae_kw is not None else None,
            "forecast_rmse_kw": round(self.forecast_rmse_kw, 3) if self.forecast_rmse_kw is not None else None,
            "total_cost": round(self.total_cost, 2),
            "final_backlog_kwh": round(self.final_backlog_kwh, 2),
            "service_ratio": round(self.service_ratio, 4),
        }


# ---------------------------------------------------------------------------
# Complete simulation result
# ---------------------------------------------------------------------------

@dataclass
class SimulationResult:
    """Canonical output of one complete simulation run.

    All dashboards, exports and metric calculations derive their data from
    this object.  Nothing is recomputed independently.
    """

    scenario_name: str
    seed: int
    building_ids: list[str]
    resolution_minutes: int
    start_time: datetime
    end_time: datetime
    run_duration_s: float
    slots: list[SlotRecord]
    building_summaries: dict[str, BuildingSummary]
    supply_metrics: dict[str, Any]         # SupplyMetrics.to_dict() from P3
    supply_snapshot: dict[str, Any]        # provider.snapshot()
    event_log: list[EventRecord]
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- helpers
    @property
    def n_slots(self) -> int:
        return len(self.slots)

    @property
    def n_buildings(self) -> int:
        return len(self.building_ids)

    @property
    def total_demand_kwh(self) -> float:
        return sum(s.demand_kwh for s in self.building_summaries.values())

    @property
    def total_served_kwh(self) -> float:
        return sum(s.served_kwh for s in self.building_summaries.values())

    @property
    def overall_service_ratio(self) -> float:
        d = self.total_demand_kwh
        return self.total_served_kwh / d if d > 0 else 1.0

    @property
    def total_critical_shortfall_kwh(self) -> float:
        return sum(s.critical_shortfall_kwh for s in self.building_summaries.values())

    @property
    def total_deferred_kwh(self) -> float:
        return sum(s.deferred_kwh for s in self.building_summaries.values())

    @property
    def total_curtailed_kwh(self) -> float:
        return sum(s.curtailed_kwh for s in self.building_summaries.values())

    @property
    def scarcity_slots(self) -> int:
        return sum(1 for r in self.slots if r.is_scarcity_slot)

    @property
    def re_auction_slots(self) -> int:
        return sum(1 for r in self.slots if r.rounds > 1)

    @property
    def market_failure_slots(self) -> int:
        return sum(1 for r in self.slots if r.failure)

    @property
    def n_events(self) -> int:
        return len(self.event_log)

    # ---------------------------------------------------------------- export
    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "seed": self.seed,
            "building_ids": self.building_ids,
            "n_buildings": self.n_buildings,
            "resolution_minutes": self.resolution_minutes,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "run_duration_s": round(self.run_duration_s, 3),
            "n_slots": self.n_slots,
            "total_demand_kwh": round(self.total_demand_kwh, 2),
            "total_served_kwh": round(self.total_served_kwh, 2),
            "overall_service_ratio": round(self.overall_service_ratio, 4),
            "total_critical_shortfall_kwh": round(self.total_critical_shortfall_kwh, 3),
            "total_deferred_kwh": round(self.total_deferred_kwh, 2),
            "total_curtailed_kwh": round(self.total_curtailed_kwh, 2),
            "scarcity_slots": self.scarcity_slots,
            "re_auction_slots": self.re_auction_slots,
            "market_failure_slots": self.market_failure_slots,
            "n_events": self.n_events,
            "slots": [r.to_dict() for r in self.slots],
            "building_summaries": {bid: bs.to_dict() for bid, bs in self.building_summaries.items()},
            "supply_metrics": self.supply_metrics,
            "event_log": [e.to_dict() for e in self.event_log],
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def slot_series(self, field: str) -> list[float]:
        """Return a time-ordered list of a scalar field from each SlotRecord."""
        return [getattr(r, field) for r in self.slots]

    def building_series(self, building_id: str, field: str) -> list[float]:
        """Return a time-ordered list of a Settlement scalar for one building."""
        out = []
        for r in self.slots:
            s = r.settlements.get(building_id)
            out.append(getattr(s, field, 0.0) if s is not None else 0.0)
        return out
