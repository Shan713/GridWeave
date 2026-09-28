"""Bid validation layer for the GridWeave Auction Subsystem (Person 2).

Validates individual bids and collections of bids submitted to the market.
Ensures physical invariants, timing consistency, revision order, and lack
of duplicates or malformed quantities before market clearing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import SupplyOffer
from gridweave.utils.validation import POWER_TOLERANCE_KW


class BidValidationError(ValueError):
    """Raised or recorded when a bid violates market rules."""


@dataclass(frozen=True)
class ValidationReport:
    """Structured report produced by bid and offer validation."""

    is_valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    accepted_bids: tuple[Bid, ...] = field(default_factory=tuple)
    rejected_bids: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (bid_id, reason)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "accepted_bid_ids": [b.bid_id for b in self.accepted_bids],
            "rejected_bids": [{"bid_id": bid_id, "reason": reason} for bid_id, reason in self.rejected_bids],
        }


class BidValidator:
    """Dedicated validation layer for market inputs (bids and supply offers).

    Enforces:
    1. Non-empty identifiers and valid string format.
    2. Physical invariants: 0 <= critical <= minimum <= requested.
    3. Consistency: critical + flexible == requested within tolerance.
    4. Bounds: priority in [0, 1], flexibility in [0, 1], 0 <= WTP <= max_price.
    5. No NaN or infinity values.
    6. Capacity cap: requested <= capacity_kw when capacity is specified.
    7. TimeSlot matching: all bids match the clearing time slot.
    8. Revision semantics: for a given building and slot, newer revision (rev_new > rev_old)
       supersedes older revision; duplicate or stale revisions (rev_new <= rev_old) are rejected.
    """

    def __init__(self, tolerance_kw: float = POWER_TOLERANCE_KW) -> None:
        self.tolerance_kw = tolerance_kw

    def validate_bid(self, bid: Bid, expected_slot: TimeSlot | None = None) -> list[str]:
        """Validate a single bid. Returns a list of error messages (empty if valid)."""
        errors: list[str] = []

        # Identifier checks
        if not bid.bid_id or not isinstance(bid.bid_id, str):
            errors.append(f"Invalid or empty bid_id: {bid.bid_id!r}")
        if not bid.building_id or not isinstance(bid.building_id, str):
            errors.append(f"Invalid or empty building_id: {bid.building_id!r}")

        # Slot check
        if expected_slot is not None and bid.time_slot != expected_slot:
            errors.append(f"Bid {bid.bid_id} time_slot {bid.time_slot} does not match market slot {expected_slot}")

        # Finite number check
        for name, val in [
            ("requested_power_kw", bid.requested_power_kw),
            ("minimum_power_kw", bid.minimum_power_kw),
            ("critical_power_kw", bid.critical_power_kw),
            ("flexible_power_kw", bid.flexible_power_kw),
            ("priority_score", bid.priority_score),
            ("flexibility_score", bid.flexibility_score),
            ("willingness_to_pay", bid.willingness_to_pay),
            ("maximum_price", bid.maximum_price),
            ("voluntary_reduction_kw", bid.voluntary_reduction_kw),
        ]:
            if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                errors.append(f"Field {name} in bid {bid.bid_id} is not a finite number: {val}")

        # Non-negativity and bounds
        if bid.requested_power_kw < 0:
            errors.append(f"Bid {bid.bid_id} has negative requested power: {bid.requested_power_kw}")
        if bid.minimum_power_kw < 0:
            errors.append(f"Bid {bid.bid_id} has negative minimum power: {bid.minimum_power_kw}")
        if bid.critical_power_kw < 0:
            errors.append(f"Bid {bid.bid_id} has negative critical power: {bid.critical_power_kw}")
        if bid.flexible_power_kw < 0:
            errors.append(f"Bid {bid.bid_id} has negative flexible power: {bid.flexible_power_kw}")

        # Ordering invariants
        if bid.critical_power_kw > bid.minimum_power_kw + self.tolerance_kw:
            errors.append(
                f"Bid {bid.bid_id}: critical power ({bid.critical_power_kw:.3f}) "
                f"exceeds minimum ({bid.minimum_power_kw:.3f})"
            )
        if bid.minimum_power_kw > bid.requested_power_kw + self.tolerance_kw:
            errors.append(
                f"Bid {bid.bid_id}: minimum power ({bid.minimum_power_kw:.3f}) "
                f"exceeds requested ({bid.requested_power_kw:.3f})"
            )

        # Power balance
        if abs(bid.critical_power_kw + bid.flexible_power_kw - bid.requested_power_kw) > self.tolerance_kw:
            errors.append(
                f"Bid {bid.bid_id}: critical ({bid.critical_power_kw:.3f}) + flexible ({bid.flexible_power_kw:.3f}) "
                f"!= requested ({bid.requested_power_kw:.3f})"
            )

        # Priority and flexibility scores
        if not (0.0 <= bid.priority_score <= 1.0):
            errors.append(f"Bid {bid.bid_id}: priority score {bid.priority_score} not in [0, 1]")
        if not (0.0 <= bid.flexibility_score <= 1.0):
            errors.append(f"Bid {bid.bid_id}: flexibility score {bid.flexibility_score} not in [0, 1]")

        # Pricing bounds
        if bid.willingness_to_pay < 0:
            errors.append(f"Bid {bid.bid_id}: negative willingness to pay {bid.willingness_to_pay}")
        if bid.willingness_to_pay > bid.maximum_price + self.tolerance_kw:
            errors.append(
                f"Bid {bid.bid_id}: WTP ({bid.willingness_to_pay:.3f}) exceeds maximum price ({bid.maximum_price:.3f})"
            )

        # Capacity check if available
        if bid.capacity_kw is not None:
            if bid.requested_power_kw > bid.capacity_kw + self.tolerance_kw:
                errors.append(
                    f"Bid {bid.bid_id}: requested power ({bid.requested_power_kw:.3f}) "
                    f"exceeds capacity ({bid.capacity_kw:.3f})"
                )

        # Revision check
        if not isinstance(bid.revision, int) or isinstance(bid.revision, bool) or bid.revision < 0:
            errors.append(f"Bid {bid.bid_id}: invalid revision number {bid.revision}")

        return errors

    def validate_bids(
        self,
        bids: Sequence[Bid],
        expected_slot: TimeSlot,
        known_buildings: set[str] | None = None,
    ) -> ValidationReport:
        """Validate a collection of bids for a market clearing slot.

        Handles revisions: if multiple bids are present for the same building,
        only the highest valid revision is kept. Duplicate identical revisions or
        stale (lower) revisions are rejected.
        """
        errors: list[str] = []
        warnings: list[str] = []
        rejected: list[tuple[str, str]] = []

        # Track active bids per building: building_id -> Bid
        building_bids: dict[str, Bid] = {}
        seen_bid_ids: set[str] = set()

        for bid in bids:
            # 1. Structural / single-bid validation
            bid_errs = self.validate_bid(bid, expected_slot=expected_slot)
            if bid_errs:
                for err in bid_errs:
                    errors.append(err)
                    rejected.append((bid.bid_id, err))
                continue

            # 2. Known building check (if registry provided)
            if known_buildings is not None and bid.building_id not in known_buildings:
                err = f"Bid {bid.bid_id} from unknown building {bid.building_id!r}"
                errors.append(err)
                rejected.append((bid.bid_id, err))
                continue

            # 3. Exact duplicate bid_id check
            if bid.bid_id in seen_bid_ids:
                err = f"Duplicate bid_id submitted: {bid.bid_id}"
                errors.append(err)
                rejected.append((bid.bid_id, err))
                continue
            seen_bid_ids.add(bid.bid_id)

            # 4. Building revision semantics
            prev_bid = building_bids.get(bid.building_id)
            if prev_bid is not None:
                if bid.revision > prev_bid.revision:
                    warnings.append(
                        f"Building {bid.building_id}: revision {bid.revision} supersedes revision {prev_bid.revision}"
                    )
                    # Archive previous bid as superseded
                    rejected.append((prev_bid.bid_id, f"Superseded by revision {bid.revision}"))
                    building_bids[bid.building_id] = bid
                elif bid.revision == prev_bid.revision:
                    err = f"Duplicate revision {bid.revision} for building {bid.building_id} ({bid.bid_id})"
                    errors.append(err)
                    rejected.append((bid.bid_id, err))
                else:
                    err = (
                        f"Stale revision {bid.revision} submitted for building {bid.building_id} "
                        f"after revision {prev_bid.revision} was already received"
                    )
                    errors.append(err)
                    rejected.append((bid.bid_id, err))
            else:
                building_bids[bid.building_id] = bid

        accepted = tuple(building_bids.values())
        is_valid = len(errors) == 0

        return ValidationReport(
            is_valid=is_valid,
            errors=tuple(errors),
            warnings=tuple(warnings),
            accepted_bids=accepted,
            rejected_bids=tuple(rejected),
        )

    def validate_offers(self, offers: Sequence[SupplyOffer], expected_slot: TimeSlot) -> list[str]:
        """Validate supply offers submitted to the clearing engine."""
        errors: list[str] = []
        seen_sources: set[str] = set()

        for offer in offers:
            if not offer.source_id or not isinstance(offer.source_id, str):
                errors.append(f"Invalid or empty source_id in offer: {offer.source_id!r}")
            if offer.source_id in seen_sources:
                errors.append(f"Duplicate offer for source_id {offer.source_id!r}")
            seen_sources.add(offer.source_id)

            if offer.time_slot != expected_slot:
                errors.append(
                    f"Offer {offer.source_id} slot {offer.time_slot} does not match clearing slot {expected_slot}"
                )

            if offer.available_kw < 0:
                errors.append(f"Offer {offer.source_id} has negative available power: {offer.available_kw}")

            if offer.marginal_price < 0:
                errors.append(f"Offer {offer.source_id} has negative marginal price: {offer.marginal_price}")

            if math.isnan(offer.available_kw) or math.isinf(offer.available_kw):
                errors.append(f"Offer {offer.source_id} available_kw is not finite: {offer.available_kw}")

            if math.isnan(offer.marginal_price) or math.isinf(offer.marginal_price):
                errors.append(f"Offer {offer.source_id} marginal_price is not finite: {offer.marginal_price}")

        return errors
