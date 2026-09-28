"""Bid scoring model for the GridWeave Auction Subsystem (Person 2).

Defines an explainable, multi-criteria bid evaluation function that balances
safety criticality, agent priority, economic willingness-to-pay, and fairness
deprivation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from gridweave.models.bid import Bid


@dataclass(frozen=True)
class ScoreBreakdown:
    """Detailed breakdown of factors contributing to a bid's market score."""

    composite_score: float
    criticality_component: float
    priority_component: float
    wtp_component: float
    deprivation_component: float
    flexibility_discount: float
    raw_wtp: float
    normalized_wtp: float

    def to_dict(self) -> dict:
        return {
            "composite_score": round(self.composite_score, 4),
            "criticality_component": round(self.criticality_component, 4),
            "priority_component": round(self.priority_component, 4),
            "wtp_component": round(self.wtp_component, 4),
            "deprivation_component": round(self.deprivation_component, 4),
            "flexibility_discount": round(self.flexibility_discount, 4),
            "raw_wtp": round(self.raw_wtp, 4),
            "normalized_wtp": round(self.normalized_wtp, 4),
        }


class BidScorer:
    """Evaluates and ranks bids using a multi-criteria objective function.

    Formula:
        Score = w_crit * C_i + w_prio * P_i + w_wtp * W_norm_i + w_fair * D_i - w_flex * F_i

    Where:
        - C_i: Criticality proportion (critical_kw / requested_kw)
        - P_i: Priority score (urgency, importance from P1)
        - W_norm_i: Normalized willingness to pay (wtp / max_price)
        - D_i: Deprivation boost (starvation avoidance factor)
        - F_i: Flexibility score (curtailable fraction)
    """

    def __init__(
        self,
        weight_criticality: float = 0.35,
        weight_priority: float = 0.30,
        weight_wtp: float = 0.20,
        weight_fairness: float = 0.15,
        weight_flexibility: float = 0.05,
    ) -> None:
        self.w_crit = weight_criticality
        self.w_prio = weight_priority
        self.w_wtp = weight_wtp
        self.w_fair = weight_fairness
        self.w_flex = weight_flexibility

    def compute_score(
        self,
        bid: Bid,
        deprivation_boost: float = 0.0,
    ) -> ScoreBreakdown:
        """Compute the composite score and explainable breakdown for a bid."""
        # Criticality ratio [0, 1]
        crit_ratio = (
            bid.critical_power_kw / bid.requested_power_kw
            if bid.requested_power_kw > 1e-6
            else 0.0
        )
        crit_comp = self.w_crit * min(1.0, max(0.0, crit_ratio))

        # Priority component [0, 1]
        prio_comp = self.w_prio * min(1.0, max(0.0, bid.priority_score))

        # Normalized willingness-to-pay [0, 1]
        norm_wtp = (
            min(1.0, max(0.0, bid.willingness_to_pay / bid.maximum_price))
            if bid.maximum_price > 1e-6
            else 0.0
        )
        wtp_comp = self.w_wtp * norm_wtp

        # Deprivation boost [0, 1]
        dep_comp = self.w_fair * min(1.0, max(0.0, deprivation_boost))

        # Flexibility discount [0, 1]
        flex_discount = self.w_flex * min(1.0, max(0.0, bid.flexibility_score))

        # Composite score
        composite = max(0.0, crit_comp + prio_comp + wtp_comp + dep_comp - flex_discount)

        return ScoreBreakdown(
            composite_score=composite,
            criticality_component=crit_comp,
            priority_component=prio_comp,
            wtp_component=wtp_comp,
            deprivation_component=dep_comp,
            flexibility_discount=flex_discount,
            raw_wtp=bid.willingness_to_pay,
            normalized_wtp=norm_wtp,
        )

    def score_bids(
        self,
        bids: Sequence[Bid],
        deprivation_factors: Mapping[str, float] | None = None,
    ) -> dict[str, ScoreBreakdown]:
        """Score a collection of bids and return a mapping bid_id -> ScoreBreakdown."""
        dep_map = deprivation_factors or {}
        return {
            b.bid_id: self.compute_score(b, deprivation_boost=dep_map.get(b.building_id, 0.0))
            for b in bids
        }
