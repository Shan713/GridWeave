"""Time-varying electricity tariff schedules and pricing models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gridweave.models.common import TimeSlot
from gridweave.utils.validation import ValidationError, require_non_negative


@dataclass(frozen=True)
class TariffPeriod:
    """A time interval [start_hour, end_hour) with a designated rate.

    Hours are represented as floats in [0.0, 24.0] (e.g. 6.5 for 06:30).
    """

    name: str
    start_hour: float
    end_hour: float
    rate_per_kwh: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.start_hour < self.end_hour <= 24.0):
            raise ValidationError(
                f"TariffPeriod hours must satisfy 0 <= start < end <= 24, got [{self.start_hour}, {self.end_hour}]"
            )
        require_non_negative("rate_per_kwh", self.rate_per_kwh)

    def contains(self, hour: float) -> bool:
        """Check if a fractional hour falls within [start_hour, end_hour)."""
        return self.start_hour <= hour < self.end_hour


@dataclass
class TariffSchedule:
    """Configurable time-of-use (TOU) tariff schedule.

    Default configuration provides typical commercial campus tiers:
      - 00:00 - 06:00: Off-Peak (5.0 / kWh)
      - 06:00 - 17:00: Standard (10.0 / kWh)
      - 17:00 - 22:00: Peak (18.0 / kWh)
      - 22:00 - 24:00: Standard (10.0 / kWh)
    """

    periods: tuple[TariffPeriod, ...] = field(default_factory=tuple)
    default_rate: float = 10.0
    multiplier: float = 1.0  # Dynamic tariff multiplier (for price spikes)

    def __post_init__(self) -> None:
        require_non_negative("default_rate", self.default_rate)
        require_non_negative("multiplier", self.multiplier)
        if not self.periods:
            # Setup default campus TOU profile
            self.periods = (
                TariffPeriod("off_peak", 0.0, 6.0, 5.0),
                TariffPeriod("standard", 6.0, 17.0, 10.0),
                TariffPeriod("peak", 17.0, 22.0, 18.0),
                TariffPeriod("standard_late", 22.0, 24.0, 10.0),
            )

    def get_rate(self, dt_or_slot: datetime | TimeSlot) -> float:
        """Get effective tariff rate ($/kWh) for the given datetime or TimeSlot."""
        if isinstance(dt_or_slot, TimeSlot):
            # Evaluate at the midpoint of the slot for temporal fairness
            dt = dt_or_slot.start
            hour = dt.hour + (dt.minute + dt_or_slot.duration_minutes / 2.0) / 60.0
        else:
            hour = dt_or_slot.hour + dt_or_slot.minute / 60.0 + dt_or_slot.second / 3600.0

        for period in self.periods:
            if period.contains(hour):
                return round(period.rate_per_kwh * self.multiplier, 4)

        return round(self.default_rate * self.multiplier, 4)

    def is_peak(self, dt_or_slot: datetime | TimeSlot) -> bool:
        """Determine if a slot corresponds to a peak tariff period."""
        if isinstance(dt_or_slot, TimeSlot):
            dt = dt_or_slot.start
            hour = dt.hour + (dt.minute + dt_or_slot.duration_minutes / 2.0) / 60.0
        else:
            hour = dt_or_slot.hour + dt_or_slot.minute / 60.0

        for period in self.periods:
            if period.contains(hour) and "peak" in period.name.lower() and "off" not in period.name.lower():
                return True
        return False

    def set_multiplier(self, multiplier: float) -> None:
        """Apply a dynamic multiplier (e.g. 2.0 for emergency price spike event)."""
        require_non_negative("multiplier", multiplier)
        self.multiplier = float(multiplier)

    def reset_multiplier(self) -> None:
        """Reset multiplier to nominal 1.0."""
        self.multiplier = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_rate": self.default_rate,
            "multiplier": self.multiplier,
            "periods": [
                {
                    "name": p.name,
                    "start_hour": p.start_hour,
                    "end_hour": p.end_hour,
                    "rate_per_kwh": p.rate_per_kwh,
                }
                for p in self.periods
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TariffSchedule:
        periods = tuple(
            TariffPeriod(
                name=p["name"],
                start_hour=float(p["start_hour"]),
                end_hour=float(p["end_hour"]),
                rate_per_kwh=float(p["rate_per_kwh"]),
            )
            for p in data.get("periods", [])
        )
        return cls(
            periods=periods,
            default_rate=float(data.get("default_rate", 10.0)),
            multiplier=float(data.get("multiplier", 1.0)),
        )
