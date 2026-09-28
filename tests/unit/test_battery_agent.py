"""Unit tests for the autonomous BatteryStorageAgent and electrochemical state transitions."""
from datetime import datetime

import pytest

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, SourceType
from gridweave.supply.battery_agent import (
    BatteryOperatingMode,
    BatteryReservePolicy,
    BatteryStorageAgent,
)


@pytest.fixture
def sample_slot():
    return TimeSlot(datetime(2026, 6, 15, 18, 0), duration_minutes=15)


def test_battery_initialization_and_units():
    # 200 kWh battery, 50 kW max discharge, 0.8 initial SOC
    agent = BatteryStorageAgent(
        capacity_kwh=200.0,
        initial_soc=0.80,
        max_discharge_kw=50.0,
        max_charge_kw=50.0,
        charge_efficiency=0.95,
        discharge_efficiency=0.95,
        min_soc=0.10,
        max_soc=0.95,
    )

    assert agent.capacity_kwh == 200.0
    assert agent.stored_energy_kwh == 160.0
    assert agent.round_trip_efficiency == 0.95 * 0.95


def test_battery_available_discharge_math(sample_slot):
    # 100 kWh battery, initial SOC 0.50, operational reserve 0.20
    # Usable energy = (0.50 - 0.20) * 100 = 30 kWh
    # With 15-min slot (0.25h) and discharge efficiency 0.90:
    # Max deliverable AC power = (30 kWh * 0.90) / 0.25h = 108 kW
    # Clamped to max_discharge_kw (40 kW)
    agent = BatteryStorageAgent(
        capacity_kwh=100.0,
        initial_soc=0.50,
        max_discharge_kw=40.0,
        discharge_efficiency=0.90,
        min_soc=0.10,
        reserve_policy=BatteryReservePolicy(physical_min_soc=0.10, operational_reserve_soc=0.20),
    )

    avail = agent.available_discharge_kw(sample_slot)
    assert avail == 40.0

    offer = agent.get_offer(sample_slot)
    assert offer.source_type == SourceType.BATTERY
    assert offer.available_kw == 40.0
    assert offer.marginal_price == agent.degradation_cost_per_kwh
    assert offer.constraints["soc"] == 0.50


def test_battery_discharge_and_efficiency_losses(sample_slot):
    # 100 kWh battery, initial SOC 0.80 (80 kWh)
    # Deliver 40 kW for 15 min (0.25h) -> Delivered AC energy = 10 kWh
    # Chemistry drawn = 10 kWh / 0.90 = 11.1111 kWh
    # Efficiency loss = 1.1111 kWh
    # New stored = 80 - 11.1111 = 68.8889 kWh -> SOC = 0.688889
    agent = BatteryStorageAgent(
        capacity_kwh=100.0,
        initial_soc=0.80,
        max_discharge_kw=50.0,
        discharge_efficiency=0.90,
    )

    req = DispatchRequest("battery", sample_slot, 40.0)
    res = agent.dispatch(req)

    assert res.delivered_kw == 40.0
    assert round(agent.soc, 4) == 0.6889
    assert round(res.state["chemical_drawn_kwh"], 4) == 11.1111
    assert round(res.state["efficiency_loss_kwh"], 4) == 1.1111
    assert agent.operating_mode == BatteryOperatingMode.DISCHARGE


def test_battery_charging_physics(sample_slot):
    # 100 kWh battery, initial SOC 0.50 (50 kWh)
    # Charge 40 kW for 15 min (0.25h) -> AC energy = 10 kWh
    # Stored into chemistry = 10 kWh * 0.95 = 9.5 kWh
    # New stored = 59.5 kWh -> SOC = 0.595
    agent = BatteryStorageAgent(
        capacity_kwh=100.0,
        initial_soc=0.50,
        max_charge_kw=50.0,
        charge_efficiency=0.95,
        max_soc=0.95,
    )

    accepted = agent.charge(40.0, sample_slot)
    assert accepted == 40.0
    assert round(agent.soc, 4) == 0.5950
    assert agent.operating_mode == BatteryOperatingMode.CHARGE


def test_battery_reserve_preservation(sample_slot):
    # Battery at operational reserve (0.20)
    agent = BatteryStorageAgent(
        capacity_kwh=100.0,
        initial_soc=0.20,
        reserve_policy=BatteryReservePolicy(
            physical_min_soc=0.10,
            operational_reserve_soc=0.20,
            emergency_reserve_soc=0.35,
        ),
    )

    # Ordinary available discharge must be 0.0 kW
    assert agent.available_discharge_kw(sample_slot) == 0.0
    offer = agent.get_offer(sample_slot)
    assert offer.available_kw == 0.0

    # Dispatch request must deliver 0.0 kW
    res = agent.dispatch(DispatchRequest("battery", sample_slot, 20.0))
    assert res.delivered_kw == 0.0
    assert agent.soc == 0.20


def test_battery_emergency_reserve_release(sample_slot):
    # Battery at operational reserve (0.20), physical min is 0.10
    agent = BatteryStorageAgent(
        capacity_kwh=100.0,
        initial_soc=0.20,
        max_discharge_kw=50.0,
        discharge_efficiency=1.0,  # ideal for simple math
        reserve_policy=BatteryReservePolicy(
            physical_min_soc=0.10,
            operational_reserve_soc=0.20,
        ),
    )

    # Initially 0.0 available
    assert agent.available_discharge_kw(sample_slot) == 0.0

    # Authorize emergency release
    agent.release_emergency_reserve(True)
    # Now can discharge (0.20 - 0.10) * 100 = 10 kWh -> 40 kW for 15 min
    assert agent.available_discharge_kw(sample_slot) == 40.0


def test_battery_max_soc_clamping(sample_slot):
    # Battery near max SOC (0.94, max 0.95)
    # Headroom = 1 kWh. For 15 min (0.25h) with eta_c 1.0 -> max charge = 4 kW
    agent = BatteryStorageAgent(
        capacity_kwh=100.0,
        initial_soc=0.94,
        max_soc=0.95,
        charge_efficiency=1.0,
    )

    accepted = agent.charge(50.0, sample_slot)
    assert accepted == 4.0
    assert round(agent.soc, 4) == 0.9500


def test_battery_get_offer_idempotent(sample_slot):
    agent = BatteryStorageAgent(capacity_kwh=100.0, initial_soc=0.80)
    o1 = agent.get_offer(sample_slot)
    o2 = agent.get_offer(sample_slot)

    assert o1 == o2
    assert agent.soc == 0.80
    assert agent.state.dispatch_count == 0
