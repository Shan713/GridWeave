"""Allocation strategies for the GridWeave Auction Subsystem (Person 2).

Implements:
1. Strategy A: GreedyAllocationStrategy (Tiered sequential allocation)
2. Strategy B: OptimizedAllocationStrategy (Exact bounded marginal welfare optimization)
3. Baseline 1: ProportionalAllocationStrategy (Uncoordinated proportional rationing)
4. Baseline 2: PriorityAllocationStrategy (Strict single-dimension priority allocation)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from gridweave.auction.constraints import EmergencyPolicy, EmergencyPolicyType
from gridweave.auction.result import DecisionTrace
from gridweave.auction.scoring import BidScorer
from gridweave.models.allocation import Allocation
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, SupplyOffer


@dataclass(frozen=True)
class StrategyResult:
    """Internal result produced by an allocation strategy before wrapping into MarketResult."""

    time_slot: TimeSlot
    allocations: tuple[Allocation, ...]
    dispatch: tuple[DispatchRequest, ...]
    clearing_price: float | None
    strategy_name: str
    decision_traces: tuple[DecisionTrace, ...]
    runtime_ms: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class AllocationStrategy(Protocol):
    """Protocol for all market allocation algorithms."""

    @property
    def name(self) -> str: ...

    def allocate(
        self,
        time_slot: TimeSlot,
        bids: Sequence[Bid],
        offers: Sequence[SupplyOffer],
        scorer: BidScorer | None = None,
        deprivation_factors: Mapping[str, float] | None = None,
    ) -> StrategyResult: ...


def merit_order_dispatch(
    time_slot: TimeSlot,
    total_allocation_kw: float,
    offers: Sequence[SupplyOffer],
) -> tuple[tuple[DispatchRequest, ...], dict[str, float], float | None]:
    """Execute economic merit-order dispatch across available supply offers.

    Cheapest marginal price is dispatched first. Ties broken deterministically
    by source_id.

    Returns:
        (dispatch_requests, source_shares, marginal_clearing_price)
    """
    sorted_offers = sorted(offers, key=lambda o: (o.marginal_price, o.source_id))
    dispatch_list: list[DispatchRequest] = []
    remaining_demand = total_allocation_kw
    marginal_clearing_price: float | None = None

    for offer in sorted_offers:
        if remaining_demand <= 1e-6:
            break
        take = min(offer.available_kw, remaining_demand)
        if take > 0:
            dispatch_list.append(DispatchRequest(offer.source_id, time_slot, take))
            marginal_clearing_price = offer.marginal_price
            remaining_demand -= take

    total_dispatched = sum(d.requested_kw for d in dispatch_list)
    # Absorb any minute float discrepancy into the last dispatch request so total_dispatched == total_allocated exactly
    if dispatch_list and abs(total_dispatched - total_allocation_kw) > 1e-6:
        last = dispatch_list[-1]
        diff = total_allocation_kw - total_dispatched
        adjusted = last.requested_kw + diff
        dispatch_list[-1] = DispatchRequest(last.source_id, time_slot, max(0.0, adjusted))
        total_dispatched = sum(d.requested_kw for d in dispatch_list)

    # Compute source shares for supply_mix
    shares: dict[str, float] = {}
    if total_allocation_kw > 1e-6:
        shares = {d.source_id: d.requested_kw / total_allocation_kw for d in dispatch_list}

    return tuple(dispatch_list), shares, marginal_clearing_price


def build_supply_mix(allocated_kw: float, shares: dict[str, float]) -> dict[str, float]:
    """Calculate supply mix for an allocation, ensuring exact sum reconciliation."""
    if allocated_kw <= 1e-6 or not shares:
        return {}
    mix = {src: allocated_kw * sh for src, sh in shares.items()}
    last_src = next(reversed(mix))
    mix[last_src] = max(0.0, allocated_kw - sum(v for k, v in mix.items() if k != last_src))
    return mix


class GreedyAllocationStrategy:
    """Strategy A: Three-tier sequential greedy auction allocation.

    Tiers:
    1. Critical Load Protection: Fully allocate critical demands if total_supply >= total_critical.
       If supply < total_critical, invoke EmergencyPolicy (pro-rata rationing).
    2. Minimum Operational Floor: With remaining supply, satisfy (minimum - critical) in order
       of priority or pro-rata.
    3. Flexible Allocation: With remaining supply, allocate (requested - minimum) greedily
       sorted by multi-criteria composite score.
    """

    def __init__(self, emergency_policy: EmergencyPolicyType = EmergencyPolicyType.PRO_RATA) -> None:
        self.emergency_policy = emergency_policy

    @property
    def name(self) -> str:
        return "GreedyTieredAuction"

    def allocate(
        self,
        time_slot: TimeSlot,
        bids: Sequence[Bid],
        offers: Sequence[SupplyOffer],
        scorer: BidScorer | None = None,
        deprivation_factors: Mapping[str, float] | None = None,
    ) -> StrategyResult:
        t0 = time.perf_counter()
        active_scorer = scorer or BidScorer()
        scores = active_scorer.score_bids(bids, deprivation_factors=deprivation_factors)

        total_supply = sum(o.available_kw for o in offers)
        total_critical = sum(b.critical_power_kw for b in bids)

        granted: dict[str, float] = {b.bid_id: 0.0 for b in bids}
        crit_served: dict[str, float] = {b.bid_id: 0.0 for b in bids}
        min_served: dict[str, float] = {b.bid_id: 0.0 for b in bids}
        flex_served: dict[str, float] = {b.bid_id: 0.0 for b in bids}
        reasons: dict[str, str] = {}

        # ---------------- Tier 1: Critical Demands ----------------
        if total_supply < total_critical:
            # Severe shortage: Emergency rationing
            crit_allocs = EmergencyPolicy.ration_critical(bids, total_supply, policy=self.emergency_policy)
            for b in bids:
                given = crit_allocs[b.bid_id]
                granted[b.bid_id] = given
                crit_served[b.bid_id] = given
                shortfall = b.critical_power_kw - given
                reasons[b.bid_id] = (
                    f"Severe emergency shortage: critical load rationed ({given:.2f}/{b.critical_power_kw:.2f} kW). "
                    f"Shortfall: {shortfall:.2f} kW."
                )
            remaining_supply = 0.0
        else:
            for b in bids:
                granted[b.bid_id] += b.critical_power_kw
                crit_served[b.bid_id] = b.critical_power_kw
            remaining_supply = total_supply - total_critical

            # ---------------- Tier 2: Minimum Operational Floor ----------------
            min_increments = {b.bid_id: max(0.0, b.minimum_power_kw - b.critical_power_kw) for b in bids}
            total_min_inc = sum(min_increments.values())

            if remaining_supply >= total_min_inc:
                for b in bids:
                    inc = min_increments[b.bid_id]
                    granted[b.bid_id] += inc
                    min_served[b.bid_id] = inc
                remaining_supply -= total_min_inc
            else:
                # Partial minimum coverage pro-rata
                ratio = remaining_supply / total_min_inc if total_min_inc > 0 else 0.0
                for b in bids:
                    inc = min_increments[b.bid_id] * ratio
                    granted[b.bid_id] += inc
                    min_served[b.bid_id] = inc
                remaining_supply = 0.0

            # ---------------- Tier 3: Flexible Load (Greedy by Composite Score) ----------------
            sorted_bids = sorted(
                bids,
                key=lambda b: (-scores[b.bid_id].composite_score, -b.willingness_to_pay, b.building_id),
            )
            for b in sorted_bids:
                flex_need = max(0.0, b.requested_power_kw - (b.critical_power_kw + min_served[b.bid_id]))
                give_flex = min(flex_need, remaining_supply)
                granted[b.bid_id] += give_flex
                flex_served[b.bid_id] = give_flex
                remaining_supply -= give_flex

                # Generate explainable rationale
                c_kw = b.critical_power_kw
                m_inc = min_served[b.bid_id]
                tot = granted[b.bid_id]
                score_val = scores[b.bid_id].composite_score
                if tot >= b.requested_power_kw - 1e-6:
                    reasons[b.bid_id] = (
                        f"Fully served: {c_kw:.1f} kW critical, {m_inc:.1f} kW min floor, {give_flex:.1f} kW flexible "
                        f"(score {score_val:.3f})."
                    )
                elif tot >= b.minimum_power_kw - 1e-6:
                    deferred = b.requested_power_kw - tot
                    reasons[b.bid_id] = (
                        f"Critical & min operational satisfied ({b.minimum_power_kw:.1f} kW). "
                        f"{give_flex:.1f}/{flex_need:.1f} kW flexible served "
                        f"(score {score_val:.3f}); {deferred:.1f} kW deferred."
                    )
                else:
                    reasons[b.bid_id] = (
                        f"Critical satisfied ({c_kw:.1f} kW). Minimum floor partially served "
                        f"({tot:.1f}/{b.minimum_power_kw:.1f} kW) due to campus supply constraints."
                    )

        total_alloc = sum(granted.values())
        dispatch, shares, clearing_price = merit_order_dispatch(time_slot, total_alloc, offers)

        # Build Allocation and DecisionTrace objects
        allocations: list[Allocation] = []
        traces: list[DecisionTrace] = []

        for b in bids:
            kw = granted[b.bid_id]
            mix = build_supply_mix(kw, shares)
            # Pay-as-bid or clearing price
            price = b.willingness_to_pay if clearing_price is None else clearing_price
            allocations.append(
                Allocation(
                    bid_id=b.bid_id,
                    building_id=b.building_id,
                    time_slot=time_slot,
                    allocated_power_kw=kw,
                    clearing_price=price,
                    supply_mix=mix,
                    metadata={"mechanism": self.name, "score": scores[b.bid_id].composite_score},
                )
            )
            shortfall = max(0.0, b.critical_power_kw - crit_served[b.bid_id])
            traces.append(
                DecisionTrace(
                    building_id=b.building_id,
                    bid_id=b.bid_id,
                    requested_kw=b.requested_power_kw,
                    critical_kw=b.critical_power_kw,
                    minimum_kw=b.minimum_power_kw,
                    flexible_kw=b.flexible_power_kw,
                    allocated_kw=kw,
                    critical_served_kw=crit_served[b.bid_id],
                    minimum_served_kw=min_served[b.bid_id] + crit_served[b.bid_id],
                    flexible_served_kw=flex_served[b.bid_id],
                    critical_shortfall_kw=shortfall,
                    priority_score=b.priority_score,
                    willingness_to_pay=b.willingness_to_pay,
                    composite_score=scores[b.bid_id].composite_score,
                    clearing_price=price,
                    reason=reasons[b.bid_id],
                )
            )

        runtime = (time.perf_counter() - t0) * 1000.0
        return StrategyResult(
            time_slot=time_slot,
            allocations=tuple(allocations),
            dispatch=dispatch,
            clearing_price=clearing_price,
            strategy_name=self.name,
            decision_traces=tuple(traces),
            runtime_ms=runtime,
            metadata={"total_supply_kw": total_supply},
        )


class OptimizedAllocationStrategy:
    """Strategy B: Exact Bounded Social Welfare Maximization.

    Solves the continuous bounded resource allocation problem:
        max sum_i U_i(x_i)
        subject to:
            0 <= x_i <= requested_i
            sum_i x_i <= Available_Supply
            x_i >= critical_i  (if supply >= sum critical)

    Where marginal utility is piecewise concave:
        - Critical tier [0, critical_i]: MU = 1000.0 + priority_i
        - Minimum floor [critical_i, minimum_i]: MU = 100.0 + priority_i
        - Flexible tier [minimum_i, requested_i]: MU = composite_score_i (from BidScorer)

    The continuous bounded knapsack algorithm yields the provably optimal solution
    in O(N log N) time without external solver overhead, maintaining 100% determinism.
    """

    def __init__(self, emergency_policy: EmergencyPolicyType = EmergencyPolicyType.PRO_RATA) -> None:
        self.emergency_policy = emergency_policy

    @property
    def name(self) -> str:
        return "OptimizedWelfareAuction"

    def allocate(
        self,
        time_slot: TimeSlot,
        bids: Sequence[Bid],
        offers: Sequence[SupplyOffer],
        scorer: BidScorer | None = None,
        deprivation_factors: Mapping[str, float] | None = None,
    ) -> StrategyResult:
        t0 = time.perf_counter()
        active_scorer = scorer or BidScorer()
        scores = active_scorer.score_bids(bids, deprivation_factors=deprivation_factors)

        total_supply = sum(o.available_kw for o in offers)
        total_critical = sum(b.critical_power_kw for b in bids)

        granted: dict[str, float] = {b.bid_id: 0.0 for b in bids}
        crit_served: dict[str, float] = {b.bid_id: 0.0 for b in bids}
        min_served: dict[str, float] = {b.bid_id: 0.0 for b in bids}
        flex_served: dict[str, float] = {b.bid_id: 0.0 for b in bids}
        reasons: dict[str, str] = {}

        if total_supply < total_critical:
            crit_allocs = EmergencyPolicy.ration_critical(bids, total_supply, policy=self.emergency_policy)
            for b in bids:
                given = crit_allocs[b.bid_id]
                granted[b.bid_id] = given
                crit_served[b.bid_id] = given
                shortfall = b.critical_power_kw - given
                reasons[b.bid_id] = (
                    f"Emergency supply deficit: global optimization allocated {given:.2f}/{b.critical_power_kw:.2f} kW "
                    f"under equitable critical rationing. Shortfall: {shortfall:.2f} kW."
                )
            remaining_supply = 0.0
        else:
            # Satisfy all critical tiers (marginal utility > 1000)
            for b in bids:
                granted[b.bid_id] = b.critical_power_kw
                crit_served[b.bid_id] = b.critical_power_kw
            remaining_supply = total_supply - total_critical

            # Construct segmented bounded blocks for optimization
            # Block structure: (marginal_utility, capacity_kw, bid_id, tier_name)
            blocks: list[tuple[float, float, str, str]] = []

            for b in bids:
                min_need = max(0.0, b.minimum_power_kw - b.critical_power_kw)
                if min_need > 0:
                    mu_min = 100.0 + b.priority_score
                    blocks.append((mu_min, min_need, b.bid_id, "minimum"))

                flex_need = max(0.0, b.requested_power_kw - b.minimum_power_kw)
                if flex_need > 0:
                    mu_flex = scores[b.bid_id].composite_score
                    blocks.append((mu_flex, flex_need, b.bid_id, "flexible"))

            # Sort blocks by descending marginal utility (deterministic tie-breaking by bid_id)
            blocks.sort(key=lambda item: (-item[0], item[2], item[3]))

            # Fill blocks optimally up to remaining supply
            for mu, cap, bid_id, tier in blocks:
                if remaining_supply <= 1e-6:
                    break
                take = min(cap, remaining_supply)
                granted[bid_id] += take
                if tier == "minimum":
                    min_served[bid_id] += take
                else:
                    flex_served[bid_id] += take
                remaining_supply -= take

            # Generate explainability traces
            for b in bids:
                c_kw = b.critical_power_kw
                m_kw = min_served[b.bid_id]
                f_kw = flex_served[b.bid_id]
                tot = granted[b.bid_id]
                score_val = scores[b.bid_id].composite_score
                if tot >= b.requested_power_kw - 1e-6:
                    reasons[b.bid_id] = (
                        f"Globally optimal allocation: 100% request satisfied ({tot:.1f} kW). "
                        f"Critical: {c_kw:.1f} kW, Min floor: {m_kw:.1f} kW, "
                        f"Flexible: {f_kw:.1f} kW (score {score_val:.3f})."
                    )
                elif tot >= b.minimum_power_kw - 1e-6:
                    def_kw = b.requested_power_kw - tot
                    reasons[b.bid_id] = (
                        f"Critical and operational floor guaranteed ({b.minimum_power_kw:.1f} kW). "
                        f"Optimal flexible allocation: {f_kw:.1f} kW served, "
                        f"{def_kw:.1f} kW deferred (score {score_val:.3f})."
                    )
                else:
                    reasons[b.bid_id] = (
                        f"Critical guaranteed ({c_kw:.1f} kW). Partial minimum floor "
                        f"({tot:.1f}/{b.minimum_power_kw:.1f} kW) "
                        f"allocated by social utility maximization."
                    )

        total_alloc = sum(granted.values())
        dispatch, shares, clearing_price = merit_order_dispatch(time_slot, total_alloc, offers)

        allocations: list[Allocation] = []
        traces: list[DecisionTrace] = []

        for b in bids:
            kw = granted[b.bid_id]
            mix = build_supply_mix(kw, shares)
            price = b.willingness_to_pay if clearing_price is None else clearing_price
            allocations.append(
                Allocation(
                    bid_id=b.bid_id,
                    building_id=b.building_id,
                    time_slot=time_slot,
                    allocated_power_kw=kw,
                    clearing_price=price,
                    supply_mix=mix,
                    metadata={"mechanism": self.name, "score": scores[b.bid_id].composite_score},
                )
            )
            shortfall = max(0.0, b.critical_power_kw - crit_served[b.bid_id])
            traces.append(
                DecisionTrace(
                    building_id=b.building_id,
                    bid_id=b.bid_id,
                    requested_kw=b.requested_power_kw,
                    critical_kw=b.critical_power_kw,
                    minimum_kw=b.minimum_power_kw,
                    flexible_kw=b.flexible_power_kw,
                    allocated_kw=kw,
                    critical_served_kw=crit_served[b.bid_id],
                    minimum_served_kw=min_served[b.bid_id] + crit_served[b.bid_id],
                    flexible_served_kw=flex_served[b.bid_id],
                    critical_shortfall_kw=shortfall,
                    priority_score=b.priority_score,
                    willingness_to_pay=b.willingness_to_pay,
                    composite_score=scores[b.bid_id].composite_score,
                    clearing_price=price,
                    reason=reasons[b.bid_id],
                )
            )

        runtime = (time.perf_counter() - t0) * 1000.0
        return StrategyResult(
            time_slot=time_slot,
            allocations=tuple(allocations),
            dispatch=dispatch,
            clearing_price=clearing_price,
            strategy_name=self.name,
            decision_traces=tuple(traces),
            runtime_ms=runtime,
            metadata={"total_supply_kw": total_supply},
        )


class ProportionalAllocationStrategy:
    """Baseline 1: Uncoordinated Proportional Rationing.

    Every building receives an equal percentage of its requested load.
    Does NOT protect critical load or use priority/price signals.
    Serves as an empirical baseline to show why intelligent auction coordination is needed.
    """

    @property
    def name(self) -> str:
        return "ProportionalBaseline"

    def allocate(
        self,
        time_slot: TimeSlot,
        bids: Sequence[Bid],
        offers: Sequence[SupplyOffer],
        scorer: BidScorer | None = None,
        deprivation_factors: Mapping[str, float] | None = None,
    ) -> StrategyResult:
        t0 = time.perf_counter()
        total_supply = sum(o.available_kw for o in offers)
        total_requested = sum(b.requested_power_kw for b in bids)

        ratio = min(1.0, total_supply / total_requested) if total_requested > 0 else 1.0

        granted: dict[str, float] = {}
        for b in bids:
            granted[b.bid_id] = b.requested_power_kw * ratio

        total_alloc = sum(granted.values())
        dispatch, shares, clearing_price = merit_order_dispatch(time_slot, total_alloc, offers)

        allocations: list[Allocation] = []
        traces: list[DecisionTrace] = []

        for b in bids:
            kw = granted[b.bid_id]
            mix = build_supply_mix(kw, shares)
            crit_s = min(kw, b.critical_power_kw)
            shortfall = max(0.0, b.critical_power_kw - crit_s)
            price = b.willingness_to_pay if clearing_price is None else clearing_price

            allocations.append(
                Allocation(
                    bid_id=b.bid_id,
                    building_id=b.building_id,
                    time_slot=time_slot,
                    allocated_power_kw=kw,
                    clearing_price=price,
                    supply_mix=mix,
                    metadata={"mechanism": self.name, "proportional_ratio": ratio},
                )
            )
            traces.append(
                DecisionTrace(
                    building_id=b.building_id,
                    bid_id=b.bid_id,
                    requested_kw=b.requested_power_kw,
                    critical_kw=b.critical_power_kw,
                    minimum_kw=b.minimum_power_kw,
                    flexible_kw=b.flexible_power_kw,
                    allocated_kw=kw,
                    critical_served_kw=crit_s,
                    minimum_served_kw=min(kw, b.minimum_power_kw),
                    flexible_served_kw=max(0.0, kw - b.minimum_power_kw),
                    critical_shortfall_kw=shortfall,
                    priority_score=b.priority_score,
                    willingness_to_pay=b.willingness_to_pay,
                    composite_score=0.0,
                    clearing_price=price,
                    reason=(
                        f"Uncoordinated proportional rationing "
                        f"({ratio:.1%} of requested {b.requested_power_kw:.1f} kW)."
                    ),
                )
            )

        runtime = (time.perf_counter() - t0) * 1000.0
        return StrategyResult(
            time_slot=time_slot,
            allocations=tuple(allocations),
            dispatch=dispatch,
            clearing_price=clearing_price,
            strategy_name=self.name,
            decision_traces=tuple(traces),
            runtime_ms=runtime,
            metadata={"proportional_ratio": ratio},
        )


class PriorityAllocationStrategy:
    """Baseline 2: Strict Priority-Only Allocation.

    Bids are sorted purely by P1's priority_score. Full requests are served
    sequentially until supply is exhausted. Does not separate critical tiers,
    meaning low-priority buildings receive 0 kW even if they have life-safety critical load.
    """

    @property
    def name(self) -> str:
        return "PriorityOnlyBaseline"

    def allocate(
        self,
        time_slot: TimeSlot,
        bids: Sequence[Bid],
        offers: Sequence[SupplyOffer],
        scorer: BidScorer | None = None,
        deprivation_factors: Mapping[str, float] | None = None,
    ) -> StrategyResult:
        t0 = time.perf_counter()
        total_supply = sum(o.available_kw for o in offers)
        remaining = total_supply

        sorted_bids = sorted(
            bids,
            key=lambda b: (-b.priority_score, -b.willingness_to_pay, b.building_id),
        )

        granted: dict[str, float] = {b.bid_id: 0.0 for b in bids}
        reasons: dict[str, str] = {}

        for b in sorted_bids:
            give = min(b.requested_power_kw, remaining)
            granted[b.bid_id] = give
            remaining -= give
            if give >= b.requested_power_kw - 1e-6:
                reasons[b.bid_id] = (
                    f"Full request granted ({give:.1f} kW) based on high priority "
                    f"({b.priority_score:.2f})."
                )
            elif give > 0:
                reasons[b.bid_id] = (
                    f"Partial allocation ({give:.1f}/{b.requested_power_kw:.1f} kW) as supply was exhausted "
                    f"at priority tier {b.priority_score:.2f}."
                )
            else:
                reasons[b.bid_id] = f"Completely starved (0 kW) due to lower priority rank ({b.priority_score:.2f})."

        total_alloc = sum(granted.values())
        dispatch, shares, clearing_price = merit_order_dispatch(time_slot, total_alloc, offers)

        allocations: list[Allocation] = []
        traces: list[DecisionTrace] = []

        for b in bids:
            kw = granted[b.bid_id]
            mix = build_supply_mix(kw, shares)
            crit_s = min(kw, b.critical_power_kw)
            shortfall = max(0.0, b.critical_power_kw - crit_s)
            price = b.willingness_to_pay if clearing_price is None else clearing_price

            allocations.append(
                Allocation(
                    bid_id=b.bid_id,
                    building_id=b.building_id,
                    time_slot=time_slot,
                    allocated_power_kw=kw,
                    clearing_price=price,
                    supply_mix=mix,
                    metadata={"mechanism": self.name, "priority": b.priority_score},
                )
            )
            traces.append(
                DecisionTrace(
                    building_id=b.building_id,
                    bid_id=b.bid_id,
                    requested_kw=b.requested_power_kw,
                    critical_kw=b.critical_power_kw,
                    minimum_kw=b.minimum_power_kw,
                    flexible_kw=b.flexible_power_kw,
                    allocated_kw=kw,
                    critical_served_kw=crit_s,
                    minimum_served_kw=min(kw, b.minimum_power_kw),
                    flexible_served_kw=max(0.0, kw - b.minimum_power_kw),
                    critical_shortfall_kw=shortfall,
                    priority_score=b.priority_score,
                    willingness_to_pay=b.willingness_to_pay,
                    composite_score=b.priority_score,
                    clearing_price=price,
                    reason=reasons[b.bid_id],
                )
            )

        runtime = (time.perf_counter() - t0) * 1000.0
        return StrategyResult(
            time_slot=time_slot,
            allocations=tuple(allocations),
            dispatch=dispatch,
            clearing_price=clearing_price,
            strategy_name=self.name,
            decision_traces=tuple(traces),
            runtime_ms=runtime,
            metadata={"total_supply_kw": total_supply},
        )
