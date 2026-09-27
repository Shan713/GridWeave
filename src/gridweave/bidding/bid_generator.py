"""Turn a building's demand state + priority into a :class:`Bid`.

Division of responsibility (the P1/P2 boundary):

* **P1 (here)** decides *what* to ask for — requested / minimum / critical /
  flexible power — and *how much it is worth* to the building
  (``willingness_to_pay``, capped by ``maximum_price``).
* **P2 (auction)** decides *how bids compete* and *who gets what*.

Bidding is truthful: ``requested_power_kw`` is the building's real desired
demand. Strategic behaviour, if any, lives only in the price.

Willingness to pay (currency/kWh)::

    pressure = wp * priority + ws * scarcity + wd * deprivation     (in [0, 1])
    wtp      = base_price + (max_price - base_price) * pressure
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping

from gridweave.bidding.priority import PriorityBreakdown
from gridweave.models.bid import Bid
from gridweave.models.building import BuildingSpec
from gridweave.models.common import TimeSlot
from gridweave.models.context import BidContext
from gridweave.models.demand import DemandState
from gridweave.utils.validation import ValidationError, clamp, require_non_negative


@dataclass(frozen=True)
class PricingPolicy:
    priority_weight: float = 0.5
    scarcity_weight: float = 0.3
    deprivation_weight: float = 0.2

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            require_non_negative(name, value)
        if sum(asdict(self).values()) <= 0:
            raise ValidationError("at least one pricing weight must be > 0")

    def pressure(self, priority: float, scarcity: float, deprivation: float) -> float:
        total = self.priority_weight + self.scarcity_weight + self.deprivation_weight
        raw = (self.priority_weight * priority + self.scarcity_weight * scarcity
               + self.deprivation_weight * deprivation) / total
        return clamp(raw)


def make_bid_id(building_id: str, slot: TimeSlot, revision: int) -> str:
    """Deterministic, human-readable, unique per (building, slot, revision)."""
    return f"{building_id}:{slot.start:%Y%m%dT%H%M}:r{revision}"


class BidGenerator:
    def __init__(self, pricing: PricingPolicy | None = None) -> None:
        self.pricing = pricing or PricingPolicy()

    def willingness_to_pay(self, spec: BuildingSpec, priority: float, scarcity: float, deprivation: float) -> float:
        pressure = self.pricing.pressure(priority, scarcity, deprivation)
        wtp = spec.base_price_per_kwh + (spec.max_price_per_kwh - spec.base_price_per_kwh) * pressure
        return round(min(wtp, spec.max_price_per_kwh), 6)

    def generate(
        self,
        spec: BuildingSpec,
        state: DemandState,
        priority: PriorityBreakdown,
        context: BidContext,
        created_at: datetime,
        deprivation: float = 0.0,
        revision: int = 0,
        extra_explanation: Mapping[str, Any] | None = None,
    ) -> Bid:
        requested = state.desired_demand_kw
        flexibility = (requested - state.minimum_demand_kw) / requested if requested > 0 else 0.0
        wtp = self.willingness_to_pay(spec, priority.score, context.scarcity, deprivation)
        explanation = {
            "priority": priority.to_dict(),
            "pricing": {
                "base_price": spec.base_price_per_kwh,
                "max_price": spec.max_price_per_kwh,
                "scarcity": context.scarcity,
                "deprivation": deprivation,
                "pressure": self.pricing.pressure(priority.score, context.scarcity, deprivation),
            },
            "demand": state.to_dict(),
        }
        explanation.update(extra_explanation or {})
        return Bid(
            bid_id=make_bid_id(spec.building_id, context.time_slot, revision),
            building_id=spec.building_id,
            time_slot=context.time_slot,
            created_at=context.created_at or created_at,
            requested_power_kw=requested,
            minimum_power_kw=state.minimum_demand_kw,
            critical_power_kw=state.critical_demand_kw,
            flexible_power_kw=state.flexible_demand_kw,
            priority_score=priority.score,
            flexibility_score=clamp(flexibility),
            willingness_to_pay=wtp,
            maximum_price=spec.max_price_per_kwh,
            revision=revision,
            explanation=explanation,
        )
