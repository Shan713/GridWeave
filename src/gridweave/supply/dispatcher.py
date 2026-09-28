"""Validated physical dispatch execution engine enforcing atomic source state transitions."""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence

from gridweave.contracts import validate_dispatch
from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, DispatchResult, SupplyOffer
from gridweave.utils.validation import POWER_TOLERANCE_KW, ValidationError


class DispatchExecutionError(ValidationError):
    """Raised when a dispatch instruction cannot be safely executed."""


class SupplyDispatcher:
    """Validates and executes dispatch requests across autonomous energy sources.

    Correctness & Atomicity Guarantees:
      1. Batch Pre-Validation: Checks all requests for validity (known source, slot match,
         non-negativity, within-offer limits, no duplicate source requests) BEFORE
         mutating any physical source agent.
      2. No Fabricated Supply: Dispatches actual agent methods and returns true physical delivery.
      3. Invariant Verification: Verifies result-to-request mapping via cross-workstream contracts.
    """

    def __init__(self, sources: Mapping[str, Any]) -> None:
        self.sources = dict(sources)

    def validate_batch(
        self,
        requests: Sequence[DispatchRequest],
        active_offers: Mapping[str, SupplyOffer],
        expected_slot: TimeSlot | None = None,
    ) -> list[str]:
        """Pre-validate all dispatch requests in the batch before executing state mutations."""
        errors: list[str] = []
        seen_sources: set[str] = set()

        for req in requests:
            # 1. Unknown source check
            if req.source_id not in self.sources:
                errors.append(f"Dispatch instruction names unknown source {req.source_id!r}")
                continue

            # 2. Duplicate source in single slot
            if req.source_id in seen_sources:
                errors.append(f"Multiple dispatch requests submitted for source {req.source_id!r}")
            seen_sources.add(req.source_id)

            # 3. Slot alignment
            if expected_slot is not None and req.time_slot != expected_slot:
                errors.append(
                    f"Request for source {req.source_id!r} is for slot {req.time_slot}, "
                    f"expected clearing slot {expected_slot}"
                )

            # 4. Non-negativity
            if req.requested_kw < 0.0 or math.isnan(req.requested_kw) or math.isinf(req.requested_kw):
                errors.append(f"Invalid requested power {req.requested_kw} for {req.source_id!r}")

            # 5. Adherence to accepted offer
            offer = active_offers.get(req.source_id)
            if offer is None:
                errors.append(f"Source {req.source_id!r} received dispatch but made no active offer")
            elif offer.time_slot != req.time_slot:
                errors.append(
                    f"Offer for source {req.source_id!r} is for slot {offer.time_slot}, "
                    f"not request slot {req.time_slot}"
                )
            elif req.requested_kw > offer.available_kw + POWER_TOLERANCE_KW:
                errors.append(
                    f"Dispatch {req.requested_kw:.3f} kW exceeds offered capacity {offer.available_kw:.3f} kW "
                    f"for {req.source_id!r}"
                )

        return errors

    def dispatch(
        self,
        requests: Sequence[DispatchRequest],
        active_offers: Mapping[str, SupplyOffer],
    ) -> list[DispatchResult]:
        """Execute dispatch batch atomically across all requested energy sources."""
        if not requests:
            return []

        expected_slot = requests[0].time_slot
        errors = self.validate_batch(requests, active_offers, expected_slot=expected_slot)
        if errors:
            raise DispatchExecutionError(f"Dispatch batch pre-validation failed: {'; '.join(errors)}")

        # Execute physical dispatch transactionally. The built-in sources are
        # mutable, so restore their pre-batch dictionaries if execution or
        # post-execution contract validation fails.
        source_states = {req.source_id: copy.deepcopy(self.sources[req.source_id].__dict__) for req in requests}
        try:
            results: list[DispatchResult] = []
            for req in requests:
                source = self.sources[req.source_id]
                results.append(source.dispatch(req))

            validate_dispatch(requests, results)
            return results
        except Exception as exc:
            for source_id, state in source_states.items():
                self.sources[source_id].__dict__.clear()
                self.sources[source_id].__dict__.update(state)
            if isinstance(exc, DispatchExecutionError):
                raise
            raise DispatchExecutionError(f"Dispatch batch execution failed and was rolled back: {exc}") from exc
