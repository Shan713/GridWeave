"""Grid capacity profiles, scheduled maintenance, and outage modeling."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gridweave.models.common import TimeSlot
from gridweave.utils.validation import ValidationError, require_fraction, require_non_negative


@dataclass(frozen=True)
class OutageWindow:
    """A planned or scheduled reduction in grid capacity over a time range.

    Can be specified either as:
      - Absolute datetimes (start_dt, end_dt)
      - Daily recurring hours [start_hour, end_hour) with start_hour, end_hour in [0, 24]
    capacity_factor is in [0.0, 1.0], where 0.0 is total blackout and 0.5 is 50% restriction.
    """

    name: str
    capacity_factor: float
    start_hour: float | None = None
    end_hour: float | None = None
    start_dt: datetime | None = None
    end_dt: datetime | None = None

    def __post_init__(self) -> None:
        require_fraction("capacity_factor", self.capacity_factor)
        if self.start_hour is not None or self.end_hour is not None:
            if self.start_hour is None or self.end_hour is None:
                raise ValidationError("Both start_hour and end_hour must be specified for recurring window")
            if not (0.0 <= self.start_hour < self.end_hour <= 24.0):
                raise ValidationError(
                    f"OutageWindow hours must satisfy 0 <= start < end <= 24, got [{self.start_hour}, {self.end_hour}]"
                )
        if self.start_dt is not None or self.end_dt is not None:
            if self.start_dt is None or self.end_dt is None:
                raise ValidationError("Both start_dt and end_dt must be specified for datetime window")
            if self.start_dt >= self.end_dt:
                raise ValidationError("OutageWindow start_dt must be strictly before end_dt")

    def affects_slot(self, slot: TimeSlot) -> bool:
        """Check if this outage window overlaps with the given TimeSlot."""
        # 1. Check absolute datetime range
        if self.start_dt is not None and self.end_dt is not None:
            if not (slot.end <= self.start_dt or slot.start >= self.end_dt):
                return True

        # 2. Check daily recurring hours
        if self.start_hour is not None and self.end_hour is not None:
            dt = slot.start
            slot_mid_hour = dt.hour + (dt.minute + slot.duration_minutes / 2.0) / 60.0
            if self.start_hour <= slot_mid_hour < self.end_hour:
                return True

        return False


@dataclass
class GridProfile:
    """Configurable grid capacity profile."""

    nominal_capacity_kw: float
    outage_windows: list[OutageWindow] = field(default_factory=list)
    ramp_limit_kw_per_slot: float | None = None  # Optional physical ramp-rate constraint
    emergency_outage: bool = False  # Dynamic sudden grid outage flag

    def __post_init__(self) -> None:
        require_non_negative("nominal_capacity_kw", self.nominal_capacity_kw)
        if self.ramp_limit_kw_per_slot is not None:
            require_non_negative("ramp_limit_kw_per_slot", self.ramp_limit_kw_per_slot)

    def get_available_capacity(self, slot: TimeSlot) -> float:
        """Calculate available import capacity (kW) for the given TimeSlot."""
        if self.emergency_outage:
            return 0.0

        min_factor = 1.0
        for window in self.outage_windows:
            if window.affects_slot(slot):
                min_factor = min(min_factor, window.capacity_factor)

        return round(self.nominal_capacity_kw * min_factor, 4)

    def set_emergency_outage(self, outage: bool) -> None:
        """Trigger or clear an unplanned emergency grid outage."""
        self.emergency_outage = bool(outage)

    def add_outage_window(self, window: OutageWindow) -> None:
        self.outage_windows.append(window)

    def clear_outages(self) -> None:
        self.outage_windows.clear()
        self.emergency_outage = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "nominal_capacity_kw": self.nominal_capacity_kw,
            "ramp_limit_kw_per_slot": self.ramp_limit_kw_per_slot,
            "emergency_outage": self.emergency_outage,
            "outage_windows": [
                {
                    "name": w.name,
                    "capacity_factor": w.capacity_factor,
                    "start_hour": w.start_hour,
                    "end_hour": w.end_hour,
                    "start_dt": w.start_dt.isoformat() if w.start_dt else None,
                    "end_dt": w.end_dt.isoformat() if w.end_dt else None,
                }
                for w in self.outage_windows
            ],
        }
