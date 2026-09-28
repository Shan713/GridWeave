"""Energy conservation ledger and financial cost accounting for the supply subsystem."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchResult, SourceType
from gridweave.utils.validation import ValidationError


class ConservationViolation(ValidationError):
    """Raised when first law of thermodynamics (energy balance) is violated."""


@dataclass
class SlotEnergyRecord:
    """Detailed energy flows for a single 15-minute simulation slot."""

    slot: TimeSlot
    grid_imported_kwh: float
    solar_generated_kwh: float
    solar_delivered_kwh: float
    solar_curtailed_kwh: float
    solar_to_battery_kwh: float
    battery_charged_kwh: float
    battery_discharged_kwh: float
    battery_losses_kwh: float
    total_delivered_to_campus_kwh: float
    grid_cost: float
    battery_degradation_cost: float
    total_supply_cost: float

    def verify_conservation(self, tolerance_kwh: float = 0.005) -> None:
        """Verify first-law energy conservation for this slot."""
        # 1. Total delivered to campus must equal sum of source deliveries
        sum_deliveries = (
            self.grid_imported_kwh
            + self.solar_delivered_kwh
            + self.battery_discharged_kwh
        )
        diff = abs(self.total_delivered_to_campus_kwh - sum_deliveries)
        if diff > tolerance_kwh:
            raise ConservationViolation(
                f"Energy conservation violation in slot {self.slot}: "
                f"Campus received {self.total_delivered_to_campus_kwh:.4f} kWh, "
                f"but sum of source dispatches is {sum_deliveries:.4f} kWh (diff={diff:.4f} kWh)"
            )

        # 2. Solar balance: generated = delivered + curtailed + to_battery
        solar_accounted = self.solar_delivered_kwh + self.solar_curtailed_kwh + self.solar_to_battery_kwh
        solar_diff = abs(self.solar_generated_kwh - solar_accounted)
        if solar_diff > tolerance_kwh:
            raise ConservationViolation(
                f"Solar energy balance violation in slot {self.slot}: "
                f"Generated {self.solar_generated_kwh:.4f} kWh != Accounted {solar_accounted:.4f} kWh"
            )


class SupplyAccountant:
    """Audits and records all physical energy flows and procurement expenses across time slots."""

    def __init__(self) -> None:
        self.history: list[SlotEnergyRecord] = []
        self.cumulative_grid_kwh: float = 0.0
        self.cumulative_solar_gen_kwh: float = 0.0
        self.cumulative_solar_del_kwh: float = 0.0
        self.cumulative_solar_curt_kwh: float = 0.0
        self.cumulative_solar_to_batt_kwh: float = 0.0
        self.cumulative_battery_disch_kwh: float = 0.0
        self.cumulative_battery_chg_kwh: float = 0.0
        self.cumulative_battery_losses_kwh: float = 0.0
        self.cumulative_delivered_kwh: float = 0.0
        self.cumulative_grid_cost: float = 0.0
        self.cumulative_battery_deg_cost: float = 0.0
        self.cumulative_total_cost: float = 0.0

    def record_slot(
        self,
        slot: TimeSlot,
        results: Sequence[DispatchResult],
        solar_generated_kw: float = 0.0,
        battery_charged_kw: float = 0.0,
        battery_efficiency_losses_kwh: float = 0.0,
        grid_tariff: float = 10.0,
        battery_deg_rate: float = 7.0,
        source_types: Mapping[str, SourceType] | None = None,
    ) -> SlotEnergyRecord:
        """Process and verify physical dispatch results for one market slot."""
        dt_hours = slot.hours

        grid_kw = 0.0
        solar_kw = 0.0
        battery_kw = 0.0

        source_types = source_types or {}
        for r in results:
            source_type = source_types.get(r.source_id)
            if source_type == SourceType.GRID or (source_type is None and "grid" in r.source_id.lower()):
                grid_kw += r.delivered_kw
            elif source_type == SourceType.SOLAR or (source_type is None and "solar" in r.source_id.lower()):
                solar_kw += r.delivered_kw
            elif source_type == SourceType.BATTERY or (source_type is None and "battery" in r.source_id.lower()):
                battery_kw += r.delivered_kw

        grid_kwh = grid_kw * dt_hours
        solar_del_kwh = solar_kw * dt_hours
        solar_gen_kwh = solar_generated_kw * dt_hours
        battery_disch_kwh = battery_kw * dt_hours
        battery_chg_kwh = battery_charged_kw * dt_hours
        solar_to_batt_kwh = (
            min(solar_gen_kwh - solar_del_kwh, battery_chg_kwh)
            if solar_gen_kwh > solar_del_kwh
            else 0.0
        )
        solar_curt_kwh = max(0.0, solar_gen_kwh - solar_del_kwh - solar_to_batt_kwh)

        total_del_kwh = grid_kwh + solar_del_kwh + battery_disch_kwh

        grid_cost = sum(
            r.delivered_kw * dt_hours * float(r.state.get("tariff_rate", grid_tariff))
            for r in results
            if source_types.get(r.source_id) == SourceType.GRID
            or (r.source_id not in source_types and "grid" in r.source_id.lower())
        )
        deg_cost = sum(
            float(r.state.get("degradation_cost", r.delivered_kw * dt_hours * battery_deg_rate))
            for r in results
            if source_types.get(r.source_id) == SourceType.BATTERY
            or (r.source_id not in source_types and "battery" in r.source_id.lower())
        )
        total_cost = grid_cost + deg_cost

        record = SlotEnergyRecord(
            slot=slot,
            grid_imported_kwh=round(grid_kwh, 4),
            solar_generated_kwh=round(solar_gen_kwh, 4),
            solar_delivered_kwh=round(solar_del_kwh, 4),
            solar_curtailed_kwh=round(solar_curt_kwh, 4),
            solar_to_battery_kwh=round(solar_to_batt_kwh, 4),
            battery_charged_kwh=round(battery_chg_kwh, 4),
            battery_discharged_kwh=round(battery_disch_kwh, 4),
            battery_losses_kwh=round(battery_efficiency_losses_kwh, 4),
            total_delivered_to_campus_kwh=round(total_del_kwh, 4),
            grid_cost=round(grid_cost, 4),
            battery_degradation_cost=round(deg_cost, 4),
            total_supply_cost=round(total_cost, 4),
        )

        record.verify_conservation()
        self.history.append(record)

        # Update cumulative totals
        self.cumulative_grid_kwh += grid_kwh
        self.cumulative_solar_gen_kwh += solar_gen_kwh
        self.cumulative_solar_del_kwh += solar_del_kwh
        self.cumulative_solar_curt_kwh += solar_curt_kwh
        self.cumulative_solar_to_batt_kwh += solar_to_batt_kwh
        self.cumulative_battery_disch_kwh += battery_disch_kwh
        self.cumulative_battery_chg_kwh += battery_chg_kwh
        self.cumulative_battery_losses_kwh += battery_efficiency_losses_kwh
        self.cumulative_delivered_kwh += total_del_kwh
        self.cumulative_grid_cost += grid_cost
        self.cumulative_battery_deg_cost += deg_cost
        self.cumulative_total_cost += total_cost

        return record

    def reset(self) -> None:
        self.history.clear()
        self.cumulative_grid_kwh = 0.0
        self.cumulative_solar_gen_kwh = 0.0
        self.cumulative_solar_del_kwh = 0.0
        self.cumulative_solar_curt_kwh = 0.0
        self.cumulative_solar_to_batt_kwh = 0.0
        self.cumulative_battery_disch_kwh = 0.0
        self.cumulative_battery_chg_kwh = 0.0
        self.cumulative_battery_losses_kwh = 0.0
        self.cumulative_delivered_kwh = 0.0
        self.cumulative_grid_cost = 0.0
        self.cumulative_battery_deg_cost = 0.0
        self.cumulative_total_cost = 0.0

    def summary(self) -> dict[str, Any]:
        """Comprehensive summary of physical and economic performance."""
        solar_util = (
            (self.cumulative_solar_del_kwh + self.cumulative_solar_to_batt_kwh) / self.cumulative_solar_gen_kwh
            if self.cumulative_solar_gen_kwh > 0
            else 1.0
        )
        renew_fraction = (
            self.cumulative_solar_del_kwh / self.cumulative_delivered_kwh
            if self.cumulative_delivered_kwh > 0
            else 0.0
        )
        levelized_cost = (
            self.cumulative_total_cost / self.cumulative_delivered_kwh
            if self.cumulative_delivered_kwh > 0
            else 0.0
        )

        return {
            "total_slots": len(self.history),
            "cumulative_delivered_kwh": round(self.cumulative_delivered_kwh, 4),
            "cumulative_grid_kwh": round(self.cumulative_grid_kwh, 4),
            "cumulative_solar_gen_kwh": round(self.cumulative_solar_gen_kwh, 4),
            "cumulative_solar_del_kwh": round(self.cumulative_solar_del_kwh, 4),
            "cumulative_solar_curt_kwh": round(self.cumulative_solar_curt_kwh, 4),
            "cumulative_battery_disch_kwh": round(self.cumulative_battery_disch_kwh, 4),
            "cumulative_battery_chg_kwh": round(self.cumulative_battery_chg_kwh, 4),
            "solar_utilization_ratio": round(solar_util, 4),
            "renewable_penetration_ratio": round(renew_fraction, 4),
            "cumulative_grid_cost": round(self.cumulative_grid_cost, 4),
            "cumulative_battery_deg_cost": round(self.cumulative_battery_deg_cost, 4),
            "cumulative_total_cost": round(self.cumulative_total_cost, 4),
            "levelized_cost_per_kwh": round(levelized_cost, 4),
        }
