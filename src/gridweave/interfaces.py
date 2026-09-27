"""Integration contracts between the four GridWeave workstreams (contract version 2).

These are :class:`typing.Protocol` definitions (structural interfaces): a
teammate's class satisfies one simply by having the right methods; no
inheritance from GridWeave classes is required. The mocks in
:mod:`gridweave.mocks` implement every protocol so each workstream can be
developed and tested in isolation. :mod:`gridweave.contracts` has the
cross-party consistency checks P4 should run every slot.

One slot, end to end::

    P4  env.step() / agent.observe()          history up to slot t-1
    P1  agent.generate_bid(BidContext(t, s))   forecast-based Bid (revisable)
    P3  supply.offers(t)                       [SupplyOffer] per source
    P2  auction.clear(t, bids, offers)         ClearingResult (allocations + dispatch)
    P3  supply.dispatch(requests)              [DispatchResult] (delivered kW, new SOC)
    P4  env.step()                             realised Observation for slot t
    P1  agent.settle(allocation, realised)     Settlement (served / deferred / shortfall)
    P4  env.apply_settlement(settlement)       environment updates its state for t+1
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from gridweave.models.allocation import Allocation
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.context import BidContext
from gridweave.models.demand import Observation
from gridweave.models.settlement import Settlement
from gridweave.models.supply import ClearingResult, DispatchRequest, DispatchResult, SupplyOffer

CONTRACT_VERSION = "2.0"


@runtime_checkable
class DemandAgent(Protocol):
    """Implemented by P1 (:class:`gridweave.agents.BuildingAgent`). Driven by P4."""

    @property
    def building_id(self) -> str: ...

    def observe(self, observation: Observation) -> bool: ...

    def generate_bid(self, context: BidContext | None = None) -> Bid: ...

    def abort_bid(self, reason: str = "") -> Bid: ...

    def settle(self, allocation: Allocation, realised: Observation) -> Settlement: ...

    def snapshot(self) -> Mapping[str, Any]: ...


@runtime_checkable
class Auctioneer(Protocol):
    """Implemented by P2 (market mechanism). Called by P4.

    ``clear`` must return exactly one allocation per bid (zero is allowed),
    dispatch only offered sources within their offers, keep total allocation
    within total offered supply, and balance energy (total dispatched ==
    total allocated). :func:`gridweave.contracts.validate_clearing` checks all of this.
    """

    def clear(self, time_slot: TimeSlot, bids: Sequence[Bid], offers: Sequence[SupplyOffer]) -> ClearingResult: ...


@runtime_checkable
class SupplyProvider(Protocol):
    """Implemented by P3 (grid, solar, battery, ... aggregated). Called by P4.

    ``offers`` states what each source can deliver in a slot and at what
    marginal price; ``dispatch`` executes P2's dispatch requests, updates
    source state (e.g. battery SOC) and reports what was actually delivered.
    """

    def offers(self, time_slot: TimeSlot) -> Sequence[SupplyOffer]: ...

    def dispatch(self, requests: Sequence[DispatchRequest]) -> Sequence[DispatchResult]: ...


@runtime_checkable
class EnvironmentStream(Protocol):
    """One building's environment (simulator or meter feed). Owned by P4.

    ``step`` reveals the realised demand of the next slot; ``apply_settlement``
    feeds the outcome back so the environment's future can depend on what
    was served (closed loop).
    """

    @property
    def has_next(self) -> bool: ...

    def step(self) -> Observation: ...

    def apply_settlement(self, settlement: Settlement) -> None: ...
