"""Typed adapter for consuming P1 demand forecasts without coupling P3 to P1 internals."""
from __future__ import annotations

from datetime import timedelta
from typing import Protocol, Sequence

from gridweave.models.common import TimeSlot


class DemandOutlookProvider(Protocol):
    """Public subset of the P1 BuildingAgent used by P3 scheduling."""

    def demand_outlook(self, horizon: int) -> Sequence[object]: ...


class BuildingDemandOutlookAdapter:
    """Convert P1's public ``demand_outlook`` into forecast power values."""

    def __init__(self, provider: DemandOutlookProvider) -> None:
        self.provider = provider

    def forecast_demand_kw(self, start_slot: TimeSlot, horizon: int) -> list[float]:
        points = list(self.provider.demand_outlook(horizon))
        values: list[float] = []
        for index, point in enumerate(points[:horizon]):
            timestamp = getattr(point, "timestamp", None)
            expected = start_slot.start + timedelta(minutes=index * start_slot.duration_minutes)
            if timestamp is not None and timestamp != expected:
                raise ValueError(f"P1 demand outlook point {index} is for {timestamp}, expected {expected}")
            values.append(float(getattr(point, "predicted_demand_kw")))
        return values
