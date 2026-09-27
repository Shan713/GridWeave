"""MockAuctioneer: a simple, transparent stand-in for P2's market.

Clearing rule (deliberately naive, *not* a proposal for the real mechanism):

1. Critical tier — every bid's ``critical_power_kw``; if supply is short it
   is shared pro rata.
2. Minimum tier — ``minimum - critical``; pro rata on what is left.
3. Flexible tier — ``requested - minimum``; greedily by priority, then
   willingness to pay, then building id (deterministic tie-break).

Pay-as-bid: ``clearing_price = willingness_to_pay``.
"""
from __future__ import annotations

from gridweave.models.allocation import Allocation
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.utils.validation import ValidationError, require_non_negative


class MockAuctioneer:
    def __init__(self) -> None:
        self._book: dict[TimeSlot, dict[str, Bid]] = {}
        self.history: list[tuple[TimeSlot, list[Bid], list[Allocation]]] = []

    def submit_bid(self, bid: Bid) -> None:
        """Accept a bid; a later revision from the same building replaces the earlier one."""
        if not isinstance(bid, Bid):
            raise ValidationError(f"expected Bid, got {type(bid).__name__}")
        slot_book = self._book.setdefault(bid.time_slot, {})
        existing = slot_book.get(bid.building_id)
        if existing is not None and existing.revision > bid.revision:
            raise ValidationError(f"stale bid revision {bid.revision} for {bid.building_id}")
        slot_book[bid.building_id] = bid

    def pending_bids(self, time_slot: TimeSlot) -> list[Bid]:
        return list(self._book.get(time_slot, {}).values())

    def clear(self, time_slot: TimeSlot, available_supply_kw: float) -> list[Allocation]:
        supply = require_non_negative("available_supply_kw", available_supply_kw)
        bids = sorted(self._book.pop(time_slot, {}).values(), key=lambda b: b.building_id)
        granted = {b.bid_id: 0.0 for b in bids}

        for need_of in (lambda b: b.critical_power_kw, lambda b: b.minimum_power_kw - b.critical_power_kw):
            needs = {b.bid_id: need_of(b) for b in bids}
            total = sum(needs.values())
            share = 1.0 if total <= supply else (supply / total if total > 0 else 0.0)
            for bid_id, need in needs.items():
                granted[bid_id] += need * share
            supply = max(0.0, supply - total * share)

        for b in sorted(bids, key=lambda b: (-b.priority_score, -b.willingness_to_pay, b.building_id)):
            give = min(b.requested_power_kw - b.minimum_power_kw, supply)
            granted[b.bid_id] += give
            supply -= give

        allocations = [
            Allocation(b.bid_id, b.building_id, time_slot, granted[b.bid_id], clearing_price=b.willingness_to_pay,
                       metadata={"mechanism": "mock_tiered_pay_as_bid"})
            for b in bids
        ]
        self.history.append((time_slot, bids, allocations))
        return allocations
