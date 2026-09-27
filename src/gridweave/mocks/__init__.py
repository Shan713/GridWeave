"""Lightweight test doubles for the other workstreams.

**These are NOT the project's auction, supply model or coordinator.** They
exist so the Building Intelligence subsystem can be demonstrated and tested
end-to-end, and so P2/P3/P4 have a reference implementation of each
protocol in :mod:`gridweave.interfaces` to test against and replace.
"""
from gridweave.mocks.auctioneer import MockAuctioneer
from gridweave.mocks.coordinator import MockCoordinator, SettlementError, StepRecord, summarise
from gridweave.mocks.supply import MockBattery, MockGrid, MockSolar, MockSupply

__all__ = ["MockAuctioneer", "MockBattery", "MockCoordinator", "MockGrid", "MockSolar", "MockSupply",
           "SettlementError", "StepRecord", "summarise"]
