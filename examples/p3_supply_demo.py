"""Standalone Interactive Demonstration of Workstream 3 (Energy Supply Intelligence).

Demonstrates the full autonomous supply subsystem in action:
  - Grid Supply Agent: Dynamic time-of-use tariffs, scheduled restrictions, and outages
  - Solar Energy Agent: Physics-based generation, weather-aware forecasting, and curtailment
  - Battery Storage Agent: State-of-charge tracking, electrochemical efficiencies, reserves,
    and forecast-aware peak shaving
  - Dynamic Event Engine: Thunderstorms, sudden cloud cover, grid blackouts, and emergency restoration

Run with:
  python examples/p3_supply_demo.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest
from gridweave.supply.battery_agent import BatteryReservePolicy, BatteryStorageAgent
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.profiles.solar import SolarProfile
from gridweave.supply.profiles.tariff import TariffSchedule
from gridweave.supply.provider import CampusSupplyProvider
from gridweave.supply.solar_agent import SolarEnergyAgent


def print_banner(text: str) -> None:
    print("\n" + "=" * 80)
    print(f"  {text.upper()}")
    print("=" * 80)


def print_slot_telemetry(
    step_num: int,
    phase_name: str,
    slot: TimeSlot,
    provider: CampusSupplyProvider,
    demand_kw: float,
    dispatch_results: list,
) -> None:
    time_str = slot.start.strftime("%H:%M")
    grid = provider.grid_agents[0]
    solar = provider.solar_agents[0]
    battery = provider.battery_agents[0]

    offers = {o.source_id: o for o in provider._active_offers.values()}
    total_offered = sum(o.available_kw for o in offers.values())
    total_delivered = sum(r.delivered_kw for r in dispatch_results)
    grid_tariff = grid.current_tariff(slot)

    print(f"\n[STEP {step_num:02d} | {time_str}] Phase: {phase_name}")
    print("-" * 80)
    print(
        f"  Campus Demand Needed: {demand_kw:6.1f} kW | Total Offered: {total_offered:6.1f} kW | "
        f"Total Delivered: {total_delivered:6.1f} kW"
    )
    print(
        f"  Grid Supply:          Capacity: {grid.available_kw(slot):5.1f} kW | "
        f"Tariff: ${grid_tariff:5.2f}/kWh | "
        f"Dispatched: {offers['grid'].available_kw if 'grid' in offers else 0:5.1f} kW"
    )
    print(
        f"  Solar PV:             Generated: {solar.generation_kw(slot):5.1f} kW | "
        f"Cloud: {solar.profile.cloud_cover:4.0%} | "
        f"Dispatched: {offers['solar'].available_kw if 'solar' in offers else 0:5.1f} kW"
    )
    print(
            f"  Battery Storage:      SOC: {battery.soc:5.1%} | "
            f"Usable: {battery.available_discharge_kw(slot):5.1f} kW | "
            f"Mode: {battery.operating_mode.value.upper():10s}"
        )

    # Decisions & Dispatch breakdown
    print("  Physical Dispatch Execution:")
    for r in dispatch_results:
            print(
                f"    -> Source [{r.source_id:7s}]: Requested {r.requested_kw:5.1f} kW | "
                f"Delivered {r.delivered_kw:5.1f} kW "
                f"(Remaining: {r.remaining_capacity_kw:5.1f} kW)"
            )

    # Latest decision trace
    batt_offer = offers.get("battery")
    if batt_offer and "decision_trace" in batt_offer.constraints:
        print(f"  Battery Decision Trace: {batt_offer.constraints['decision_trace']}")


def main() -> None:
    print_banner("GridWeave Person 3 — Energy Supply Intelligence Standalone Demo")
    print("Initializing Autonomous Energy Source Agents...")

    # Configure agents
    tariff_sched = TariffSchedule()
    grid = GridSupplyAgent("grid", nominal_capacity_kw=350.0, tariff_schedule=tariff_sched)
    solar_profile = SolarProfile(installed_capacity_kw=160.0, cloud_cover=0.0)
    solar = SolarEnergyAgent("solar", profile=solar_profile)
    battery = BatteryStorageAgent(
        "battery",
        capacity_kwh=120.0,
        initial_soc=0.70,
        max_discharge_kw=45.0,
        max_charge_kw=45.0,
        charge_efficiency=0.95,
        discharge_efficiency=0.95,
        degradation_cost_per_kwh=7.0,
        reserve_policy=BatteryReservePolicy(physical_min_soc=0.10, operational_reserve_soc=0.20),
    )

    provider = CampusSupplyProvider([grid], [solar], [battery], auto_charge_surplus_solar=True)

    # Simulation timeline steps
    base_date = datetime(2026, 6, 15)
    timeline = [
        # (hour, minute, demand_kw, event_type, phase_name)
        (12, 0, 100.0, None, "Midday Solar Peak & High Solar Self-Consumption"),
        (12, 15, 60.0, None, "Midday Solar Surplus -> Storing Excess in Battery"),
        (14, 30, 140.0, "cloud_event", "Sudden Thunderstorm Cloud Event (Solar Drop)"),
        (15, 0, 150.0, "clear_sky", "Cloud Clearing -> Solar Restoration"),
        (17, 30, 220.0, None, "Evening Demand Ramp -> Transition to Peak Tariff ($18/kWh)"),
        (18, 0, 260.0, None, "Peak Demand Hour -> Battery Discharge Peak Shaving"),
        (19, 0, 240.0, "grid_blackout", "Emergency Grid Blackout Event -> Islanded Microgrid Mode"),
        (19, 15, 180.0, "emergency_reserve", "Emergency Reserve Authorized -> Battery Deep Discharge"),
        (20, 0, 160.0, "grid_restoration", "Grid Infrastructure Restored -> Grid Recovery"),
    ]

    step_idx = 1
    for hour, minute, demand_kw, event, phase_name in timeline:
        slot = TimeSlot(base_date.replace(hour=hour, minute=minute), duration_minutes=15)

        # Trigger dynamic events if designated
        if event == "cloud_event":
            provider.events.trigger_solar_drop(cloud_cover=0.85)
        elif event == "clear_sky":
            provider.events.trigger_solar_recovery()
        elif event == "grid_blackout":
            provider.events.trigger_grid_outage()
        elif event == "emergency_reserve":
            provider.events.trigger_emergency_reserve_release()
        elif event == "grid_restoration":
            provider.events.trigger_grid_restoration()

        # 1. P3 generates supply offers
        offers = provider.offers(slot)
        offer_by_id = {o.source_id: o.available_kw for o in offers}

        # 2. Auction / Market clearing simulation (merit order: solar 0.0 -> battery 7.0 -> grid 10-18)
        remaining = demand_kw
        reqs: list[DispatchRequest] = []

        # Solar dispatch
        if "solar" in offer_by_id:
            take_solar = min(remaining, offer_by_id["solar"])
            if take_solar > 0:
                reqs.append(DispatchRequest("solar", slot, take_solar))
                remaining -= take_solar

        # Battery dispatch during peak or grid shortage
        is_peak = tariff_sched.is_peak(slot) or offer_by_id.get("grid", 0.0) <= 0.01
        if is_peak and "battery" in offer_by_id:
            take_batt = min(remaining, offer_by_id["battery"])
            if take_batt > 0:
                reqs.append(DispatchRequest("battery", slot, take_batt))
                remaining -= take_batt

        # Grid dispatch for residual
        if "grid" in offer_by_id:
            take_grid = min(remaining, offer_by_id["grid"])
            if take_grid > 0:
                reqs.append(DispatchRequest("grid", slot, take_grid))
                remaining -= take_grid

        # 3. P3 physical dispatch execution
        results = provider.dispatch(reqs)

        # Print detailed telemetry
        print_slot_telemetry(step_idx, phase_name, slot, provider, demand_kw, results)
        step_idx += 1

    # Final Subsystem Audit Summary
    print_banner("Simulation Run Completed — Final KPI Audit")
    provider.accountant.summary()
    metrics = provider.metrics()

    print(f"  Total Energy Delivered:     {metrics.total_energy_delivered_kwh:8.2f} kWh")
    print(f"  Grid Imported:              {metrics.grid_import_kwh:8.2f} kWh")
    print(f"  Solar Generated:            {metrics.solar_generation_kwh:8.2f} kWh")
    print(f"  Solar Delivered to Campus:  {metrics.solar_delivered_kwh:8.2f} kWh")
    print(f"  Solar Curtailed:            {metrics.solar_curtailed_kwh:8.2f} kWh")
    print(f"  Solar Self-Consumption:     {metrics.solar_self_consumption_rate:8.1%}")
    print(f"  Battery Energy Discharged:  {metrics.battery_discharge_kwh:8.2f} kWh")
    print(f"  Battery Energy Charged:     {metrics.battery_charge_kwh:8.2f} kWh")
    print(f"  Total Supply Procurement:   ${metrics.total_procurement_cost:8.2f}")
    print(f"  Levelized Supply Cost:      ${metrics.levelized_cost_per_kwh:8.3f} / kWh")
    print(f"  Estimated CO2 Displaced:    {metrics.co2_displaced_kg:8.2f} kg CO2")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
