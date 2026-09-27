"""Transparent, deterministic building priority score in [0, 1].

``priority = sum_i w_i * f_i`` with non-negative weights normalised to sum
to 1 and every factor ``f_i`` in [0, 1], so the score is always in [0, 1].

Semantics: **0 = this building can easily wait; 1 = most essential and
most urgent right now.** The auction (P2) decides how, and whether, to use
it; the score is advisory input, not an allocation.

Factors:

* ``criticality`` — share of the slot's own demand that is critical load
  (deferred backlog is excluded so that postponing load never makes a
  building look *less* critical).
* ``importance`` — static importance of the building (config; e.g. labs
  with running experiments rank above admin offices).
* ``urgency`` — deferred-demand pressure: ``backlog / backlog_limit``.
  Rises every time the building's flexible load is postponed.
* ``deprivation`` — exponentially smoothed share of recent requests that
  went unserved (historical learning from past allocations).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from gridweave.utils.validation import ValidationError, clamp, require_fraction, require_non_negative


@dataclass(frozen=True)
class PriorityWeights:
    criticality: float = 0.35
    importance: float = 0.25
    urgency: float = 0.20
    deprivation: float = 0.20

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            require_non_negative(f"weight {name}", value)
        if sum(asdict(self).values()) <= 0:
            raise ValidationError("at least one priority weight must be > 0")

    def normalised(self) -> dict[str, float]:
        raw = asdict(self)
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}


@dataclass(frozen=True)
class PriorityFactors:
    criticality: float
    importance: float
    urgency: float
    deprivation: float

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            require_fraction(name, value)


@dataclass(frozen=True)
class PriorityBreakdown:
    """The score plus everything needed to explain it in a demo or viva."""

    score: float
    factors: dict[str, float]
    weights: dict[str, float]
    contributions: dict[str, float]

    def to_dict(self) -> dict:
        return asdict(self)


class PriorityModel:
    def __init__(self, weights: PriorityWeights | None = None) -> None:
        self.weights = weights or PriorityWeights()

    @staticmethod
    def factors(
        critical_kw: float,
        base_demand_kw: float,
        importance: float,
        backlog_kw: float,
        backlog_limit_kw: float,
        deprivation: float,
    ) -> PriorityFactors:
        criticality = critical_kw / base_demand_kw if base_demand_kw > 0 else 0.0
        urgency = backlog_kw / backlog_limit_kw if backlog_limit_kw > 0 else 0.0
        return PriorityFactors(
            criticality=clamp(criticality),
            importance=clamp(importance),
            urgency=clamp(urgency),
            deprivation=clamp(deprivation),
        )

    def score(self, factors: PriorityFactors) -> PriorityBreakdown:
        w = self.weights.normalised()
        f = asdict(factors)
        contributions = {k: w[k] * f[k] for k in w}
        return PriorityBreakdown(
            score=round(clamp(sum(contributions.values())), 6),
            factors=f,
            weights=w,
            contributions=contributions,
        )
