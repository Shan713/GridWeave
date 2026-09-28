"""Fairness metrics, deprivation tracking, and starvation prevention for GridWeave.

Implements Jain's Fairness Index, multi-slot deprivation accounting, and dynamic
priority boosts to ensure that low-budget buildings are not perpetually starved.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence


def jains_fairness_index(values: Sequence[float]) -> float:
    """Compute Jain's Fairness Index over a list of non-negative values (e.g. satisfaction ratios).

    Formula:
        J(x) = (sum(x_i))^2 / (n * sum(x_i^2))

    Returns:
        float in [1/n, 1.0]. Returns 1.0 if all values are 0 or list is empty.
    """
    clean_vals = [max(0.0, v) for v in values]
    n = len(clean_vals)
    if n == 0:
        return 1.0
    sum_vals = sum(clean_vals)
    if sum_vals <= 0.0:
        # All buildings received 0 ratio, uniformly identical outcome
        return 1.0
    sum_sq = sum(v * v for v in clean_vals)
    if sum_sq <= 0.0:
        return 1.0
    return float(min(1.0, max(0.0, (sum_vals * sum_vals) / (n * sum_sq))))


@dataclass
class BuildingFairnessRecord:
    """Historical tracking for a single building's market experience."""

    building_id: str
    total_requested_kwh: float = 0.0
    total_allocated_kwh: float = 0.0
    consecutive_starved_slots: int = 0
    total_slots_participated: int = 0
    total_starved_slots: int = 0

    @property
    def cumulative_service_ratio(self) -> float:
        if self.total_requested_kwh <= 0:
            return 1.0
        return min(1.0, self.total_allocated_kwh / self.total_requested_kwh)

    @property
    def unserved_kwh(self) -> float:
        return max(0.0, self.total_requested_kwh - self.total_allocated_kwh)


class FairnessTracker:
    """Tracks multi-slot service history to detect starvation and compute deprivation boosts.

    A building is considered 'starved' in a slot if its satisfaction ratio is below
    ``starvation_threshold`` (default 0.50). Consecutive starved slots increase the
    building's deprivation boost, allowing it to compete fairly in future rounds.
    """

    def __init__(
        self,
        starvation_threshold: float = 0.50,
        max_boost_slots: int = 5,
    ) -> None:
        self.starvation_threshold = starvation_threshold
        self.max_boost_slots = max(1, max_boost_slots)
        self.records: dict[str, BuildingFairnessRecord] = {}

    def get_record(self, building_id: str) -> BuildingFairnessRecord:
        if building_id not in self.records:
            self.records[building_id] = BuildingFairnessRecord(building_id=building_id)
        return self.records[building_id]

    def record_slot(
        self,
        building_id: str,
        requested_kw: float,
        allocated_kw: float,
        duration_hours: float = 0.25,
    ) -> None:
        """Update a building's fairness record after a slot settles."""
        rec = self.get_record(building_id)
        rec.total_slots_participated += 1
        req_kwh = requested_kw * duration_hours
        alloc_kwh = min(requested_kw, allocated_kw) * duration_hours
        rec.total_requested_kwh += req_kwh
        rec.total_allocated_kwh += alloc_kwh

        sat_ratio = (alloc_kwh / req_kwh) if req_kwh > 1e-6 else 1.0
        if sat_ratio < self.starvation_threshold:
            rec.consecutive_starved_slots += 1
            rec.total_starved_slots += 1
        else:
            rec.consecutive_starved_slots = 0

    def get_deprivation_boost(self, building_id: str) -> float:
        """Compute normalized deprivation factor in [0, 1] for bid scoring.

        Scales with consecutive starved slots up to max_boost_slots.
        """
        rec = self.records.get(building_id)
        if not rec:
            return 0.0
        return min(1.0, rec.consecutive_starved_slots / self.max_boost_slots)

    def get_all_deprivation_boosts(self) -> dict[str, float]:
        """Return mapping of building_id -> deprivation_boost."""
        return {b_id: self.get_deprivation_boost(b_id) for b_id in self.records}

    def detect_starving_buildings(self, min_consecutive_slots: int = 2) -> list[str]:
        """Return list of buildings currently experiencing consecutive starvation."""
        return [
            b_id
            for b_id, rec in self.records.items()
            if rec.consecutive_starved_slots >= min_consecutive_slots
        ]

    def summary(self) -> dict[str, float]:
        """Aggregate fairness metrics across all tracked buildings."""
        if not self.records:
            return {
                "jains_service_index": 1.0,
                "max_consecutive_starved_slots": 0.0,
                "total_starved_slots": 0.0,
                "average_service_ratio": 1.0,
            }
        ratios = [r.cumulative_service_ratio for r in self.records.values()]
        jain = jains_fairness_index(ratios)
        max_starved = max(r.consecutive_starved_slots for r in self.records.values())
        total_starved = sum(r.total_starved_slots for r in self.records.values())
        avg_ratio = sum(ratios) / len(ratios)

        return {
            "jains_service_index": round(jain, 4),
            "max_consecutive_starved_slots": float(max_starved),
            "total_starved_slots": float(total_starved),
            "average_service_ratio": round(avg_ratio, 4),
        }
