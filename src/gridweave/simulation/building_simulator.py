"""Per-building environment: turns a demand series into agent observations.

The simulator is the *environment* half of the agent/environment split. It
knows the true demand; the agent only sees what ``step()`` returns. This is
also where later workstreams can inject events (P4) — e.g. a heat-wave that
scales demand — without touching agent code, via ``demand_modifier``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Sequence

from gridweave.models.building import BuildingSpec
from gridweave.models.demand import DemandSample, Observation
from gridweave.simulation.demand_generator import DemandGenerator, derive_seed
from gridweave.simulation.profiles import DemandProfile, get_profile

#: ``(timestamp, true_demand_kw) -> modified_demand_kw``
DemandModifier = Callable[[datetime, float], float]


class BuildingSimulator:
    """Replays a demand series one slot at a time."""

    def __init__(
        self,
        spec: BuildingSpec,
        series: Sequence[DemandSample],
        demand_modifier: DemandModifier | None = None,
    ) -> None:
        self.spec = spec
        self._series = list(series)
        self._cursor = 0
        self.demand_modifier = demand_modifier

    @classmethod
    def from_profile(
        cls,
        spec: BuildingSpec,
        start: datetime,
        periods: int,
        profile: DemandProfile | None = None,
        seed: int = 42,
        resolution_minutes: int = 15,
    ) -> "BuildingSimulator":
        profile = profile or get_profile(spec.building_type)
        gen = DemandGenerator(profile, spec.capacity_kw, derive_seed(seed, spec.building_id), resolution_minutes)
        return cls(spec, gen.generate(start, periods))

    @property
    def series(self) -> list[DemandSample]:
        return list(self._series)

    @property
    def has_next(self) -> bool:
        return self._cursor < len(self._series)

    @property
    def position(self) -> int:
        return self._cursor

    def reset(self) -> None:
        self._cursor = 0

    def true_demand(self, index: int) -> float:
        sample = self._series[index]
        value = sample.demand_kw
        if self.demand_modifier is not None:
            value = max(0.0, min(self.spec.capacity_kw, self.demand_modifier(sample.timestamp, value)))
        return value

    def step(self) -> Observation:
        """Return the next observation and advance the clock."""
        if not self.has_next:
            raise StopIteration(f"simulator for {self.spec.building_id} exhausted")
        sample = self._series[self._cursor]
        obs = Observation(self.spec.building_id, sample.timestamp, self.true_demand(self._cursor))
        self._cursor += 1
        return obs
