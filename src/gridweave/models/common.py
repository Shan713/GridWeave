"""Shared value types: time slots and timestamp helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from gridweave.utils.validation import ValidationError

#: Default market / simulation resolution: 15-minute slots (96 per day).
DEFAULT_RESOLUTION_MINUTES = 15


class MisalignedTimestampError(ValidationError):
    """A timestamp does not fall on the slot grid (e.g. 18:07 with 15-min slots)."""


class MissingSlotError(ValidationError):
    """A time series skips one or more slots, or is not strictly regular."""


def is_aligned(ts: datetime, resolution_minutes: int = DEFAULT_RESOLUTION_MINUTES) -> bool:
    """True if ``ts`` lies exactly on the ``resolution_minutes`` grid counted from midnight."""
    return ts.second == 0 and ts.microsecond == 0 and (ts.hour * 60 + ts.minute) % resolution_minutes == 0


def require_aligned(ts: datetime, resolution_minutes: int = DEFAULT_RESOLUTION_MINUTES) -> datetime:
    """Reject off-grid timestamps. Silent rounding would hide data errors, so the core never normalises."""
    if not is_aligned(ts, resolution_minutes):
        raise MisalignedTimestampError(f"{ts.isoformat()} is not aligned to the {resolution_minutes}-minute slot grid")
    return ts


@dataclass(frozen=True, order=True)
class TimeSlot:
    """A half-open market interval ``[start, start + duration)``.

    Every bid and allocation refers to exactly one ``TimeSlot``. Power values
    (kW) attached to a slot are *average* power over the slot, so the energy
    of a slot is ``power_kw * slot.hours``.
    """

    start: datetime
    duration_minutes: int = DEFAULT_RESOLUTION_MINUTES

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime):
            raise ValidationError("TimeSlot.start must be a datetime")
        if not isinstance(self.duration_minutes, int) or self.duration_minutes <= 0:
            raise ValidationError("TimeSlot.duration_minutes must be a positive int")
        if 1440 % self.duration_minutes == 0:
            require_aligned(self.start, self.duration_minutes)

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_minutes)

    @property
    def hours(self) -> float:
        return self.duration_minutes / 60.0

    def next(self) -> "TimeSlot":
        return TimeSlot(self.end, self.duration_minutes)

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "duration_minutes": self.duration_minutes}

    @classmethod
    def from_dict(cls, data: dict) -> "TimeSlot":
        return cls(datetime.fromisoformat(data["start"]), int(data["duration_minutes"]))

    def __str__(self) -> str:
        return f"{self.start:%Y-%m-%d %H:%M}+{self.duration_minutes}m"
