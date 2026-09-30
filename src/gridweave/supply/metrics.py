"""Supply-side performance metrics, KPI calculations, and environmental impact reporting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gridweave.supply.accounting import SupplyAccountant


@dataclass(frozen=True)
class SupplyMetrics:
    """Consolidated KPI report for academic evaluation and dashboard display."""

    total_energy_delivered_kwh: float
    grid_import_kwh: float
    peak_grid_import_kw: float
    solar_generation_kwh: float
    solar_delivered_kwh: float
    solar_curtailed_kwh: float
    solar_curtailment_rate: float
    solar_self_consumption_rate: float
    renewable_penetration_rate: float
    battery_discharge_kwh: float
    battery_charge_kwh: float
    battery_cycles: float
    total_procurement_cost: float
    levelized_cost_per_kwh: float
    co2_displaced_kg: float
    grid_to_battery_kwh: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_energy_delivered_kwh": round(self.total_energy_delivered_kwh, 2),
            "grid_import_kwh": round(self.grid_import_kwh, 2),
            "peak_grid_import_kw": round(self.peak_grid_import_kw, 2),
            "solar_generation_kwh": round(self.solar_generation_kwh, 2),
            "solar_delivered_kwh": round(self.solar_delivered_kwh, 2),
            "solar_curtailed_kwh": round(self.solar_curtailed_kwh, 2),
            "solar_curtailment_rate": round(self.solar_curtailment_rate, 4),
            "solar_self_consumption_rate": round(self.solar_self_consumption_rate, 4),
            "renewable_penetration_rate": round(self.renewable_penetration_rate, 4),
            "battery_discharge_kwh": round(self.battery_discharge_kwh, 2),
            "battery_charge_kwh": round(self.battery_charge_kwh, 2),
            "battery_cycles": round(self.battery_cycles, 4),
            "total_procurement_cost": round(self.total_procurement_cost, 2),
            "levelized_cost_per_kwh": round(self.levelized_cost_per_kwh, 4),
            "co2_displaced_kg": round(self.co2_displaced_kg, 2),
            "grid_to_battery_kwh": round(self.grid_to_battery_kwh, 2),
        }


class SupplyMetricsCalculator:
    """Computes comprehensive supply subsystem metrics from the operational accountant."""

    @staticmethod
    def compute(
        accountant: SupplyAccountant,
        peak_grid_kw: float = 0.0,
        battery_nominal_capacity_kwh: float = 200.0,
        grid_co2_kg_per_kwh: float = 0.82,  # Typical coal/gas marginal grid emission factor
    ) -> SupplyMetrics:
        s = accountant.summary()
        gen = s["cumulative_solar_gen_kwh"]
        deliv = s["cumulative_delivered_kwh"]
        curt = s["cumulative_solar_curt_kwh"]
        disch = s["cumulative_battery_disch_kwh"]
        chg = s["cumulative_battery_chg_kwh"]

        curt_rate = curt / gen if gen > 0 else 0.0
        self_cons_rate = (gen - curt) / gen if gen > 0 else 1.0
        renew_rate = s["cumulative_solar_del_kwh"] / deliv if deliv > 0 else 0.0
        cycles = (disch + chg) / (2.0 * battery_nominal_capacity_kwh) if battery_nominal_capacity_kwh > 0 else 0.0
        grid_to_batt = s.get("cumulative_grid_to_batt_kwh", 0.0)
        # Battery energy that came from the grid displaces no grid emissions.
        co2_disp = (s["cumulative_solar_del_kwh"] + max(0.0, disch - grid_to_batt)) * grid_co2_kg_per_kwh

        return SupplyMetrics(
            total_energy_delivered_kwh=s["cumulative_delivered_kwh"],
            grid_import_kwh=s["cumulative_grid_kwh"],
            peak_grid_import_kw=peak_grid_kw,
            solar_generation_kwh=gen,
            solar_delivered_kwh=s["cumulative_solar_del_kwh"],
            solar_curtailed_kwh=curt,
            solar_curtailment_rate=curt_rate,
            solar_self_consumption_rate=self_cons_rate,
            renewable_penetration_rate=renew_rate,
            battery_discharge_kwh=disch,
            battery_charge_kwh=chg,
            battery_cycles=cycles,
            total_procurement_cost=s["cumulative_total_cost"],
            levelized_cost_per_kwh=s["levelized_cost_per_kwh"],
            co2_displaced_kg=co2_disp,
            grid_to_battery_kwh=grid_to_batt,
        )
