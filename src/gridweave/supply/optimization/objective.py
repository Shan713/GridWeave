"""Battery optimization objectives, cost modeling, and schedule evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class ScheduleCostBreakdown:
    """Multi-slot schedule cost breakdown."""

    grid_cost: float
    degradation_cost: float
    shortage_penalty: float
    terminal_penalty: float
    total_cost: float

    def to_dict(self) -> dict:
        return {
            "grid_cost": round(self.grid_cost, 4),
            "degradation_cost": round(self.degradation_cost, 4),
            "shortage_penalty": round(self.shortage_penalty, 4),
            "terminal_penalty": round(self.terminal_penalty, 4),
            "total_cost": round(self.total_cost, 4),
        }


def evaluate_schedule_cost(
    grid_powers_kw: Sequence[float],
    tariffs_per_kwh: Sequence[float],
    discharge_powers_kw: Sequence[float],
    degradation_cost_per_kwh: float,
    shortage_powers_kw: Sequence[float],
    shortage_penalty_per_kwh: float,
    final_soc: float,
    target_terminal_soc: float,
    terminal_penalty_weight: float = 50.0,
    slot_hours: float = 0.25,
) -> ScheduleCostBreakdown:
    """Calculate multi-objective cost for a candidate battery schedule.

    Total Objective = Grid Procurement Cost + Battery Degradation Cost
                    + Shortage Penalty + Terminal SOC Target Penalty
    """
    grid_cost = sum(
        p * t * slot_hours for p, t in zip(grid_powers_kw, tariffs_per_kwh)
    )
    deg_cost = sum(
        p * degradation_cost_per_kwh * slot_hours for p in discharge_powers_kw
    )
    shortage_cost = sum(
        p * shortage_penalty_per_kwh * slot_hours for p in shortage_powers_kw
    )

    # Quadratic penalty for falling short of desired terminal SOC (e.g. 0.50)
    soc_deficit = max(0.0, target_terminal_soc - final_soc)
    terminal_cost = terminal_penalty_weight * (soc_deficit ** 2)

    total = grid_cost + deg_cost + shortage_cost + terminal_cost

    return ScheduleCostBreakdown(
        grid_cost=grid_cost,
        degradation_cost=deg_cost,
        shortage_penalty=shortage_cost,
        terminal_penalty=terminal_cost,
        total_cost=total,
    )
