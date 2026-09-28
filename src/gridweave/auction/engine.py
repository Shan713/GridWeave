"""AuctionEngine: The main market clearing coordinator for GridWeave (Person 2).

Implements the :class:`gridweave.interfaces.Auctioneer` protocol, providing both
a stateless ``clear(...)`` method and a stateful market lifecycle for P4 orchestration.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Sequence

from gridweave.auction.constraints import ConstraintValidator
from gridweave.auction.fairness import FairnessTracker
from gridweave.auction.metrics import MarketMetricsCalculator
from gridweave.auction.result import MarketResult
from gridweave.auction.scoring import BidScorer
from gridweave.auction.strategies import (
    AllocationStrategy,
    GreedyAllocationStrategy,
    StrategyResult,
)
from gridweave.auction.validator import BidValidator
from gridweave.contracts import validate_clearing
from gridweave.models.allocation import Allocation
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import ClearingResult, SupplyOffer
from gridweave.utils.validation import ValidationError


class MarketPhase(str, Enum):
    """Explicit lifecycle stages of a market session."""

    IDLE = "idle"
    OPEN = "open"
    COLLECTING_BIDS = "collecting_bids"
    VALIDATING = "validating"
    CLEARING = "clearing"
    SETTLED = "settled"
    RE_AUCTION = "re_auction"


class AuctionEngine:
    """The central campus energy auction engine.

    Satisfies the :class:`gridweave.interfaces.Auctioneer` protocol via ``clear(...)``,
    and additionally provides:
    - Dedicated input validation layer (:class:`BidValidator`)
    - Pluggable allocation strategies (Greedy, Optimized, Baselines)
    - Full market lifecycle state machine
    - Dynamic re-auction and revision management
    - Explainable decision tracing per building
    - Historical multi-slot fairness and starvation prevention (:class:`FairnessTracker`)
    """

    def __init__(
        self,
        strategy: AllocationStrategy | None = None,
        scorer: BidScorer | None = None,
        validator: BidValidator | None = None,
        track_fairness: bool = True,
        strict_contract_validation: bool = True,
    ) -> None:
        self.strategy: AllocationStrategy = strategy or GreedyAllocationStrategy()
        self.scorer: BidScorer = scorer or BidScorer()
        self.validator: BidValidator = validator or BidValidator()
        self.constraint_validator: ConstraintValidator = ConstraintValidator()
        self.fairness_tracker: FairnessTracker | None = FairnessTracker() if track_fairness else None
        self.strict_contract_validation = strict_contract_validation

        # Lifecycle state
        self.phase: MarketPhase = MarketPhase.IDLE
        self.current_slot: TimeSlot | None = None
        self._active_bids: dict[str, Bid] = {}           # building_id -> Bid (latest revision)
        self._active_offers: dict[str, SupplyOffer] = {} # source_id -> SupplyOffer
        self.last_result: MarketResult | None = None
        self.history: list[MarketResult] = []

    # -------------------------------------------------------------------------
    # Auctioneer Protocol Implementation
    # -------------------------------------------------------------------------
    def clear(
        self,
        time_slot: TimeSlot,
        bids: Sequence[Bid],
        offers: Sequence[SupplyOffer],
    ) -> ClearingResult:
        """Stateless one-slot market clearing conforming to :class:`gridweave.interfaces.Auctioneer`."""
        t_start = time.perf_counter()

        # 1. Validation
        validation_rep = self.validator.validate_bids(bids, expected_slot=time_slot)
        if not validation_rep.is_valid:
            raise ValidationError(f"Bid validation failed in clearing: {validation_rep.errors}")

        offer_errors = self.validator.validate_offers(offers, expected_slot=time_slot)
        if offer_errors:
            raise ValidationError(f"Offer validation failed in clearing: {offer_errors}")

        valid_bids = validation_rep.accepted_bids
        if not valid_bids:
            # Zero bids: return empty clearing result with zero dispatch
            empty_res = ClearingResult(time_slot=time_slot, allocations=(), dispatch=(), clearing_price=None)
            if self.strict_contract_validation:
                validate_clearing(empty_res, valid_bids, offers)
            return empty_res

        # 2. Get fairness deprivation boosts if tracker active
        dep_boosts = self.fairness_tracker.get_all_deprivation_boosts() if self.fairness_tracker else {}

        # 3. Execute Allocation Strategy
        strat_res: StrategyResult = self.strategy.allocate(
            time_slot=time_slot,
            bids=valid_bids,
            offers=offers,
            scorer=self.scorer,
            deprivation_factors=dep_boosts,
        )

        # 4. Invariant checks
        alloc_map = {a.bid_id: a.allocated_power_kw for a in strat_res.allocations}
        disp_map = {d.source_id: d.requested_kw for d in strat_res.dispatch}
        violations = self.constraint_validator.check_invariants(
            bids=valid_bids,
            offers=offers,
            allocations=alloc_map,
            dispatches=disp_map,
        )
        if violations:
            raise ValidationError(f"Market allocation violated mathematical constraints: {violations}")

        # 5. Contract consistency check (P1 cross-party verification)
        clearing_res = ClearingResult(
            time_slot=time_slot,
            allocations=strat_res.allocations,
            dispatch=strat_res.dispatch,
            clearing_price=strat_res.clearing_price,
            metadata=dict(strat_res.metadata),
        )
        if self.strict_contract_validation:
            validate_clearing(clearing_res, valid_bids, offers)

        # 6. Metrics & Multi-Slot Fairness Update
        runtime_ms = (time.perf_counter() - t_start) * 1000.0
        metrics = MarketMetricsCalculator.calculate(
            time_slot=time_slot,
            bids=valid_bids,
            offers=offers,
            allocations=strat_res.allocations,
            runtime_ms=runtime_ms,
        )

        if self.fairness_tracker:
            for b in valid_bids:
                alloc_kw = alloc_map.get(b.bid_id, 0.0)
                self.fairness_tracker.record_slot(
                    building_id=b.building_id,
                    requested_kw=b.requested_power_kw,
                    allocated_kw=alloc_kw,
                    duration_hours=time_slot.hours,
                )

        market_res = MarketResult(
            time_slot=time_slot,
            allocations=strat_res.allocations,
            dispatch=strat_res.dispatch,
            strategy_name=strat_res.strategy_name,
            metrics=metrics,
            decision_traces=strat_res.decision_traces,
            clearing_price=strat_res.clearing_price,
            validation_report=validation_rep,
            metadata=dict(strat_res.metadata),
        )

        self.last_result = market_res
        self.history.append(market_res)
        return clearing_res

    # -------------------------------------------------------------------------
    # Stateful Lifecycle Methods (for P4 Coordinator & Re-Auction)
    # -------------------------------------------------------------------------
    def open_market(self, time_slot: TimeSlot) -> None:
        """Open the market for a new time slot."""
        self.current_slot = time_slot
        self.phase = MarketPhase.COLLECTING_BIDS
        self._active_bids.clear()
        self._active_offers.clear()

    def submit_bid(self, bid: Bid) -> None:
        """Submit or revise a bid during the COLLECTING_BIDS or RE_AUCTION phase."""
        if self.phase not in (MarketPhase.COLLECTING_BIDS, MarketPhase.RE_AUCTION):
            raise ValidationError(f"Cannot submit bid in phase {self.phase.value}")
        if self.current_slot is not None and bid.time_slot != self.current_slot:
            raise ValidationError(f"Bid slot {bid.time_slot} does not match open market slot {self.current_slot}")

        errs = self.validator.validate_bid(bid, expected_slot=self.current_slot)
        if errs:
            raise ValidationError(f"Invalid bid {bid.bid_id}: {errs}")

        prev = self._active_bids.get(bid.building_id)
        if prev is not None:
            if bid.revision <= prev.revision:
                raise ValidationError(
                    f"Revision {bid.revision} for {bid.building_id} is not newer than active revision {prev.revision}"
                )
        self._active_bids[bid.building_id] = bid

    def submit_offer(self, offer: SupplyOffer) -> None:
        """Submit a supply offer during the open market session."""
        if self.phase not in (MarketPhase.COLLECTING_BIDS, MarketPhase.RE_AUCTION):
            raise ValidationError(f"Cannot submit offer in phase {self.phase.value}")
        if self.current_slot is not None and offer.time_slot != self.current_slot:
            raise ValidationError(f"Offer slot {offer.time_slot} does not match open market slot {self.current_slot}")

        errs = self.validator.validate_offers([offer], expected_slot=self.current_slot)
        if errs:
            raise ValidationError(f"Invalid offer from {offer.source_id}: {errs}")

        self._active_offers[offer.source_id] = offer

    def clear_market(self) -> MarketResult:
        """Transition through VALIDATING and CLEARING, returning the rich MarketResult."""
        if self.current_slot is None:
            raise ValidationError("Market has not been opened with a TimeSlot.")
        self.phase = MarketPhase.VALIDATING

        bids = list(self._active_bids.values())
        offers = list(self._active_offers.values())

        self.phase = MarketPhase.CLEARING
        self.clear(self.current_slot, bids, offers)
        self.phase = MarketPhase.SETTLED

        assert self.last_result is not None
        return self.last_result

    def re_auction(
        self,
        new_offers: Sequence[SupplyOffer] | None = None,
        revised_bids: Sequence[Bid] | None = None,
    ) -> MarketResult:
        """Trigger dynamic re-auction under changed supply or revised bids."""
        self.phase = MarketPhase.RE_AUCTION

        if new_offers is not None:
            self._active_offers.clear()
            for o in new_offers:
                self.submit_offer(o)

        if revised_bids is not None:
            for b in revised_bids:
                self.submit_bid(b)

        return self.clear_market()

    def get_allocations(self) -> Sequence[Allocation]:
        """Return allocations from the most recent clearing."""
        if self.last_result is None:
            return ()
        return self.last_result.allocations

    def get_last_result(self) -> MarketResult | None:
        return self.last_result
