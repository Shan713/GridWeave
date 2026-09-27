"""Ex-ante interpretation of an allocation against the bid it answers.

Service metrics are *not* computed here; see ``BuildingAgent.settle`` and
:class:`gridweave.models.Settlement`, which judge against realised demand.

Serving order is fixed and safety-first:

1. Critical load is served first, up to the allocation.
2. Whatever is left serves flexible load.
3. Unserved flexible load is split: ``deferrable_fraction`` of it is shifted
   to later slots (backlog), the rest is curtailed (lost / comfort reduced).
4. Any allocation above the request is left unused and reported.

Example (from the project brief)::

    requested 40, critical 25, flexible 15, allocated 32
    -> critical served 25, flexible served 7, flexible deferred 8 (deferrable_fraction = 1)
"""
from __future__ import annotations

from gridweave.models.allocation import Allocation, AllocationOutcome, AllocationStatus
from gridweave.models.bid import Bid
from gridweave.utils.validation import POWER_TOLERANCE_KW, ValidationError, require_fraction


class AllocationMismatchError(ValidationError):
    """The allocation does not belong to the bid it is applied to."""


def classify_status(bid: Bid, accepted_kw: float) -> AllocationStatus:
    tol = POWER_TOLERANCE_KW
    if bid.requested_power_kw <= tol or accepted_kw >= bid.requested_power_kw - tol:
        return AllocationStatus.FULL
    if accepted_kw <= tol:
        return AllocationStatus.NONE
    if accepted_kw >= bid.minimum_power_kw - tol:
        return AllocationStatus.PARTIAL
    if accepted_kw >= bid.critical_power_kw - tol:
        return AllocationStatus.BELOW_MINIMUM
    return AllocationStatus.CRITICAL_SHORTFALL


def check_allocation_matches(bid: Bid, allocation: Allocation) -> None:
    """Raise :class:`AllocationMismatchError` unless ``allocation`` answers ``bid``."""
    if allocation.bid_id != bid.bid_id:
        raise AllocationMismatchError(f"allocation for bid {allocation.bid_id!r} applied to bid {bid.bid_id!r}")
    if allocation.building_id != bid.building_id:
        raise AllocationMismatchError(
            f"allocation for building {allocation.building_id!r} applied to {bid.building_id!r}"
        )
    if allocation.time_slot != bid.time_slot:
        raise AllocationMismatchError(f"allocation slot {allocation.time_slot} != bid slot {bid.time_slot}")


def respond_to_allocation(bid: Bid, allocation: Allocation, deferrable_fraction: float) -> AllocationOutcome:
    """*Ex-ante* view: what this allocation means relative to the **bid**.

    Pure; does not mutate anything. Useful for P2 to reason about a clearing
    before the slot happens. It is **not** the service outcome: that comes
    from :meth:`BuildingAgent.settle`, which uses realised demand.
    """
    require_fraction("deferrable_fraction", deferrable_fraction)
    check_allocation_matches(bid, allocation)

    accepted = min(allocation.allocated_power_kw, bid.requested_power_kw)
    critical_served = min(accepted, bid.critical_power_kw)
    flexible_served = min(accepted - critical_served, bid.flexible_power_kw)
    flexible_unserved = bid.flexible_power_kw - flexible_served
    deferred = flexible_unserved * deferrable_fraction
    energy_cost = None
    if allocation.clearing_price is not None:
        energy_cost = round(accepted * bid.time_slot.hours * allocation.clearing_price, 6)

    return AllocationOutcome(
        bid_id=bid.bid_id,
        building_id=bid.building_id,
        time_slot=bid.time_slot,
        status=classify_status(bid, accepted),
        requested_kw=bid.requested_power_kw,
        minimum_kw=bid.minimum_power_kw,
        allocated_kw=allocation.allocated_power_kw,
        accepted_kw=accepted,
        unused_allocation_kw=allocation.allocated_power_kw - accepted,
        critical_requested_kw=bid.critical_power_kw,
        critical_served_kw=critical_served,
        critical_shortfall_kw=bid.critical_power_kw - critical_served,
        flexible_requested_kw=bid.flexible_power_kw,
        flexible_served_kw=flexible_served,
        flexible_deferred_kw=deferred,
        flexible_curtailed_kw=flexible_unserved - deferred,
        energy_cost=energy_cost,
    )
