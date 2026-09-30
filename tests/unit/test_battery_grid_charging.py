"""Off-peak grid charging: the battery must recover after it is discharged.

Before this fix the battery could only charge from surplus solar, which never
occurs on this campus (the market always uses all solar), so it emptied once
and offered 0 kW for the rest of every run.
"""
from datetime import datetime, timedelta

import pytest

from gridweave.auction import AuctionEngine
from gridweave.config import synthetic_campus
from gridweave.coordinator import Coordinator, get_scenario
from gridweave.factory import build_agents, build_simulators
from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.provider import CampusSupplyProvider
from gridweave.utils.validation import ValidationError

NIGHT = TimeSlot(datetime(2026, 1, 6, 2, 0))   # off-peak (00:00-06:00)
NOON = TimeSlot(datetime(2026, 1, 6, 12, 0))


def provider(grid_kw=300.0, soc=0.2, **kw):
    grid = GridSupplyAgent("grid", nominal_capacity_kw=grid_kw)
    battery = BatteryStorageAgent("battery", capacity_kwh=100.0, initial_soc=soc, max_charge_kw=40.0)
    return CampusSupplyProvider([grid], [], [battery], **kw), grid, battery


def run_slot(p, slot, grid_request_kw, battery_request_kw=0.0):
    p.offers(slot)
    reqs = [DispatchRequest("grid", slot, grid_request_kw)]
    if battery_request_kw:
        reqs.append(DispatchRequest("battery", slot, battery_request_kw))
    return p.dispatch(reqs)


def test_battery_charges_from_grid_off_peak_and_is_accounted():
    p, grid, battery = provider()
    run_slot(p, NIGHT, grid_request_kw=100.0)
    assert battery.soc == pytest.approx(0.2 + 40.0 * 0.25 * 0.95 / 100.0)   # 40 kW x 15 min x eta_c
    rec = p.accountant.history[-1]
    assert rec.grid_imported_kwh == pytest.approx(25.0)                   # campus delivery unchanged
    assert rec.grid_to_battery_kwh == pytest.approx(10.0)                 # storage import tracked separately
    assert rec.battery_charged_kwh == pytest.approx(10.0)
    assert rec.grid_charging_cost == pytest.approx(10.0 * grid.current_tariff(NIGHT))
    assert rec.total_supply_cost == pytest.approx(rec.grid_cost + rec.grid_charging_cost)
    assert grid.state.total_imported_kwh == pytest.approx(35.0)            # physical import includes charging
    summary = p.accountant.summary()
    assert summary["cumulative_grid_to_batt_kwh"] == pytest.approx(10.0)
    assert p.metrics().grid_to_battery_kwh == pytest.approx(10.0)


def test_charging_only_uses_spare_grid_capacity():
    p, grid, battery = provider(grid_kw=110.0)
    run_slot(p, NIGHT, grid_request_kw=100.0)
    assert p.accountant.history[-1].grid_to_battery_kwh == pytest.approx(10.0 * 0.25)
    assert grid.state.peak_imported_kw == pytest.approx(110.0)            # never above the grid limit


def test_no_grid_charging_outside_the_window_or_when_disabled():
    p, _, battery = provider()
    run_slot(p, NOON, grid_request_kw=100.0)
    assert battery.soc == pytest.approx(0.2)
    p2, _, battery2 = provider(grid_charge_off_peak=False)
    run_slot(p2, NIGHT, grid_request_kw=100.0)
    assert battery2.soc == pytest.approx(0.2)


def test_battery_that_discharged_this_slot_is_not_charged():
    p, _, battery = provider(soc=0.8)
    run_slot(p, NIGHT, grid_request_kw=50.0, battery_request_kw=20.0)
    assert battery.soc < 0.8
    assert p.accountant.history[-1].grid_to_battery_kwh == 0.0


def test_no_charging_while_battery_is_offline_or_grid_is_out():
    p, grid, battery = provider()
    battery.set_unavailable(True)
    run_slot(p, NIGHT, grid_request_kw=10.0)
    assert battery.soc == pytest.approx(0.2)
    p2, grid2, battery2 = provider()
    grid2.trigger_outage()
    run_slot(p2, NIGHT, grid_request_kw=0.0)
    assert battery2.soc == pytest.approx(0.2)


def test_charging_stops_at_max_soc():
    p, _, battery = provider(soc=0.94)
    slot = NIGHT
    for _ in range(8):
        run_slot(p, slot, grid_request_kw=10.0)
        slot = TimeSlot(slot.start + timedelta(minutes=15))
    assert battery.soc == pytest.approx(battery.max_soc)


def test_invalid_charge_window_rejected():
    with pytest.raises(ValidationError):
        CampusSupplyProvider([], [], [], grid_charge_hours=(6.0, 2.0))


def test_config_keys_are_respected():
    p = CampusSupplyProvider.from_config({"sources": [{"type": "grid"}], "grid_charge_off_peak": False,
                                         "grid_charge_hours": [1, 5]})
    assert p.grid_charge_off_peak is False and p.grid_charge_hours == (1.0, 5.0)


def _run(scenario_name):
    sc = get_scenario(scenario_name)
    cfg = synthetic_campus(5, seed=sc.seed)
    supply = CampusSupplyProvider.from_config(sc.supply_config)
    result = Coordinator(build_agents(cfg), build_simulators(cfg), AuctionEngine(), supply, scenario=sc).run()
    return result, supply


def test_battery_recovers_every_night_in_a_full_run():
    """End to end: the battery is refilled overnight and used again on later days."""
    result, supply = _run("normal")
    battery = supply.battery_agents[0]
    socs = {s.slot_index: next(o.constraints["soc"] for o in s.offers if o.source_type.value == "battery")
            for s in result.slots}
    days_used = {s.time_slot.start.date() for s in result.slots
                 for d in s.dispatch_results if d.source_id == battery.source_id and d.delivered_kw > 0}
    assert len(days_used) >= 3                        # before the fix: day 1 only
    assert max(socs[i] for i in range(100, 130)) > 0.9  # refilled during the night of day 2
    assert result.supply_metrics["grid_to_battery_kwh"] > 0


def test_battery_outage_scenario_now_changes_battery_use():
    """Before the fix battery_outage was identical to normal, because the battery was already empty."""
    normal, _ = _run("normal")
    outage, _ = _run("battery_outage")
    assert outage.supply_metrics["battery_discharge_kwh"] < normal.supply_metrics["battery_discharge_kwh"]
