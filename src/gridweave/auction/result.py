"""Explainable decision traces and rich market result structures for GridWeave.

Captures why each building received its specific power allocation without
relying on an LLM, generating transparent audit records directly from
algorithmic state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from gridweave.auction.metrics import MarketMetrics
from gridweave.auction.validator import ValidationReport
from gridweave.models.allocation import Allocation
from gridweave.models.common import TimeSlot
from gridweave.models.supply import ClearingResult, DispatchRequest


@dataclass(frozen=True)
class DecisionTrace:
    """Deterministic, explainable rationale for a single building's allocation."""

    building_id: str
    bid_id: str
    requested_kw: float
    critical_kw: float
    minimum_kw: float
    flexible_kw: float
    allocated_kw: float
    critical_served_kw: float
    minimum_served_kw: float
    flexible_served_kw: float
    critical_shortfall_kw: float
    priority_score: float
    willingness_to_pay: float
    composite_score: float
    clearing_price: float | None
    reason: str

    def to_dict(self) -> dict:
        return {
            "building_id": self.building_id,
            "bid_id": self.bid_id,
            "requested_kw": round(self.requested_kw, 3),
            "critical_kw": round(self.critical_kw, 3),
            "minimum_kw": round(self.minimum_kw, 3),
            "flexible_kw": round(self.flexible_kw, 3),
            "allocated_kw": round(self.allocated_kw, 3),
            "critical_served_kw": round(self.critical_served_kw, 3),
            "minimum_served_kw": round(self.minimum_served_kw, 3),
            "flexible_served_kw": round(self.flexible_served_kw, 3),
            "critical_shortfall_kw": round(self.critical_shortfall_kw, 3),
            "priority_score": round(self.priority_score, 3),
            "willingness_to_pay": round(self.willingness_to_pay, 3),
            "composite_score": round(self.composite_score, 4),
            "clearing_price": round(self.clearing_price, 3) if self.clearing_price is not None else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MarketResult:
    """Rich market result combining ClearingResult, metrics, and explainability traces."""

    time_slot: TimeSlot
    allocations: tuple[Allocation, ...]
    dispatch: tuple[DispatchRequest, ...]
    strategy_name: str
    metrics: MarketMetrics
    decision_traces: tuple[DecisionTrace, ...]
    clearing_price: float | None = None
    validation_report: ValidationReport | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def total_allocated_kw(self) -> float:
        return sum(a.allocated_power_kw for a in self.allocations)

    @property
    def total_dispatched_kw(self) -> float:
        return sum(d.requested_kw for d in self.dispatch)

    def to_clearing_result(self) -> ClearingResult:
        """Export as the standard P1/P4 ClearingResult model."""
        return ClearingResult(
            time_slot=self.time_slot,
            allocations=self.allocations,
            dispatch=self.dispatch,
            clearing_price=self.clearing_price,
            metadata=dict(self.metadata),
        )

    def get_trace(self, building_id: str) -> DecisionTrace | None:
        """Find the explanation trace for a given building."""
        for trace in self.decision_traces:
            if trace.building_id == building_id:
                return trace
        return None

    def to_dict(self) -> dict:
        return {
            "time_slot": self.time_slot.to_dict(),
            "strategy_name": self.strategy_name,
            "total_allocated_kw": round(self.total_allocated_kw, 3),
            "total_dispatched_kw": round(self.total_dispatched_kw, 3),
            "clearing_price": self.clearing_price,
            "metrics": self.metrics.to_dict(),
            "decision_traces": [t.to_dict() for t in self.decision_traces],
            "validation_report": self.validation_report.to_dict() if self.validation_report else None,
            "metadata": dict(self.metadata),
        }

    def format_summary(self) -> str:
        """Format a human-readable table of market outcomes."""
        lines = [
            f"=== Market Clearing Summary [{self.time_slot}] ===",
            f"Strategy: {self.strategy_name} | Clearing Price: {self.clearing_price or 'N/A'}",
            f"Requested: {self.metrics.total_requested_kw:.2f} kW | Allocated: {self.metrics.total_allocated_kw:.2f} kW "
            f"| Unmet: {self.metrics.total_unmet_kw:.2f} kW | Critical Shortfall: {self.metrics.critical_shortfall_kw:.2f} kW",
            f"Fairness (Jain's): {self.metrics.jains_fairness_index:.4f} | Service Ratio: {self.metrics.service_ratio:.2%} "
            f"| Utilization: {self.metrics.supply_utilization:.2%}",
            "",
            f"{'Building':<16} {'Req (kW)':<10} {'Crit (kW)':<10} {'Alloc (kW)':<12} {'Score':<8} {'WTP':<8} {'Explanation'}",
            "-" * 95,
        ]
        for t in self.decision_traces:
            lines.append(
                f"{t.building_id:<16} {t.requested_kw:<10.2f} {t.critical_kw:<10.2f} {t.allocated_kw:<12.2f} "
                f"{t.composite_score:<8.3f} {t.willingness_to_pay:<8.2f} {t.reason}"
            )
        return "\n".join(lines)
