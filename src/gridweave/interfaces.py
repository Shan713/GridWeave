"""Integration contracts between the four GridWeave workstreams.

These are :class:`typing.Protocol` definitions: structural interfaces. A
teammate's class satisfies a protocol simply by having the right methods —
no inheritance from GridWeave classes is required. The mocks in
:mod:`gridweave.mocks` implement every protocol, so each workstream can be
developed and tested in isolation.

Data exchanged across these boundaries is always one of the immutable,
self-validating models in :mod:`gridweave.models` (``Bid``, ``Allocation``,
``Observation``, ``TimeSlot``...). See ``docs/integration_contract.md``.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from gridweave.bidding.bid_generator import BidContext
from gridweave.models.allocation import Allocation, AllocationOutcome
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.demand import Observation


@runtime_checkable
class DemandAgent(Protocol):
    """Implemented by P1 (:class:`gridweave.agents.BuildingAgent`). Used by P4."""

    @property
    def building_id(self) -> str: ...

    def observe(self, observation: Observation) -> bool: ...

    def generate_bid(self, context: BidContext | None = None) -> Bid: ...

    def apply_allocation(self, allocation: Allocation) -> AllocationOutcome: ...

    def snapshot(self) -> Mapping[str, Any]: ...


@runtime_checkable
class Auctioneer(Protocol):
    """Implemented by P2 (market mechanism). Used by P4.

    ``clear`` must return at most one allocation per submitted bid, each
    echoing the bid's ``bid_id``, ``building_id`` and ``time_slot``, with
    ``sum(allocated_power_kw) <= available_supply_kw``.
    """

    def submit_bid(self, bid: Bid) -> None: ...

    def clear(self, time_slot: TimeSlot, available_supply_kw: float) -> Sequence[Allocation]: ...


@runtime_checkable
class SupplyProvider(Protocol):
    """Implemented by P3 (grid / solar / battery agents, aggregated). Used by P4."""

    def available_power_kw(self, time_slot: TimeSlot) -> float: ...


@runtime_checkable
class EnvironmentStream(Protocol):
    """Anything that produces observations for one building (simulator, meter feed)."""

    @property
    def has_next(self) -> bool: ...

    def step(self) -> Observation: ...
