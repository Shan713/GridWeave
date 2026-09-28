"""Regression tests for completed Person 3 lifecycle and integration boundaries."""
from datetime import datetime

import pytest

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, SourceType, SupplyOffer
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.optimization import BuildingDemandOutlookAdapter
from gridweave.supply.provider import CampusSupplyProvider


class FailingSource:
    source_id = "custom_source"

    def get_offer(self, slot):
        return SupplyOffer(self.source_id, SourceType.OTHER, slot, 10.0, 1.0)

    def dispatch(self, request):
        raise RuntimeError("simulated source failure")

    def reset(self):
        pass

    def snapshot(self):
        return {"source_id": self.source_id}


class OutlookPoint:
    def __init__(self, timestamp, predicted_demand_kw):
        self.timestamp = timestamp
        self.predicted_demand_kw = predicted_demand_kw


class OutlookProvider:
    def __init__(self, points):
        self.points = points

    def demand_outlook(self, horizon):
        return self.points[:horizon]


def test_battery_events_change_offers_and_restore():
    slot = TimeSlot(datetime(2026, 6, 15, 12), 15)
    battery = BatteryStorageAgent("storage_unit", capacity_kwh=100.0, initial_soc=0.8)
    provider = CampusSupplyProvider([], [], [battery])

    nominal = next(offer for offer in provider.offers(slot) if offer.source_id == "storage_unit").available_kw
    provider.events.trigger_battery_derate(5.0)
    assert next(offer for offer in provider.offers(slot) if offer.source_id == "storage_unit").available_kw == 5.0
    provider.events.trigger_battery_outage()
    assert next(offer for offer in provider.offers(slot) if offer.source_id == "storage_unit").available_kw == 0.0
    provider.events.trigger_battery_restoration()
    assert next(offer for offer in provider.offers(slot) if offer.source_id == "storage_unit").available_kw == 5.0
    battery.set_derated_discharge_kw(None)
    assert next(offer for offer in provider.offers(slot) if offer.source_id == "storage_unit").available_kw == nominal


def test_dispatch_failure_rolls_back_prior_source_mutation():
    slot = TimeSlot(datetime(2026, 6, 15, 12), 15)
    battery = BatteryStorageAgent("storage_unit", capacity_kwh=100.0, initial_soc=0.8)
    provider = CampusSupplyProvider([], [], [battery])
    provider.sources["custom_source"] = FailingSource()
    provider.dispatcher.sources = provider.sources
    provider.offers(slot)
    before = battery.soc

    with pytest.raises(Exception, match="rolled back"):
        provider.dispatch([
            DispatchRequest("storage_unit", slot, 10.0),
            DispatchRequest("custom_source", slot, 1.0),
        ])

    assert battery.soc == before
    assert provider.accountant.history == []


def test_accounting_uses_registered_source_type_for_custom_ids():
    slot = TimeSlot(datetime(2026, 6, 15, 12), 15)
    battery = BatteryStorageAgent("storage_unit", capacity_kwh=100.0, initial_soc=0.8)
    provider = CampusSupplyProvider([], [], [battery])
    provider.offers(slot)
    provider.dispatch([DispatchRequest("storage_unit", slot, 10.0)])
    assert provider.accountant.summary()["cumulative_battery_disch_kwh"] == 2.5


def test_explicit_empty_source_lists_do_not_register_defaults():
    provider = CampusSupplyProvider([], [], [])
    assert provider.sources == {}


def test_p1_outlook_adapter_preserves_public_forecast_contract():
    start = datetime(2026, 6, 15, 12)
    points = [OutlookPoint(start, 40.0), OutlookPoint(start.replace(minute=15), 45.0)]
    adapter = BuildingDemandOutlookAdapter(OutlookProvider(points))
    assert adapter.forecast_demand_kw(TimeSlot(start, 15), 2) == [40.0, 45.0]
