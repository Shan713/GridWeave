"""Seeded synthetic demand generation.

Each generator owns its own ``random.Random`` instance (no global random
state), so two generators with the same seed always produce identical series
and generating one building never perturbs another.
"""
from __future__ import annotations

import math
import random
import zlib
from datetime import datetime, timedelta
from typing import Iterable, Mapping

from gridweave.models.building import BuildingSpec
from gridweave.models.demand import DemandSample
from gridweave.simulation.profiles import DemandProfile
from gridweave.utils.validation import ValidationError, require_positive


def derive_seed(master_seed: int, key: str) -> int:
    """Stable per-entity seed. Uses CRC32, not ``hash()``, which is salted per
    process and would break reproducibility."""
    return (int(master_seed) * 1_000_003 + zlib.crc32(key.encode("utf-8"))) % (2**32)


class DemandGenerator:
    """Generates a plausible *synthetic* demand series for one building.

    The shapes are hand-designed campus timetables, not fitted to meter data.

    Model per slot ``t``::

        expected(t) = capacity * (base + (peak - base) * activity(t))
        noise(t)    = phi * noise(t-1) + sqrt(1 - phi^2) * sigma * N(0, 1)   # AR(1)
        demand(t)   = clip(expected(t) * (1 + noise(t)) + spike(t), 0, capacity)

    AR(1) noise gives temporally correlated deviations (a warm afternoon stays
    warm), which is more plausible than independent jitter and is what makes
    short-term forecasting non-trivial. Note that the underlying daily
    template repeats exactly every weekday, which favours seasonal forecasters.
    """

    def __init__(
        self,
        profile: DemandProfile,
        capacity_kw: float,
        seed: int = 0,
        resolution_minutes: int = 15,
    ) -> None:
        self.profile = profile
        self.capacity_kw = require_positive("capacity_kw", capacity_kw)
        if resolution_minutes <= 0 or 1440 % resolution_minutes:
            raise ValidationError("resolution_minutes must be a positive divisor of 1440")
        self.resolution_minutes = resolution_minutes
        self.seed = seed
        self._curves = {
            False: profile.activity_curve(weekend=False, resolution_minutes=resolution_minutes),
            True: profile.activity_curve(weekend=True, resolution_minutes=resolution_minutes),
        }

    # ----------------------------------------------------------- noiseless
    def expected_demand(self, ts: datetime) -> float:
        """Deterministic (noise-free) demand at ``ts`` in kW."""
        weekend = ts.weekday() >= 5
        idx = (ts.hour * 60 + ts.minute) // self.resolution_minutes
        activity = self._curves[weekend][idx]
        p = self.profile
        return self.capacity_kw * (p.base_fraction + (p.peak_fraction - p.base_fraction) * activity)

    # ------------------------------------------------------------ stochastic
    def generate(self, start: datetime, periods: int) -> list[DemandSample]:
        """Generate ``periods`` consecutive samples starting at ``start``."""
        if periods < 0:
            raise ValidationError("periods must be >= 0")
        rng = random.Random(self.seed)
        p = self.profile
        step = timedelta(minutes=self.resolution_minutes)
        innovation_scale = math.sqrt(1.0 - p.noise_autocorrelation**2) * p.noise_std
        noise = rng.gauss(0.0, p.noise_std) if p.noise_std > 0 else 0.0
        spike_left, spike_kw = 0, 0.0
        samples: list[DemandSample] = []
        for i in range(periods):
            ts = start + i * step
            if i > 0 and p.noise_std > 0:
                noise = p.noise_autocorrelation * noise + innovation_scale * rng.gauss(0.0, 1.0)
            if spike_left == 0 and p.spike_probability > 0 and rng.random() < p.spike_probability:
                spike_left = p.spike_duration_slots
                spike_kw = p.spike_magnitude * self.capacity_kw * rng.uniform(0.5, 1.0)
            value = self.expected_demand(ts) * (1.0 + noise)
            if spike_left > 0:
                value += spike_kw
                spike_left -= 1
            samples.append(DemandSample(ts, round(min(max(value, 0.0), self.capacity_kw), 4)))
        return samples


def generate_campus_demand(
    buildings: Iterable[tuple[BuildingSpec, DemandProfile]],
    start: datetime,
    periods: int,
    seed: int = 42,
    resolution_minutes: int = 15,
) -> dict[str, list[DemandSample]]:
    """Generate one independent, reproducible series per building."""
    out: dict[str, list[DemandSample]] = {}
    for spec, profile in buildings:
        gen = DemandGenerator(profile, spec.capacity_kw, derive_seed(seed, spec.building_id), resolution_minutes)
        out[spec.building_id] = gen.generate(start, periods)
    return out


def total_demand(series: Mapping[str, list[DemandSample]]) -> list[DemandSample]:
    """Campus-level aggregate series (all series must share timestamps)."""
    columns = list(series.values())
    if not columns:
        return []
    return [DemandSample(col0.timestamp, sum(col[i].demand_kw for col in columns)) for i, col0 in enumerate(columns[0])]
