"""MockAuctioneer: a transparent stand-in for P2's market. NOT the project's auction.

Clearing rule (deliberately naive):

1. Supply = sum of offered kW.
2. Allocation: critical tier pro rata, then the rest of each minimum pro
   rata, then flexible power greedily by priority, willingness to pay,
   building id.
3. Dispatch: merit order (cheapest offer first) until total allocation is
   covered.
4. Pay-as-bid price; each allocation's ``supply_mix`` is its pro-rata share
   of the dispatched sources.
"""
from __future__ import annotations

from typing import Sequence

from gridweave.models.allocation import Allocation
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import ClearingResult, DispatchRequest, SupplyOffer


class MockAuctioneer:
    def __init__(self) -> None:
        self.history: list[ClearingResult] = []

    def clear(self, time_slot: TimeSlot, bids: Sequence[Bid], offers: Sequence[SupplyOffer]) -> ClearingResult:
        bids = sorted(bids, key=lambda b: b.building_id)
        supply = sum(o.available_kw for o in offers)
        granted = {b.bid_id: 0.0 for b in bids}
        remaining = supply
        for need_of in (lambda b: b.critical_power_kw, lambda b: b.minimum_power_kw - b.critical_power_kw):
            needs = {b.bid_id: need_of(b) for b in bids}
            total = sum(needs.values())
            share = 1.0 if total <= remaining else (remaining / total if total > 0 else 0.0)
            for bid_id, need in needs.items():
                granted[bid_id] += need * share
            remaining = max(0.0, remaining - total * share)
        for b in sorted(bids, key=lambda b: (-b.priority_score, -b.willingness_to_pay, b.building_id)):
            give = min(b.requested_power_kw - b.minimum_power_kw, remaining)
            granted[b.bid_id] += give
            remaining -= give

        total_alloc = sum(granted.values())
        dispatch, left = [], total_alloc
        for o in sorted(offers, key=lambda o: (o.marginal_price, o.source_id)):
            take = min(o.available_kw, left)
            if take > 0:
                dispatch.append(DispatchRequest(o.source_id, time_slot, take))
                left -= take
        shares = {d.source_id: d.requested_kw / total_alloc for d in dispatch} if total_alloc > 0 else {}

        allocations = []
        for b in bids:
            kw = granted[b.bid_id]
            mix = {src: kw * sh for src, sh in shares.items()} if kw > 0 else {}
            if mix:  # absorb float rounding in the last source so the mix sums exactly
                last = next(reversed(mix))
                mix[last] = kw - sum(v for k, v in mix.items() if k != last)
            allocations.append(Allocation(b.bid_id, b.building_id, time_slot, kw,
                                          clearing_price=b.willingness_to_pay, supply_mix=mix,
                                          metadata={"mechanism": "mock_tiered_pay_as_bid"}))
        result = ClearingResult(time_slot, tuple(allocations), tuple(dispatch),
                                metadata={"mechanism": "mock_tiered_pay_as_bid", "supply_kw": supply})
        self.history.append(result)
        return result
