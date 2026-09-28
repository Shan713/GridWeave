"""Constraint validation and emergency policy handling for the GridWeave Auction Subsystem.

Enforces physical capacity bounds, critical load protection, and emergency
rationing policies during severe campus supply shortages.
"""
from __future__ import annotations

from enum import Enum
from typing import Mapping, Sequence

from gridweave.models.bid import Bid
from gridweave.models.supply import SupplyOffer
from gridweave.utils.validation import POWER_TOLERANCE_KW, ValidationError


class EmergencyPolicyType(str, Enum):
    """How the auction rations power when available supply is below total critical demand."""

    PRO_RATA = "pro_rata"                    # Scale critical allocations by supply / total_critical
    PRIORITY_ORDER = "priority_order"        # Serve highest priority critical loads first


class ConstraintViolation(ValidationError):
    """Raised when an allocation violates mathematical market constraints."""


class ConstraintValidator:
    """Verifies that an allocation matrix conforms to all mathematical invariants."""

    def __init__(self, tolerance_kw: float = POWER_TOLERANCE_KW) -> None:
        self.tolerance_kw = tolerance_kw

    def check_invariants(
        self,
        bids: Sequence[Bid],
        offers: Sequence[SupplyOffer],
        allocations: Mapping[str, float],      # bid_id -> allocated_kw
        dispatches: Mapping[str, float],       # source_id -> dispatched_kw
    ) -> list[str]:
        """Validate all market clearing invariants. Returns list of violation messages."""
        violations: list[str] = []
        by_bid = {b.bid_id: b for b in bids}
        by_source = {o.source_id: o for o in offers}

        # 1. Check bid id coverage
        if set(allocations) != set(by_bid):
            missing = set(by_bid) - set(allocations)
            unknown = set(allocations) - set(by_bid)
            violations.append(f"Allocations mismatch bids. Missing: {sorted(missing)}, Unknown: {sorted(unknown)}")

        # 2. Check allocation bounds per bid
        for bid_id, kw in allocations.items():
            bid = by_bid.get(bid_id)
            if bid is None:
                continue
            if kw < -self.tolerance_kw:
                violations.append(f"Negative allocation for {bid_id}: {kw:.4f} kW")
            if kw > bid.requested_power_kw + self.tolerance_kw:
                violations.append(
                    f"Allocation {kw:.4f} kW exceeds requested {bid.requested_power_kw:.4f} kW for {bid_id}"
                )

        # 3. Check dispatch bounds per source
        for source_id, kw in dispatches.items():
            offer = by_source.get(source_id)
            if offer is None:
                violations.append(f"Dispatched source {source_id!r} has no supply offer")
                continue
            if kw < -self.tolerance_kw:
                violations.append(f"Negative dispatch for {source_id}: {kw:.4f} kW")
            if kw > offer.available_kw + self.tolerance_kw:
                violations.append(
                    f"Dispatch {kw:.4f} kW exceeds offer available {offer.available_kw:.4f} kW for {source_id}"
                )

        # 4. Check total supply bound
        total_supply = sum(o.available_kw for o in offers)
        total_allocated = sum(allocations.values())
        if total_allocated > total_supply + self.tolerance_kw:
            violations.append(
                f"Total allocated ({total_allocated:.4f} kW) exceeds total supply ({total_supply:.4f} kW)"
            )

        # 5. Check energy balance: total dispatched == total allocated
        total_dispatched = sum(dispatches.values())
        if abs(total_allocated - total_dispatched) > self.tolerance_kw:
            violations.append(
                f"Energy imbalance: total allocated ({total_allocated:.4f} kW) != dispatched ({total_dispatched:.4f} kW)"
            )

        return violations


class EmergencyPolicy:
    """Handles severe supply shortages where available supply < total critical load.

    Instead of crashing or failing silently, executes a transparent, documented
    emergency rationing strategy and reports exact shortfalls.
    """

    @staticmethod
    def ration_critical(
        bids: Sequence[Bid],
        available_supply: float,
        policy: EmergencyPolicyType = EmergencyPolicyType.PRO_RATA,
    ) -> dict[str, float]:
        """Ration available supply across critical demands.

        Returns:
            dict mapping bid_id -> rationed_critical_kw
        """
        total_critical = sum(b.critical_power_kw for b in bids)
        allocations: dict[str, float] = {b.bid_id: 0.0 for b in bids}

        if available_supply <= 0 or total_critical <= 0:
            return allocations

        if policy == EmergencyPolicyType.PRO_RATA:
            ratio = min(1.0, available_supply / total_critical)
            for b in bids:
                allocations[b.bid_id] = b.critical_power_kw * ratio

        elif policy == EmergencyPolicyType.PRIORITY_ORDER:
            # Sort descending by priority, breaking ties by critical load, then building id
            sorted_bids = sorted(
                bids,
                key=lambda b: (-b.priority_score, -b.critical_power_kw, b.building_id),
            )
            remaining = available_supply
            for b in sorted_bids:
                give = min(b.critical_power_kw, remaining)
                allocations[b.bid_id] = give
                remaining -= give

        return allocations
