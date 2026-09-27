"""Critical / flexible load classification.

The rule is deliberately simple and fully configurable per building (see
:class:`gridweave.models.building.BuildingSpec`)::

    total    = min(capacity, forecast + backlog)
    base     = min(forecast, total)                 # this slot's own demand
    critical = min(base, max(minimum_operational_kw, critical_fraction * base))
    flexible = total - critical                     # includes all backlog
    minimum  = critical + min_flexible_fraction * (base - critical)

Deferred demand (backlog) is always flexible: it was already postponed once,
so by definition it is not critical. The comfort floor
(``min_flexible_fraction``) applies only to the slot's own flexible load.
"""
from __future__ import annotations

from gridweave.models.building import BuildingSpec
from gridweave.models.demand import LoadClassification
from gridweave.utils.validation import require_non_negative


class LoadClassifier:
    def __init__(self, spec: BuildingSpec) -> None:
        self.spec = spec

    def classify(self, forecast_kw: float, backlog_kw: float = 0.0) -> LoadClassification:
        forecast_kw = require_non_negative("forecast_kw", forecast_kw)
        backlog_kw = require_non_negative("backlog_kw", backlog_kw)
        spec = self.spec
        total = min(spec.capacity_kw, forecast_kw + backlog_kw)
        base = min(forecast_kw, total)
        critical = min(base, max(spec.minimum_operational_kw, spec.critical_fraction * base))
        flexible = total - critical
        minimum = critical + spec.min_flexible_fraction * (base - critical)
        return LoadClassification(total_kw=total, critical_kw=critical, flexible_kw=flexible, minimum_kw=minimum)

    def backlog_included(self, forecast_kw: float, backlog_kw: float) -> float:
        """How much of the backlog fits under capacity this slot."""
        return max(0.0, min(backlog_kw, self.spec.capacity_kw - forecast_kw))
