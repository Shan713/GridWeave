"""Cross-workstream consistency checks.

Each model validates itself; these functions validate *relationships
between* objects produced by different people (bids from P1, offers from
P3, clearing from P2, dispatch results from P3). P4's coordinator should
call them every slot, and P2/P3 can call them in their own tests.
"""
from __future__ import annotations

from typing import Sequence

from gridweave.models.bid import Bid
from gridweave.models.supply import ClearingResult, DispatchRequest, DispatchResult, SupplyOffer
from gridweave.utils.validation import POWER_TOLERANCE_KW, ValidationError


class ContractViolation(ValidationError):
    """Another component returned data that breaks the integration contract."""


def validate_clearing(result: ClearingResult, bids: Sequence[Bid], offers: Sequence[SupplyOffer]) -> None:
    """Raise :class:`ContractViolation` unless ``result`` is a consistent clearing of ``bids`` against ``offers``.

    Checks: exactly one allocation per bid (echoing bid id, building and
    slot); no allocation above its request; dispatch only to offered sources
    and within each offer; total allocation within total supply; and energy
    balance (total dispatched == total allocated).
    """
    tol = POWER_TOLERANCE_KW
    by_bid = {b.bid_id: b for b in bids}
    if len(by_bid) != len(bids):
        raise ContractViolation("duplicate bid ids submitted to clearing")
    for b in bids:
        if b.time_slot != result.time_slot:
            raise ContractViolation(f"bid {b.bid_id} is for {b.time_slot}, clearing is for {result.time_slot}")
    allocated_ids = {a.bid_id for a in result.allocations}
    if allocated_ids != set(by_bid):
        missing, unknown = set(by_bid) - allocated_ids, allocated_ids - set(by_bid)
        raise ContractViolation(f"allocations must cover each bid exactly once (missing {sorted(missing)}, "
                                f"unknown {sorted(unknown)})")
    for a in result.allocations:
        bid = by_bid[a.bid_id]
        if a.building_id != bid.building_id:
            raise ContractViolation(f"allocation {a.bid_id} names building {a.building_id}, bid is {bid.building_id}")
        if a.allocated_power_kw > bid.requested_power_kw + tol:
            raise ContractViolation(f"allocation {a.allocated_power_kw:.3f} kW exceeds request "
                                    f"{bid.requested_power_kw:.3f} kW for {a.bid_id}")
    by_source = {o.source_id: o for o in offers}
    if len(by_source) != len(offers):
        raise ContractViolation("duplicate source ids in offers")
    for d in result.dispatch:
        offer = by_source.get(d.source_id)
        if offer is None:
            raise ContractViolation(f"dispatch to {d.source_id!r}, which made no offer")
        if d.requested_kw > offer.available_kw + tol:
            raise ContractViolation(f"dispatch {d.requested_kw:.3f} kW exceeds {d.source_id} offer "
                                    f"{offer.available_kw:.3f} kW")
    supply = sum(o.available_kw for o in offers)
    if result.total_allocated_kw > supply + tol:
        raise ContractViolation(f"total allocation {result.total_allocated_kw:.3f} kW exceeds total supply "
                                f"{supply:.3f} kW")
    if abs(result.total_dispatched_kw - result.total_allocated_kw) > 1e-3:
        raise ContractViolation(f"energy balance: dispatched {result.total_dispatched_kw:.3f} kW != allocated "
                                f"{result.total_allocated_kw:.3f} kW")


def validate_dispatch(requests: Sequence[DispatchRequest], results: Sequence[DispatchResult]) -> None:
    """Raise :class:`ContractViolation` unless there is exactly one matching result per request."""
    req = {r.source_id: r for r in requests}
    res = {r.source_id: r for r in results}
    if len(res) != len(results) or set(req) != set(res):
        raise ContractViolation(f"dispatch results {sorted(res)} do not match requests {sorted(req)}")
    for source_id, r in res.items():
        q = req[source_id]
        if r.time_slot != q.time_slot or abs(r.requested_kw - q.requested_kw) > POWER_TOLERANCE_KW:
            raise ContractViolation(f"dispatch result for {source_id} does not echo its request")
