"""Physical supply invariants, validation rules, and feasibility checkers."""
from __future__ import annotations

from gridweave.models.supply import DispatchRequest, SupplyOffer
from gridweave.utils.validation import POWER_TOLERANCE_KW, ValidationError


class PhysicalConstraintViolation(ValidationError):
    """Raised when an operation violates thermodynamic or physical electrical limits."""


def validate_supply_offer(offer: SupplyOffer, max_physical_kw: float) -> None:
    """Validate that a generated SupplyOffer is physically deliverable."""
    if offer.available_kw < 0.0:
        raise PhysicalConstraintViolation(f"Offer {offer.source_id} has negative available_kw: {offer.available_kw}")
    if offer.available_kw > max_physical_kw + POWER_TOLERANCE_KW:
        raise PhysicalConstraintViolation(
            f"Offer {offer.source_id} ({offer.available_kw:.2f} kW) exceeds physical "
            f"plant rating ({max_physical_kw:.2f} kW)"
        )
    if offer.marginal_price < 0.0:
        raise PhysicalConstraintViolation(
            f"Offer {offer.source_id} has negative marginal price: {offer.marginal_price}"
        )


def validate_source_dispatch(
    request: DispatchRequest,
    offer: SupplyOffer,
    tolerance_kw: float = POWER_TOLERANCE_KW,
) -> None:
    """Validate that a dispatch request adheres to the previously accepted offer."""
    if request.source_id != offer.source_id:
        raise PhysicalConstraintViolation(
            f"Dispatch source {request.source_id!r} does not match offer source {offer.source_id!r}"
        )
    if request.time_slot != offer.time_slot:
        raise PhysicalConstraintViolation(
            f"Dispatch slot {request.time_slot} does not match offer slot {offer.time_slot}"
        )
    if request.requested_kw < 0.0:
        raise PhysicalConstraintViolation(f"Negative requested dispatch: {request.requested_kw}")
    if request.requested_kw > offer.available_kw + tolerance_kw:
        raise PhysicalConstraintViolation(
            f"Dispatch request {request.requested_kw:.3f} kW exceeds offered capacity {offer.available_kw:.3f} kW "
            f"for source {request.source_id!r}"
        )
