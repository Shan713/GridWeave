"""Property-based and physical invariant tests for the supply subsystem."""
import random
from datetime import datetime

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.profiles.solar import SolarProfile
from gridweave.supply.provider import CampusSupplyProvider
from gridweave.supply.solar_agent import SolarEnergyAgent


def test_invariant_solar_bounds_24_hours():
    """Verify that solar generation is strictly bounded in [0, capacity] and 0 at night across all 96 slots."""
    profile = SolarProfile(installed_capacity_kw=150.0, seed=123)
    start_dt = datetime(2026, 6, 15, 0, 0)
    slot = TimeSlot(start_dt, duration_minutes=15)

    for i in range(96):
        gen = profile.generation_kw(slot)
        # Invariant 1: Non-negative and capped by capacity
        assert 0.0 <= gen <= 150.0

        # Invariant 2: Night generation must be zero
        hour = slot.start.hour + slot.start.minute / 60.0
        if hour < 5.5 or hour > 19.0:
            assert gen == 0.0

        slot = slot.next()


def test_invariant_battery_soc_bounds_random_walk():
    """Verify that battery SOC never violates [min_soc, max_soc] under extreme randomized charge/discharge."""
    battery = BatteryStorageAgent(
        capacity_kwh=100.0,
        initial_soc=0.50,
        max_discharge_kw=40.0,
        max_charge_kw=40.0,
        min_soc=0.10,
        max_soc=0.95,
    )

    rng = random.Random(999)
    slot = TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)

    for _ in range(200):
        action_type = rng.choice(["discharge", "charge", "idle"])
        power = rng.uniform(0.0, 100.0)

        if action_type == "discharge":
            req = DispatchRequest("battery", slot, power)
            res = battery.dispatch(req)
            assert res.delivered_kw <= req.requested_kw
        elif action_type == "charge":
            battery.charge(power, slot)

        # Invariant: min_soc <= soc <= max_soc
        assert battery.min_soc <= round(battery.soc, 6) <= battery.max_soc
        slot = slot.next()


def test_invariant_repeated_offers_no_side_effects():
    """Verify that calling offers() repeatedly does not mutate physical source state or accounting."""
    grid = GridSupplyAgent("grid", nominal_capacity_kw=300.0)
    solar = SolarEnergyAgent("solar", installed_capacity_kw=100.0)
    battery = BatteryStorageAgent("battery", capacity_kwh=100.0, initial_soc=0.80)

    provider = CampusSupplyProvider([grid], [solar], [battery])
    slot = TimeSlot(datetime(2026, 6, 15, 12, 0), duration_minutes=15)

    initial_soc = battery.soc
    for _ in range(50):
        provider.offers(slot)

    assert battery.soc == initial_soc
    assert grid.state.total_imported_kwh == 0.0
    assert solar.state.total_delivered_kwh == 0.0
    assert provider.accountant.cumulative_delivered_kwh == 0.0


def test_invariant_energy_conservation_over_day():
    """Verify energy conservation sum(delivered) == sum(dispatches) across an entire 24h simulation."""
    grid = GridSupplyAgent("grid", nominal_capacity_kw=300.0)
    solar = SolarEnergyAgent("solar", installed_capacity_kw=100.0)
    battery = BatteryStorageAgent("battery", capacity_kwh=100.0, initial_soc=0.80)

    provider = CampusSupplyProvider([grid], [solar], [battery])
    slot = TimeSlot(datetime(2026, 6, 15, 0, 0), duration_minutes=15)

    rng = random.Random(42)
    for _ in range(96):
        offers = provider.offers(slot)
        offer_by_id = {o.source_id: o.available_kw for o in offers}

        # Request random fractions of available supply
        requests = [
            DispatchRequest("grid", slot, rng.uniform(0.0, min(50.0, offer_by_id["grid"]))),
            DispatchRequest("solar", slot, rng.uniform(0.0, offer_by_id["solar"])),
            DispatchRequest("battery", slot, rng.uniform(0.0, offer_by_id["battery"])),
        ]

        provider.dispatch(requests)
        slot = slot.next()

    # Audit final accountant ledger
    summary = provider.accountant.summary()
    assert summary["total_slots"] == 96
    expected_delivered = (
        summary["cumulative_grid_kwh"]
        + summary["cumulative_solar_del_kwh"]
        + summary["cumulative_battery_disch_kwh"]
    )
    assert abs(summary["cumulative_delivered_kwh"] - expected_delivered) < 0.001
