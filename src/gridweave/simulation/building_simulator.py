"""Per-building environment: reveals realised demand one slot at a time.

The simulator is the *environment* half of the agent/environment split. It
knows the true demand; the agent only sees what ``step()`` returns, and only
after the slot has happened. It implements the
:class:`gridweave.interfaces.EnvironmentStream` protocol, including the
closed-loop hook ``apply_settlement``.

Closed loop in P1's simulator (P4 may replace or extend it):

* Deferred flexible energy is carried by the agent's backlog queue and is
  part of the building's realised need in later slots (see ``settle``).
* ``rebound_fraction`` (default 0): a share of *curtailed* load reappears as
  extra true demand in the next slot (e.g. HVAC working harder after a
  set-point cut). This makes the environment's future depend on the market
  outcome.
* ``demand_modifier`` lets P4 inject events (heat wave, exam week).
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Sequence

from gridweave.models.building import BuildingSpec
from gridweave.models.demand import DemandSample, Observation
from gridweave.models.settlement import Settlement
from gridweave.simulation.demand_generator import DemandGenerator, derive_seed
from gridweave.simulation.profiles import DemandProfile, get_profile
from gridweave.utils.validation import ValidationError, require_fraction

#: ``(timestamp, true_demand_kw) -> modified_demand_kw``
DemandModifier = Callable[[datetime, float], float]


class BuildingSimulator:
    """Replays a base demand series one slot at a time, with optional feedback."""

    def __init__(
        self,
        spec: BuildingSpec,
        series: Sequence[DemandSample],
        demand_modifier: DemandModifier | None = None,
        rebound_fraction: float = 0.0,
    ) -> None:
        self.spec = spec
        self._series = list(series)
        self._cursor = 0
        self.demand_modifier = demand_modifier
        self.rebound_fraction = require_fraction("rebound_fraction", rebound_fraction)
        self._carry_kw = 0.0
        self.settlements: list[Settlement] = []

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
        """Exogenous demand (base series + ``demand_modifier``), *excluding*
        state-dependent feedback such as rebound. This is ground truth for
        offline analysis; an online coordinator must only use ``step()``."""
        return [DemandSample(s.timestamp, self._exogenous(i)) for i, s in enumerate(self._series)]

    @property
    def has_next(self) -> bool:
        return self._cursor < len(self._series)

    def reset(self) -> None:
        self._cursor = 0
        self._carry_kw = 0.0
        self.settlements.clear()

    def _exogenous(self, index: int) -> float:
        sample = self._series[index]
        value = sample.demand_kw
        if self.demand_modifier is not None:
            value = self.demand_modifier(sample.timestamp, value)
        return max(0.0, min(self.spec.capacity_kw, value))

    def true_demand(self, index: int) -> float:
        """Exogenous demand at ``index`` (no feedback). Kept for offline analysis."""
        return self._exogenous(index)

    def step(self) -> Observation:
        """Reveal the realised demand of the next slot and advance the clock."""
        if not self.has_next:
            raise StopIteration(f"simulator for {self.spec.building_id} exhausted")
        sample = self._series[self._cursor]
        value = min(self.spec.capacity_kw, self._exogenous(self._cursor) + self._carry_kw)
        self._carry_kw = 0.0
        self._cursor += 1
        return Observation(self.spec.building_id, sample.timestamp, value)

    def apply_settlement(self, settlement: Settlement) -> None:
        """Feed a slot's outcome back into the environment (closed loop)."""
        if settlement.building_id != self.spec.building_id:
            raise ValidationError(f"settlement for {settlement.building_id} applied to {self.spec.building_id}")
        self.settlements.append(settlement)
        self._carry_kw = self.rebound_fraction * settlement.curtailed_kw
