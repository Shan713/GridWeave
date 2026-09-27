"""MockGrid: a fixed-capacity supply with optional time-of-day shortages.

Stand-in for P3's aggregated supply (grid + solar + battery)."""
from __future__ import annotations

from typing import Sequence

from gridweave.models.common import TimeSlot
from gridweave.utils.validation import ValidationError, require_non_negative


class MockGrid:
    def __init__(self, capacity_kw: float, shortage_windows: Sequence[Sequence[float]] = ()) -> None:
        """``shortage_windows``: ``(start_hour, end_hour, capacity_factor)`` tuples."""
        self.capacity_kw = require_non_negative("capacity_kw", capacity_kw)
        self.shortage_windows = []
        for w in shortage_windows:
            start, end, factor = (float(x) for x in w)
            if not (0 <= start < end <= 24 and factor >= 0):
                raise ValidationError(f"invalid shortage window {w!r}")
            self.shortage_windows.append((start, end, factor))

    def available_power_kw(self, time_slot: TimeSlot) -> float:
        hour = time_slot.start.hour + time_slot.start.minute / 60
        factor = 1.0
        for start, end, f in self.shortage_windows:
            if start <= hour < end:
                factor = min(factor, f)
        return self.capacity_kw * factor
