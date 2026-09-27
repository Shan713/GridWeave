"""Typed, self-validating domain models shared by every GridWeave workstream."""
from gridweave.models.allocation import Allocation, AllocationOutcome, AllocationStatus
from gridweave.models.bid import BID_SCHEMA_VERSION, Bid
from gridweave.models.building import BuildingSpec, BuildingType
from gridweave.models.common import DEFAULT_RESOLUTION_MINUTES, TimeSlot
from gridweave.models.demand import DemandSample, DemandState, LoadClassification, Observation

__all__ = [
    "Allocation",
    "AllocationOutcome",
    "AllocationStatus",
    "BID_SCHEMA_VERSION",
    "Bid",
    "BuildingSpec",
    "BuildingType",
    "DEFAULT_RESOLUTION_MINUTES",
    "DemandSample",
    "DemandState",
    "LoadClassification",
    "Observation",
    "TimeSlot",
]
