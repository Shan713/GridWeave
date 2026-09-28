"""Market metrics and performance evaluation for the GridWeave Auction Subsystem.

Computes comprehensive engineering, economic, and equity metrics for any market
clearing result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from gridweave.auction.fairness import jains_fairness_index
from gridweave.models.allocation import Allocation
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import SupplyOffer


@dataclass(frozen=True)
class MarketMetrics:
    """Comprehensive performance metrics for a single market clearing slot."""

    total_requested_kw: float
    total_allocated_kw: float
    total_unmet_kw: float
    critical_shortfall_kw: float
    minimum_shortfall_kw: float
    supply_utilization: float
    service_ratio: float
    total_cost: float
    average_price: float
    peak_market_demand_kw: float
    jains_fairness_index: float
    max_deprivation_ratio: float
    allocation_efficiency: float
    runtime_ms: float

    def to_dict(self) -> dict:
        return {
            "total_requested_kw": round(self.total_requested_kw, 3),
            "total_allocated_kw": round(self.total_allocated_kw, 3),
            "total_unmet_kw": round(self.total_unmet_kw, 3),
            "critical_shortfall_kw": round(self.critical_shortfall_kw, 3),
            "minimum_shortfall_kw": round(self.minimum_shortfall_kw, 3),
            "supply_utilization": round(self.supply_utilization, 4),
            "service_ratio": round(self.service_ratio, 4),
            "total_cost": round(self.total_cost, 2),
            "average_price": round(self.average_price, 3),
            "peak_market_demand_kw": round(self.peak_market_demand_kw, 3),
            "jains_fairness_index": round(self.jains_fairness_index, 4),
            "max_deprivation_ratio": round(self.max_deprivation_ratio, 4),
            "allocation_efficiency": round(self.allocation_efficiency, 4),
            "runtime_ms": round(self.runtime_ms, 3),
        }


class MarketMetricsCalculator:
    """Calculates metrics from bids, offers, allocations, and execution metadata."""

    @staticmethod
    def calculate(
        time_slot: TimeSlot,
        bids: Sequence[Bid],
        offers: Sequence[SupplyOffer],
        allocations: Sequence[Allocation],
        runtime_ms: float = 0.0,
    ) -> MarketMetrics:
        alloc_map = {a.bid_id: a.allocated_power_kw for a in allocations}
        price_map = {a.bid_id: (a.clearing_price or 0.0) for a in allocations}

        total_requested = sum(b.requested_power_kw for b in bids)
        total_allocated = sum(allocations) if False else sum(a.allocated_power_kw for a in allocations)
        total_supply = sum(o.available_kw for o in offers)
        total_unmet = max(0.0, total_requested - total_allocated)

        critical_shortfall = 0.0
        minimum_shortfall = 0.0
        individual_satisfaction_ratios: list[float] = []

        hours = time_slot.hours
        total_cost = 0.0

        for b in bids:
            kw = alloc_map.get(b.bid_id, 0.0)
            price = price_map.get(b.bid_id, 0.0)
            total_cost += kw * price * hours

            # Critical shortfall: how much of critical was NOT served
            if kw < b.critical_power_kw:
                critical_shortfall += (b.critical_power_kw - kw)

            # Minimum shortfall
            if kw < b.minimum_power_kw:
                minimum_shortfall += (b.minimum_power_kw - kw)

            ratio = (kw / b.requested_power_kw) if b.requested_power_kw > 1e-6 else 1.0
            individual_satisfaction_ratios.append(min(1.0, max(0.0, ratio)))

        supply_utilization = (total_allocated / total_supply) if total_supply > 1e-6 else 0.0
        service_ratio = (total_allocated / total_requested) if total_requested > 1e-6 else 1.0

        total_allocated_kwh = total_allocated * hours
        average_price = (total_cost / total_allocated_kwh) if total_allocated_kwh > 1e-6 else 0.0

        peak_demand = max((b.requested_power_kw for b in bids), default=0.0)
        jain = jains_fairness_index(individual_satisfaction_ratios)

        max_deprivation = (
            max((1.0 - r for r in individual_satisfaction_ratios), default=0.0)
            if individual_satisfaction_ratios
            else 0.0
        )

        # Efficiency: total allocated power relative to min(total_requested, total_supply)
        max_possible_allocation = min(total_requested, total_supply)
        efficiency = (total_allocated / max_possible_allocation) if max_possible_allocation > 1e-6 else 1.0

        return MarketMetrics(
            total_requested_kw=total_requested,
            total_allocated_kw=total_allocated,
            total_unmet_kw=total_unmet,
            critical_shortfall_kw=critical_shortfall,
            minimum_shortfall_kw=minimum_shortfall,
            supply_utilization=min(1.0, max(0.0, supply_utilization)),
            service_ratio=min(1.0, max(0.0, service_ratio)),
            total_cost=total_cost,
            average_price=average_price,
            peak_market_demand_kw=peak_demand,
            jains_fairness_index=jain,
            max_deprivation_ratio=max_deprivation,
            allocation_efficiency=min(1.0, max(0.0, efficiency)),
            runtime_ms=runtime_ms,
        )
