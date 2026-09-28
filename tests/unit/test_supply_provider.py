"""Unit tests for CampusSupplyProvider, SupplyDispatcher, and SupplyAccountant."""
from datetime import datetime

import pytest

from gridweave.interfaces import SupplyProvider
from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.dispatcher import DispatchExecutionError
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.provider import CampusSupplyProvider
from gridweave.supply.solar_agent import SolarEnergyAgent
from gridweave.utils.validation import ValidationError


@pytest.fixture
def noon_slot():
    return TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)


def test_campus_supply_provider_implements_protocol():
    provider = CampusSupplyProvider()
    assert isinstance(provider, SupplyProvider)


def test_campus_supply_provider_multi_source_offers(noon_slot):
    grid = GridSupplyAgent("grid_main", nominal_capacity_kw=300.0)
    solar = SolarEnergyAgent("solar_roof", installed_capacity_kw=150.0)
    battery = BatteryStorageAgent("battery_main", capacity_kwh=100.0, initial_soc=0.80)

    provider = CampusSupplyProvider([grid], [solar], [battery])
    offers = provider.offers(noon_slot)

    assert len(offers) == 3
    source_ids = {o.source_id for o in offers}
    assert source_ids == {"grid_main", "solar_roof", "battery_main"}

    # Offers must have positive capacities during noon
    for o in offers:
        assert o.available_kw > 0.0
        assert o.time_slot == noon_slot


def test_duplicate_source_rejection():
    grid1 = GridSupplyAgent("grid_main")
    grid2 = GridSupplyAgent("grid_main")  # Duplicate ID

    with pytest.raises(ValidationError):
        CampusSupplyProvider([grid1, grid2], [], [])


def test_provider_dispatch_and_accounting(noon_slot):
    grid = GridSupplyAgent("grid", nominal_capacity_kw=300.0)
    solar = SolarEnergyAgent("solar", installed_capacity_kw=100.0)
    battery = BatteryStorageAgent("battery", capacity_kwh=100.0, initial_soc=0.80)

    provider = CampusSupplyProvider([grid], [solar], [battery])
    provider.offers(noon_slot)

    # Dispatch: 50 kW from grid, 40 kW from solar, 20 kW from battery
    requests = [
        DispatchRequest("grid", noon_slot, 50.0),
        DispatchRequest("solar", noon_slot, 40.0),
        DispatchRequest("battery", noon_slot, 20.0),
    ]

    results = provider.dispatch(requests)
    assert len(results) == 3
    for r in results:
        assert r.delivered_kw == r.requested_kw

    # Energy ledger verification
    summary = provider.accountant.summary()
    assert summary["total_slots"] == 1
    # 110 kW total for 0.25h = 27.5 kWh
    assert summary["cumulative_delivered_kwh"] == 27.5
    assert summary["cumulative_grid_kwh"] == 12.5
    assert summary["cumulative_solar_del_kwh"] == 10.0
    assert summary["cumulative_battery_disch_kwh"] == 5.0


def test_dispatcher_rejects_unknown_source(noon_slot):
    provider = CampusSupplyProvider()
    provider.offers(noon_slot)

    bad_req = [DispatchRequest("unknown_source", noon_slot, 10.0)]
    with pytest.raises(DispatchExecutionError):
        provider.dispatch(bad_req)


def test_dispatcher_rejects_excess_dispatch(noon_slot):
    grid = GridSupplyAgent("grid", nominal_capacity_kw=100.0)
    provider = CampusSupplyProvider([grid], [], [])
    provider.offers(noon_slot)

    excess_req = [DispatchRequest("grid", noon_slot, 500.0)]
    with pytest.raises(DispatchExecutionError):
        provider.dispatch(excess_req)


def test_provider_rejects_duplicate_dispatch_without_mutating_state(noon_slot):
    battery = BatteryStorageAgent("battery", capacity_kwh=100.0, initial_soc=0.80)
    provider = CampusSupplyProvider([], [], [battery])
    provider.offers(noon_slot)
    request = [DispatchRequest("battery", noon_slot, 20.0)]

    provider.dispatch(request)
    soc_after_first_dispatch = battery.soc

    with pytest.raises(ValidationError, match="already settled"):
        provider.dispatch(request)

    assert battery.soc == soc_after_first_dispatch


def test_provider_rejects_stale_dispatch_without_mutation(noon_slot):
    grid = GridSupplyAgent("grid", nominal_capacity_kw=100.0)
    provider = CampusSupplyProvider([grid], [], [])
    provider.offers(noon_slot)
    stale_slot = noon_slot.next()

    with pytest.raises(ValidationError, match="stale"):
        provider.dispatch([DispatchRequest("grid", stale_slot, 10.0)])

    assert grid.state.total_imported_kwh == 0.0


def test_dynamic_event_handling(noon_slot):
    grid = GridSupplyAgent("grid", nominal_capacity_kw=400.0)
    provider = CampusSupplyProvider([grid], [], [])

    # Nominal offer
    o1 = provider.offers(noon_slot)[0]
    assert o1.available_kw == 400.0

    # Trigger blackout event
    provider.events.trigger_grid_outage()

    # Next offer must reflect outage
    o2 = provider.offers(noon_slot)[0]
    assert o2.available_kw == 0.0

    # Restore grid
    provider.events.trigger_grid_restoration()
    o3 = provider.offers(noon_slot)[0]
    assert o3.available_kw == 400.0


def test_provider_from_config():
    cfg = {
        "sources": [
            {"type": "grid", "nominal_capacity_kw": 350.0},
            {"type": "solar", "installed_capacity_kw": 120.0},
            {"type": "battery", "capacity_kwh": 80.0, "initial_soc": 0.75},
        ]
    }
    provider = CampusSupplyProvider.from_config(cfg)
    assert len(provider.sources) == 3
