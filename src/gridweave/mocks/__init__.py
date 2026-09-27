"""Lightweight test doubles for the other workstreams.

**These are NOT the project's auction, supply model or coordinator.** They
exist so the Building Intelligence subsystem can be demonstrated and tested
end-to-end today, and so P2/P3/P4 have a reference implementation of each
protocol in :mod:`gridweave.interfaces` to test against and replace.
"""
from gridweave.mocks.auctioneer import MockAuctioneer
from gridweave.mocks.coordinator import MockCoordinator, StepRecord, summarise
from gridweave.mocks.grid import MockGrid

__all__ = ["MockAuctioneer", "MockCoordinator", "MockGrid", "StepRecord", "summarise"]
