"""Unit tests for the autonomous GridSupplyAgent and grid profiles."""
from datetime import datetime

import pytest

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, SourceType
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.profiles.grid import GridProfile, OutageWindow
from gridweave.utils.validation import ValidationError


@pytest.fixture
def sample_slot_offpeak():
    # 02:00 AM -> Off-peak
    return TimeSlot(datetime(2026, 6, 15, 2, 0), duration_minutes=15)


@pytest.fixture
def sample_slot_standard():
    # 10:00 AM -> Standard
    return TimeSlot(datetime(2026, 6, 15, 10, 0), duration_minutes=15)


@pytest.fixture
def sample_slot_peak():
    # 19:00 (7:00 PM) -> Peak
    return TimeSlot(datetime(2026, 6, 15, 19, 0), duration_minutes=15)


def test_grid_agent_tariffs(sample_slot_offpeak, sample_slot_standard, sample_slot_peak):
    agent = GridSupplyAgent(nominal_capacity_kw=400.0)

    # 1. Off-peak rate
    assert agent.current_tariff(sample_slot_offpeak) == 5.0
    assert not agent.tariff_schedule.is_peak(sample_slot_offpeak)

    # 2. Standard rate
    assert agent.current_tariff(sample_slot_standard) == 10.0
    assert not agent.tariff_schedule.is_peak(sample_slot_standard)

    # 3. Peak rate
    assert agent.current_tariff(sample_slot_peak) == 18.0
    assert agent.tariff_schedule.is_peak(sample_slot_peak)


def test_grid_agent_get_offer_is_pure(sample_slot_peak):
    agent = GridSupplyAgent(nominal_capacity_kw=350.0)
    offer1 = agent.get_offer(sample_slot_peak)

    assert offer1.source_id == "grid"
    assert offer1.source_type == SourceType.GRID
    assert offer1.available_kw == 350.0
    assert offer1.marginal_price == 18.0
    assert offer1.constraints["is_peak"] is True

    # Repeated calls must be idempotent and non-mutating
    offer2 = agent.get_offer(sample_slot_peak)
    assert offer1 == offer2
    assert agent.state.total_imported_kwh == 0.0
    assert agent.state.total_cost == 0.0


def test_grid_outage_window(sample_slot_standard):
    # Scheduled 50% capacity reduction between 09:00 and 12:00
    window = OutageWindow(name="maintenance", capacity_factor=0.5, start_hour=9.0, end_hour=12.0)
    profile = GridProfile(nominal_capacity_kw=400.0, outage_windows=[window])
    agent = GridSupplyAgent(profile=profile)

    assert agent.available_kw(sample_slot_standard) == 200.0
    offer = agent.get_offer(sample_slot_standard)
    assert offer.available_kw == 200.0


def test_grid_emergency_outage_and_restoration(sample_slot_standard):
    agent = GridSupplyAgent(nominal_capacity_kw=500.0)
    assert agent.available_kw(sample_slot_standard) == 500.0

    # Trigger emergency blackout
    agent.trigger_outage()
    assert agent.available_kw(sample_slot_standard) == 0.0
    offer = agent.get_offer(sample_slot_standard)
    assert offer.available_kw == 0.0
    assert offer.constraints["outage"] is True

    # Restore grid
    agent.restore_grid()
    assert agent.available_kw(sample_slot_standard) == 500.0
    assert agent.get_offer(sample_slot_standard).available_kw == 500.0


def test_grid_dispatch_execution(sample_slot_peak):
    agent = GridSupplyAgent(nominal_capacity_kw=400.0)
    req = DispatchRequest("grid", sample_slot_peak, 200.0)

    res = agent.dispatch(req)
    assert res.source_id == "grid"
    assert res.delivered_kw == 200.0
    assert res.requested_kw == 200.0
    assert res.remaining_capacity_kw == 200.0

    # 200 kW for 15 min = 50 kWh. Tariff = 18.0 $/kWh -> Cost = 900.0
    assert agent.state.total_imported_kwh == 50.0
    assert agent.state.total_cost == 900.0
    assert agent.state.peak_imported_kw == 200.0


def test_grid_dispatch_clamped_to_capacity(sample_slot_standard):
    agent = GridSupplyAgent(nominal_capacity_kw=300.0)
    # Request exceeds physical capacity
    req = DispatchRequest("grid", sample_slot_standard, 500.0)
    res = agent.dispatch(req)

    assert res.delivered_kw == 300.0
    assert res.shortfall_kw == 200.0
    assert res.remaining_capacity_kw == 0.0


def test_grid_tariff_spike_event(sample_slot_standard):
    agent = GridSupplyAgent(nominal_capacity_kw=400.0)
    assert agent.current_tariff(sample_slot_standard) == 10.0

    agent.set_tariff_multiplier(2.5)
    assert agent.current_tariff(sample_slot_standard) == 25.0
    offer = agent.get_offer(sample_slot_standard)
    assert offer.marginal_price == 25.0


def test_grid_invalid_config():
    with pytest.raises(ValidationError):
        GridSupplyAgent(nominal_capacity_kw=-100.0)

    with pytest.raises(ValidationError):
        OutageWindow(name="bad", capacity_factor=1.5, start_hour=1.0, end_hour=2.0)
