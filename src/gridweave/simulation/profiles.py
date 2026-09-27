"""Building-type demand profiles.

A profile describes the *shape* of a building's demand, relative to its
capacity, so the same profile serves a 60 kW hostel and a 300 kW hostel.
Shapes are declared as human-readable activity periods, e.g. "19:00-23:00 at
100 % activity", and then smoothed into a 15-minute curve so transitions look
like real load ramps instead of steps.

Adding a new building category never requires code changes: define a
``DemandProfile`` (in Python or in the campus JSON config) and reference it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from gridweave.models.building import BuildingType
from gridweave.utils.validation import (
    ValidationError,
    require_fraction,
    require_le,
    require_non_negative,
)

#: (start_hour, end_hour, activity_level) — hours in [0, 24], level in [0, 1].
Period = tuple[float, float, float]


@dataclass(frozen=True)
class DemandProfile:
    """Parametric daily demand profile.

    ``demand(t) = capacity * (base + (peak - base) * activity(t) * day_factor)``
    followed by autocorrelated multiplicative noise and optional spikes (see
    :class:`gridweave.simulation.demand_generator.DemandGenerator`).

    * ``weekday_periods`` — activity periods for Monday-Friday. Hours not
      covered by any period get ``idle_level``.
    * ``weekend_periods`` — optional separate weekend shape; if omitted, the
      weekday shape is reused and scaled by ``weekend_factor``.
    * ``smoothing_slots`` — width of the circular moving-average applied to
      the 15-minute activity curve (1 = no smoothing).
    """

    name: str
    base_fraction: float
    peak_fraction: float
    weekday_periods: tuple[Period, ...]
    weekend_periods: tuple[Period, ...] | None = None
    weekend_factor: float = 1.0
    idle_level: float = 0.0
    smoothing_slots: int = 5
    noise_std: float = 0.05
    noise_autocorrelation: float = 0.7
    spike_probability: float = 0.0
    spike_magnitude: float = 0.15
    spike_duration_slots: int = 2
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_fraction("base_fraction", self.base_fraction)
        require_fraction("peak_fraction", self.peak_fraction)
        require_le("base_fraction", self.base_fraction, "peak_fraction", self.peak_fraction)
        require_non_negative("weekend_factor", self.weekend_factor)
        require_fraction("idle_level", self.idle_level)
        require_non_negative("noise_std", self.noise_std)
        require_fraction("spike_probability", self.spike_probability)
        require_non_negative("spike_magnitude", self.spike_magnitude)
        if not 0.0 <= self.noise_autocorrelation < 1.0:
            raise ValidationError("noise_autocorrelation must be in [0, 1)")
        if self.smoothing_slots < 1 or self.spike_duration_slots < 1:
            raise ValidationError("smoothing_slots and spike_duration_slots must be >= 1")
        object.__setattr__(self, "weekday_periods", _validate_periods(self.weekday_periods))
        if self.weekend_periods is not None:
            object.__setattr__(self, "weekend_periods", _validate_periods(self.weekend_periods))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def activity_curve(self, weekend: bool, resolution_minutes: int = 15) -> list[float]:
        """Smoothed activity level in [0, 1] for each slot of one day."""
        if 1440 % resolution_minutes:
            raise ValidationError("resolution_minutes must divide 1440")
        periods = self.weekend_periods if (weekend and self.weekend_periods is not None) else self.weekday_periods
        slots = 1440 // resolution_minutes
        raw = []
        for i in range(slots):
            hour = (i + 0.5) * resolution_minutes / 60.0
            level = self.idle_level
            for start, end, lvl in periods:
                if start <= hour < end:
                    level = lvl  # later periods override earlier ones
            raw.append(level)
        curve = _circular_smooth(raw, self.smoothing_slots)
        if weekend and self.weekend_periods is None:
            curve = [min(1.0, v * self.weekend_factor) for v in curve]
        return curve

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DemandProfile":
        data = dict(data)
        data["weekday_periods"] = tuple(tuple(p) for p in data["weekday_periods"])
        if data.get("weekend_periods") is not None:
            data["weekend_periods"] = tuple(tuple(p) for p in data["weekend_periods"])
        return cls(**data)

    def with_overrides(self, **changes: Any) -> "DemandProfile":
        from dataclasses import replace

        return replace(self, **changes)


def _validate_periods(periods: Sequence[Sequence[float]]) -> tuple[Period, ...]:
    if not periods:
        raise ValidationError("a profile needs at least one activity period")
    out = []
    for p in periods:
        if len(p) != 3:
            raise ValidationError(f"period must be (start_hour, end_hour, level), got {p!r}")
        start, end, level = (float(x) for x in p)
        if not 0 <= start < end <= 24:
            raise ValidationError(f"invalid period hours {p!r}")
        require_fraction("period level", level)
        out.append((start, end, level))
    return tuple(out)


def _circular_smooth(values: list[float], window: int) -> list[float]:
    if window <= 1:
        return list(values)
    n, half = len(values), window // 2
    return [sum(values[(i + k) % n] for k in range(-half, half + 1)) / (2 * half + 1) for i in range(n)]


# --------------------------------------------------------------------------
# Built-in profiles. Fractions are of building capacity. Shapes reflect a
# typical Indian residential campus timetable (classes 09:00-17:00, hostel
# evening peak after dinner, labs with always-on equipment).
# --------------------------------------------------------------------------
HOSTEL = DemandProfile(
    name="hostel",
    base_fraction=0.12,
    peak_fraction=0.85,
    idle_level=0.25,
    weekday_periods=(
        (0.0, 1.5, 0.55),    # late-night study, declining
        (1.5, 6.0, 0.08),    # night: low
        (6.0, 9.0, 0.55),    # morning: water heaters, lights
        (9.0, 16.5, 0.25),   # students in class
        (16.5, 19.0, 0.55),  # return from class
        (19.0, 23.0, 1.0),   # evening peak: AC, laptops, laundry
        (23.0, 24.0, 0.75),
    ),
    weekend_periods=(
        (0.0, 2.0, 0.65),
        (2.0, 7.5, 0.08),
        (7.5, 11.0, 0.5),
        (11.0, 18.0, 0.55),  # students at hostel all day
        (18.0, 23.5, 0.95),
        (23.5, 24.0, 0.7),
    ),
    noise_std=0.06,
    spike_probability=0.01,
)

LAB = DemandProfile(
    name="lab",
    base_fraction=0.30,  # servers, fume hoods, refrigeration never switch off
    peak_fraction=0.90,
    idle_level=0.05,
    weekday_periods=(
        (8.5, 9.5, 0.5),
        (9.5, 13.0, 1.0),    # experiments
        (13.0, 14.0, 0.7),   # lunch
        (14.0, 17.5, 0.95),
        (17.5, 21.0, 0.35),  # research students
    ),
    weekend_factor=0.3,
    noise_std=0.04,
    spike_probability=0.02,  # equipment start-up surges
    spike_magnitude=0.2,
)

ACADEMIC = DemandProfile(
    name="academic",
    base_fraction=0.06,
    peak_fraction=0.80,
    weekday_periods=(
        (7.5, 8.5, 0.35),
        (8.5, 12.5, 1.0),    # lectures, projectors, AC
        (12.5, 13.5, 0.6),
        (13.5, 17.0, 0.9),
        (17.0, 19.0, 0.25),
    ),
    weekend_factor=0.1,
    noise_std=0.05,
)

LIBRARY = DemandProfile(
    name="library",
    base_fraction=0.10,
    peak_fraction=0.70,
    weekday_periods=(
        (8.0, 10.0, 0.5),
        (10.0, 17.0, 0.8),
        (17.0, 22.0, 1.0),   # evening study rush
        (22.0, 23.0, 0.4),
    ),
    weekend_periods=((9.0, 18.0, 0.7), (18.0, 21.0, 0.5)),
    noise_std=0.04,
)

ADMIN = DemandProfile(
    name="admin",
    base_fraction=0.08,
    peak_fraction=0.75,
    weekday_periods=((9.0, 13.0, 1.0), (13.0, 14.0, 0.6), (14.0, 17.5, 0.95), (17.5, 18.5, 0.3)),
    weekend_factor=0.05,
    noise_std=0.03,
)

OTHER = DemandProfile(
    name="other",
    base_fraction=0.15,
    peak_fraction=0.70,
    weekday_periods=((8.0, 20.0, 0.8),),
    weekend_factor=0.6,
)

DEFAULT_PROFILES: dict[str, DemandProfile] = {
    BuildingType.HOSTEL.value: HOSTEL,
    BuildingType.LAB.value: LAB,
    BuildingType.ACADEMIC.value: ACADEMIC,
    BuildingType.LIBRARY.value: LIBRARY,
    BuildingType.ADMIN.value: ADMIN,
    BuildingType.OTHER.value: OTHER,
}


def get_profile(name: str | BuildingType) -> DemandProfile:
    """Look up a built-in profile by name or :class:`BuildingType`."""
    key = name.value if isinstance(name, BuildingType) else str(name)
    try:
        return DEFAULT_PROFILES[key]
    except KeyError as exc:
        raise ValidationError(f"unknown demand profile {key!r}; known: {sorted(DEFAULT_PROFILES)}") from exc
