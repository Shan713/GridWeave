"""Typed, self-validating domain models shared by every GridWeave workstream."""
from gridweave.models.allocation import Allocation, AllocationOutcome, AllocationStatus
from gridweave.models.bid import BID_SCHEMA_VERSION, Bid
from gridweave.models.building import BuildingSpec, BuildingType
from gridweave.models.common import (
    DEFAULT_RESOLUTION_MINUTES,
    MisalignedTimestampError,
    MissingSlotError,
    TimeSlot,
    is_aligned,
    require_aligned,
)
from gridweave.models.context import BidContext
from gridweave.models.demand import DemandSample, DemandState, LoadClassification, Observation
from gridweave.models.supply import ClearingResult, DispatchRequest, DispatchResult, SourceType, SupplyOffer

__all__ = [
    "Allocation",
    "AllocationOutcome",
    "AllocationStatus",
    "BID_SCHEMA_VERSION",
    "Bid",
    "BidContext",
    "BuildingSpec",
    "BuildingType",
    "ClearingResult",
    "DispatchRequest",
    "DispatchResult",
    "DEFAULT_RESOLUTION_MINUTES",
    "DemandSample",
    "DemandState",
    "LoadClassification",
    "MisalignedTimestampError",
    "MissingSlotError",
    "Observation",
    "SourceType",
    "SupplyOffer",
    "TimeSlot",
    "is_aligned",
    "require_aligned",
]
