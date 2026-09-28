"""Comprehensive experimental benchmark for Workstream 3 (Energy Supply Intelligence).

Executes:
  Experiment 1: Solar Forecasting Benchmark (Persistence vs Time-of-Day vs Weather-Aware)
                under clear-sky and cloudy conditions across multiple horizons.
  Experiment 2: Multi-Strategy Supply Benchmark
                (Grid-Only vs Grid+Solar vs Grid+Solar+RuleBased vs Grid+Solar+ForecastAware)
                measuring grid import, procurement cost, peak demand, solar utilization,
                battery throughput, degradation cost, and runtime.

Outputs JSON results to: data/results/p3_experiment_results.json
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.forecasting import (
    PersistenceMode,
    PersistenceSolarForecaster,
    TimeOfDaySolarForecaster,
    WeatherAwareSolarForecaster,
    rolling_origin_evaluation,
)
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.optimization import (
    ForecastAwareBatteryStrategy,
    RuleBasedBatteryStrategy,
)
from gridweave.supply.profiles.solar import SolarProfile
from gridweave.supply.profiles.tariff import TariffSchedule
from gridweave.supply.provider import CampusSupplyProvider
from gridweave.supply.solar_agent import SolarEnergyAgent


def generate_campus_demand_trace(slots: int = 96, base_kw: float = 120.0, peak_kw: float = 280.0) -> list[float]:
    """Synthetic 24h campus demand trace (hostels, labs, academic blocks)."""
    trace = []
    for i in range(slots):
        hour = i * 0.25
        # Off-peak base demand at night (00:00 - 06:00)
        if hour < 6.0:
            dem = base_kw + 15.0 * math.sin(hour)
        # Morning ramp and academic hours (08:00 - 17:00)
        elif 8.0 <= hour < 17.0:
            dem = 190.0 + 35.0 * math.sin((hour - 8.0) * math.pi / 9.0)
        # Evening peak (17:00 - 22:00: hostels active, labs running evening compute)
        elif 17.0 <= hour < 22.0:
            dem = peak_kw * (0.85 + 0.15 * math.sin((hour - 17.0) * math.pi / 5.0))
        else:
            dem = 140.0
        trace.append(round(dem, 2))
    return trace


def run_solar_forecasting_experiment() -> dict[str, Any]:
    print("=" * 70)
    print("RUNNING EXPERIMENT 1: SOLAR FORECASTING BENCHMARK")
    print("=" * 70)

    # 3-day series (288 slots)
    profile_clear = SolarProfile(installed_capacity_kw=200.0, cloud_cover=0.0, seed=101)
    profile_cloudy = SolarProfile(installed_capacity_kw=200.0, cloud_cover=0.6, seed=202)

    start_dt = datetime(2026, 6, 15, 0, 0)
    series_clear: list[tuple[TimeSlot, float]] = []
    series_cloudy: list[tuple[TimeSlot, float]] = []

    cur = TimeSlot(start_dt, duration_minutes=15)
    for _ in range(288):
        series_clear.append((cur, profile_clear.generation_kw(cur)))
        series_cloudy.append((cur, profile_cloudy.generation_kw(cur)))
        cur = cur.next()

    forecasters = {
        "naive_persistence": lambda p: PersistenceSolarForecaster(p, mode=PersistenceMode.NAIVE_PERSISTENCE),
        "seasonal_persistence": lambda p: PersistenceSolarForecaster(p, mode=PersistenceMode.SEASONAL_PERSISTENCE),
        "time_of_day_ema": lambda p: TimeOfDaySolarForecaster(p, alpha=0.35),
        "weather_aware": lambda p: WeatherAwareSolarForecaster(p),
    }

    horizons = [1, 4, 8]  # 15-min ahead, 1-hour ahead, 2-hours ahead
    results: dict[str, Any] = {"clear_sky": {}, "cloudy": {}}

    for scenario_name, profile, series in [
        ("clear_sky", profile_clear, series_clear),
        ("cloudy", profile_cloudy, series_cloudy),
    ]:
        print(f"\nScenario: {scenario_name.upper()}")
        for f_name, f_factory in forecasters.items():
            results[scenario_name][f_name] = {}
            for h in horizons:
                f_inst = f_factory(profile)
                res = rolling_origin_evaluation(f_inst, series, horizon=h, warmup_slots=96)
                results[scenario_name][f_name][f"horizon_{h}"] = {
                    "mae_kw": res["mae_total"],
                    "rmse_kw": res["rmse_total"],
                }
                print(
                    f"  [{f_name:20s}] Horizon {h*15:2d}m -> "
                    f"MAE: {res['mae_total']:6.2f} kW | RMSE: {res['rmse_total']:6.2f} kW"
                )

    return results


def run_supply_strategies_experiment() -> dict[str, Any]:
    print("\n" + "=" * 70)
    print("RUNNING EXPERIMENT 2: MULTI-STRATEGY SUPPLY BENCHMARK")
    print("=" * 70)

    slots = 96
    start_dt = datetime(2026, 6, 15, 0, 0)
    demand_trace = generate_campus_demand_trace(slots=slots)
    time_slots = [TimeSlot(start_dt + timedelta(minutes=15 * i), duration_minutes=15) for i in range(slots)]
    tariffs = TariffSchedule()

    strategies = [
        "grid_only",
        "grid_solar",
        "grid_solar_rule_based_battery",
        "grid_solar_forecast_aware_battery",
    ]

    results: dict[str, Any] = {}

    for strat in strategies:
        t_start = time.perf_counter()

        # Build clean source agents
        grid = GridSupplyAgent("grid", nominal_capacity_kw=400.0, tariff_schedule=tariffs)
        solar = (
            SolarEnergyAgent("solar", installed_capacity_kw=180.0)
            if "solar" in strat
            else None
        )
        battery = (
            BatteryStorageAgent(
                "battery",
                capacity_kwh=150.0,
                initial_soc=0.75,
                max_discharge_kw=50.0,
                max_charge_kw=50.0,
                degradation_cost_per_kwh=7.0,
            )
            if "battery" in strat
            else None
        )

        provider = CampusSupplyProvider(
            grid_agents=[grid],
            solar_agents=[solar] if solar else [],
            battery_agents=[battery] if battery else [],
            auto_charge_surplus_solar=True,
        )

        rule_strat = RuleBasedBatteryStrategy()
        opt_strat = ForecastAwareBatteryStrategy(horizon_slots=8)

        soc_trajectory: list[float] = []

        # Run 96 slots
        for t, slot in enumerate(time_slots):
            dem_kw = demand_trace[t]
            offers = provider.offers(slot)
            offer_map = {o.source_id: o.available_kw for o in offers}

            # Determine dispatch according to strategy
            dispatch_reqs: list[DispatchRequest] = []
            rem_dem = dem_kw

            # 1. Solar dispatch (merit order: solar is 0 cost)
            solar_disp = 0.0
            if "solar" in strat and "solar" in offer_map:
                solar_disp = min(rem_dem, offer_map["solar"])
                dispatch_reqs.append(DispatchRequest("solar", slot, solar_disp))
                rem_dem -= solar_disp

            # 2. Battery dispatch
            batt_disp = 0.0
            if "battery" in strat and battery is not None:
                if strat == "grid_solar_rule_based_battery":
                    act = rule_strat.decide(
                        battery=battery,
                        slot=slot,
                        predicted_demand_kw=dem_kw,
                        solar_generation_kw=solar.generation_kw(slot) if solar else 0.0,
                        tariff_schedule=tariffs,
                    )
                    if act.mode == "discharge":
                        batt_disp = min(rem_dem, act.recommended_offer_kw, offer_map.get("battery", 0.0))
                elif strat == "grid_solar_forecast_aware_battery":
                    future_dem = demand_trace[t : t + 8]
                    fc_solar = (
                        solar.forecast(slot, horizon=len(future_dem))
                        if solar
                        else [0.0] * len(future_dem)
                    )
                    plan = opt_strat.optimize(
                        battery=battery,
                        start_slot=slot,
                        demand_forecast_kw=future_dem,
                        solar_forecast=fc_solar,
                        tariff_schedule=tariffs,
                    )
                    if plan.first_slot_mode == "discharge":
                        batt_disp = min(rem_dem, plan.first_slot_dispatch_kw, offer_map.get("battery", 0.0))

                if batt_disp > 0.01:
                    dispatch_reqs.append(DispatchRequest("battery", slot, batt_disp))
                    rem_dem -= batt_disp

            # 3. Grid dispatch (covers residual demand)
            grid_disp = min(rem_dem, offer_map.get("grid", 0.0))
            if grid_disp > 0.0:
                dispatch_reqs.append(DispatchRequest("grid", slot, grid_disp))
                rem_dem -= grid_disp

            # Execute dispatch
            provider.dispatch(dispatch_reqs)
            if battery is not None:
                soc_trajectory.append(round(battery.soc, 4))

        t_elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        metrics = provider.metrics()
        acc = provider.accountant.summary()

        results[strat] = {
            "total_energy_delivered_kwh": metrics.total_energy_delivered_kwh,
            "grid_import_kwh": metrics.grid_import_kwh,
            "peak_grid_import_kw": metrics.peak_grid_import_kw,
            "grid_procurement_cost": acc["cumulative_grid_cost"],
            "battery_degradation_cost": acc["cumulative_battery_deg_cost"],
            "total_procurement_cost": metrics.total_procurement_cost,
            "levelized_cost_per_kwh": metrics.levelized_cost_per_kwh,
            "solar_generation_kwh": metrics.solar_generation_kwh,
            "solar_delivered_kwh": metrics.solar_delivered_kwh,
            "solar_curtailed_kwh": metrics.solar_curtailed_kwh,
            "solar_self_consumption_rate": metrics.solar_self_consumption_rate,
            "battery_throughput_kwh": acc["cumulative_battery_disch_kwh"] + acc["cumulative_battery_chg_kwh"],
            "battery_cycles": metrics.battery_cycles,
            "runtime_ms": round(t_elapsed_ms, 2),
            "final_soc": round(battery.soc, 4) if battery else None,
        }

        print(f"\nStrategy: {strat.upper()}")
        print(f"  Delivered Energy:    {metrics.total_energy_delivered_kwh:8.2f} kWh")
        print(f"  Grid Imported:       {metrics.grid_import_kwh:8.2f} kWh")
        print(f"  Peak Grid Import:    {metrics.peak_grid_import_kw:8.2f} kW")
        print(f"  Grid Cost:           ${acc['cumulative_grid_cost']:8.2f}")
        print(f"  Battery Deg Cost:    ${acc['cumulative_battery_deg_cost']:8.2f}")
        print(f"  Total Cost:          ${metrics.total_procurement_cost:8.2f}")
        print(f"  Levelized Cost:      ${metrics.levelized_cost_per_kwh:8.3f}/kWh")
        print(f"  Solar Self-Consump:  {metrics.solar_self_consumption_rate:8.1%}")
        print(f"  Runtime:             {t_elapsed_ms:8.2f} ms")

    return results


def main() -> None:
    os.makedirs("data/results", exist_ok=True)
    exp1_res = run_solar_forecasting_experiment()
    exp2_res = run_supply_strategies_experiment()

    combined = {
        "timestamp": datetime.now().isoformat(),
        "workstream": "Person 3 — Energy Supply Intelligence",
        "experiment_1_solar_forecasting": exp1_res,
        "experiment_2_supply_strategies": exp2_res,
    }

    out_path = "data/results/p3_experiment_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2)

    print("\n" + "=" * 70)
    print(f"EXPERIMENTS COMPLETE. Saved results to: {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
