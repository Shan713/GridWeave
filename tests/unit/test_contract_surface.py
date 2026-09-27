"""Frozen P1 contract surface (docs/P1_FREEZE.md). Fails fast if the public contract breaks."""
from __future__ import annotations

import dataclasses
import inspect

import gridweave.models as models
from gridweave.agents import BuildingAgent
from gridweave.contracts import ContractViolation, validate_clearing, validate_dispatch
from gridweave.interfaces import CONTRACT_VERSION, Auctioneer, DemandAgent, EnvironmentStream, SupplyProvider
from gridweave.mocks import MockAuctioneer, MockSupply
from gridweave.models.bid import BID_SCHEMA_VERSION
from gridweave.simulation import BuildingSimulator

FROZEN_MODELS = ["Bid", "Allocation", "Settlement", "Observation", "DemandState", "LoadClassification", "BidContext",
                 "SupplyOffer", "DispatchRequest", "DispatchResult", "ClearingResult", "TimeSlot", "DeferredEnergy"]

PROTOCOL_METHODS = {
    DemandAgent: {"observe": ["observation"], "generate_bid": ["context"], "abort_bid": ["reason"],
                  "settle": ["allocation", "realised"], "snapshot": []},
    Auctioneer: {"clear": ["time_slot", "bids", "offers"]},
    SupplyProvider: {"offers": ["time_slot"], "dispatch": ["requests"]},
    EnvironmentStream: {"step": [], "apply_settlement": ["settlement"]},
}


def params(fn):
    return [p for p in inspect.signature(fn).parameters if p != "self"]


def test_versions_are_frozen():
    assert CONTRACT_VERSION == "2.0"
    assert BID_SCHEMA_VERSION == "1.1"


def test_frozen_models_are_exported_immutable_dataclasses():
    for name in FROZEN_MODELS:
        cls = getattr(models, name)
        assert dataclasses.is_dataclass(cls) and cls.__dataclass_params__.frozen, name


def test_bid_fields_are_stable():
    fields = {f.name for f in dataclasses.fields(models.Bid)}
    assert {"bid_id", "building_id", "time_slot", "created_at", "requested_power_kw", "minimum_power_kw",
            "critical_power_kw", "flexible_power_kw", "priority_score", "flexibility_score", "willingness_to_pay",
            "maximum_price", "revision", "capacity_kw", "voluntary_reduction_kw", "explanation",
            "schema_version"} <= fields


def test_protocol_signatures_are_stable():
    for protocol, methods in PROTOCOL_METHODS.items():
        for method, expected in methods.items():
            assert params(getattr(protocol, method)) == expected, (protocol.__name__, method)


def test_reference_implementations_satisfy_the_protocols(hostel_spec):
    agent = BuildingAgent(hostel_spec)
    assert isinstance(agent, DemandAgent)
    assert isinstance(MockAuctioneer(), Auctioneer)
    assert isinstance(MockSupply(), SupplyProvider)
    assert isinstance(BuildingSimulator(hostel_spec, []), EnvironmentStream)
    for method, expected in PROTOCOL_METHODS[DemandAgent].items():
        assert params(getattr(agent, method)) == expected, method
    for method in ("receive_allocation", "demand_outlook", "next_slot", "update_state", "energy_balance_kwh"):
        assert callable(getattr(agent, method))


def test_contract_checks_are_public():
    assert params(validate_clearing) == ["result", "bids", "offers"]
    assert params(validate_dispatch) == ["requests", "results"]
    assert issubclass(ContractViolation, ValueError)
