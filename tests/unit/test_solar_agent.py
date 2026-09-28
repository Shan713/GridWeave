"""Unit tests for the autonomous SolarEnergyAgent and solar profiles."""
from datetime import datetime

import pytest

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, SourceType
from gridweave.supply.profiles.solar import SolarProfile
from gridweave.supply.solar_agent import SolarEnergyAgent


@pytest.fixture
def night_slot():
    # 01:00 AM -> Night
    return TimeSlot(datetime(2026, 6, 15, 1, 0), duration_minutes=15)


@pytest.fixture
def noon_slot():
    # 12:00 PM -> Solar peak
    return TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)


@pytest.fixture
def sunset_slot():
    # 20:00 (8:00 PM) -> After sunset
    return TimeSlot(datetime(2026, 6, 15, 20, 0), duration_minutes=15)


def test_solar_night_zero_generation(night_slot, sunset_slot):
    agent = SolarEnergyAgent(installed_capacity_kw=250.0)

    # 1. Midnight / Early morning
    assert agent.generation_kw(night_slot) == 0.0
    offer_night = agent.get_offer(night_slot)
    assert offer_night.available_kw == 0.0
    assert offer_night.constraints["is_daylight"] is False

    # 2. Night after sunset
    assert agent.generation_kw(sunset_slot) == 0.0
    offer_sunset = agent.get_offer(sunset_slot)
    assert offer_sunset.available_kw == 0.0


def test_solar_daylight_generation(noon_slot):
    agent = SolarEnergyAgent(installed_capacity_kw=200.0)
    gen = agent.generation_kw(noon_slot)

    # Noon generation must be positive and bounded by capacity
    assert gen > 50.0
    assert gen <= 200.0

    offer = agent.get_offer(noon_slot)
    assert offer.source_type == SourceType.SOLAR
    assert offer.available_kw == round(gen, 4)
    assert offer.marginal_price == 0.0
    assert offer.constraints["is_daylight"] is True


def test_solar_cloud_cover_attenuation(noon_slot):
    profile_clear = SolarProfile(installed_capacity_kw=200.0, cloud_cover=0.0)
    profile_cloudy = SolarProfile(installed_capacity_kw=200.0, cloud_cover=0.8)

    agent_clear = SolarEnergyAgent(profile=profile_clear)
    agent_cloudy = SolarEnergyAgent(profile=profile_cloudy)

    gen_clear = agent_clear.generation_kw(noon_slot)
    gen_cloudy = agent_cloudy.generation_kw(noon_slot)

    assert gen_cloudy < gen_clear
    assert gen_cloudy > 0.0  # Diffuse radiation still generates some power


def test_solar_dispatch_and_curtailment(noon_slot):
    agent = SolarEnergyAgent(installed_capacity_kw=200.0)
    gen_kw = agent.generation_kw(noon_slot)

    # Auction requests only 30 kW out of ~140 kW generated
    req = DispatchRequest("solar", noon_slot, 30.0)
    res = agent.dispatch(req)

    assert res.delivered_kw == 30.0
    assert res.remaining_capacity_kw == round(gen_kw - 30.0, 4)
    assert res.state["curtailed_kw"] == round(gen_kw - 30.0, 4)

    # Telemetry ledger check
    assert agent.state.total_delivered_kwh == 30.0 * noon_slot.hours
    assert agent.state.total_curtailed_kwh > 0.0


def test_solar_dispatch_above_generation_clamped(noon_slot):
    agent = SolarEnergyAgent(installed_capacity_kw=100.0)
    gen_kw = agent.generation_kw(noon_slot)

    # Request exceeds actual generation
    req = DispatchRequest("solar", noon_slot, 500.0)
    res = agent.dispatch(req)

    assert res.delivered_kw == round(gen_kw, 4)
    assert res.shortfall_kw == round(500.0 - gen_kw, 4)


def test_solar_dynamic_cloud_event(noon_slot):
    agent = SolarEnergyAgent(installed_capacity_kw=200.0)
    initial_gen = agent.generation_kw(noon_slot)

    # Sudden thunderstorm
    agent.set_cloud_cover(0.9)
    storm_gen = agent.generation_kw(noon_slot)
    assert storm_gen < initial_gen * 0.5


def test_solar_idempotent_get_offer(noon_slot):
    agent = SolarEnergyAgent(installed_capacity_kw=200.0)
    o1 = agent.get_offer(noon_slot)
    o2 = agent.get_offer(noon_slot)

    assert o1 == o2
    assert agent.state.total_delivered_kwh == 0.0
    assert agent.state.dispatch_count == 0
