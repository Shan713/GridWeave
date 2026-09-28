"""Unit tests for Rule-Based (Strategy A) and Forecast-Aware (Strategy B) battery strategies."""
from datetime import datetime

import pytest

from gridweave.models.common import TimeSlot
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.optimization import (
    ForecastAwareBatteryStrategy,
    RuleBasedBatteryStrategy,
    evaluate_schedule_cost,
)
from gridweave.supply.profiles.tariff import TariffSchedule


@pytest.fixture
def noon_slot():
    return TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)


@pytest.fixture
def evening_slot():
    return TimeSlot(datetime(2026, 6, 15, 19, 0), duration_minutes=15)


def test_rule_based_strategy_solar_surplus(noon_slot):
    strat = RuleBasedBatteryStrategy()
    battery = BatteryStorageAgent(capacity_kwh=100.0, initial_soc=0.50, max_charge_kw=40.0)
    tariffs = TariffSchedule()

    # Solar (100 kW) exceeds campus demand (40 kW) -> Surplus 60 kW
    action = strat.decide(
        battery=battery,
        slot=noon_slot,
        predicted_demand_kw=40.0,
        solar_generation_kw=100.0,
        tariff_schedule=tariffs,
    )

    assert action.mode == "charge"
    assert action.recommended_charge_kw == 40.0  # Clamped to max_charge_kw
    assert action.recommended_offer_kw == 0.0


def test_rule_based_strategy_peak_shaving(evening_slot):
    strat = RuleBasedBatteryStrategy()
    battery = BatteryStorageAgent(capacity_kwh=100.0, initial_soc=0.80, max_discharge_kw=40.0)
    tariffs = TariffSchedule()

    # Evening peak (19:00, tariff 18.0) with net demand 50 kW
    action = strat.decide(
        battery=battery,
        slot=evening_slot,
        predicted_demand_kw=50.0,
        solar_generation_kw=0.0,
        tariff_schedule=tariffs,
    )

    assert action.mode == "discharge"
    assert action.recommended_offer_kw == 40.0  # Discharges to shave peak


def test_forecast_aware_arbitrage(noon_slot):
    # Afternoon: Tariff is standard (10.0), but evening peak will be 18.0
    strat = ForecastAwareBatteryStrategy(horizon_slots=8)
    battery = BatteryStorageAgent(capacity_kwh=100.0, initial_soc=0.70, max_discharge_kw=30.0)
    tariffs = TariffSchedule()

    demands = [30.0] * 8
    solars = [0.0] * 8  # Moderate demand throughout

    plan = strat.optimize(
        battery=battery,
        start_slot=noon_slot,
        demand_forecast_kw=demands,
        solar_forecast=solars,
        tariff_schedule=tariffs,
    )

    assert plan.projected_cost >= 0.0
    assert len(plan.horizon_points) == 8


def test_schedule_cost_breakdown():
    breakdown = evaluate_schedule_cost(
        grid_powers_kw=[50.0, 50.0],
        tariffs_per_kwh=[10.0, 18.0],
        discharge_powers_kw=[20.0, 0.0],
        degradation_cost_per_kwh=7.0,
        shortage_powers_kw=[0.0, 0.0],
        shortage_penalty_per_kwh=50.0,
        final_soc=0.55,
        target_terminal_soc=0.50,
        slot_hours=0.25,
    )

    # Grid cost: (50*10*0.25) + (50*18*0.25) = 125 + 225 = 350.0
    assert breakdown.grid_cost == 350.0
    # Degradation: 20 * 7 * 0.25 = 35.0
    assert breakdown.degradation_cost == 35.0
    assert breakdown.shortage_penalty == 0.0
    assert breakdown.terminal_penalty == 0.0
    assert breakdown.total_cost == 385.0
