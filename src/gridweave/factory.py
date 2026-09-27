"""Assemble agents and simulators from a :class:`CampusConfig`.

This is the only place that knows how configuration maps onto concrete
classes, so agents themselves stay free of config-parsing concerns.
"""
from __future__ import annotations

from gridweave.agents.building_agent import BuildingAgent
from gridweave.bidding.bid_generator import BidGenerator
from gridweave.bidding.priority import PriorityModel
from gridweave.config.settings import BuildingConfig, CampusConfig, ForecastSettings
from gridweave.forecasting.anomaly import SpikeDetector
from gridweave.forecasting.base_forecaster import BaseForecaster
from gridweave.forecasting.composite import FallbackForecaster
from gridweave.forecasting.registry import create_forecaster
from gridweave.simulation.building_simulator import BuildingSimulator
from gridweave.simulation.demand_generator import DemandGenerator, derive_seed


def build_forecaster(settings: ForecastSettings) -> BaseForecaster:
    primary = create_forecaster(settings.method, **dict(settings.params))
    if settings.fallback_method:
        return FallbackForecaster(primary, create_forecaster(settings.fallback_method, **dict(settings.fallback_params)))
    return primary


def build_agent(building: BuildingConfig, campus: CampusConfig) -> BuildingAgent:
    a = campus.agent
    return BuildingAgent(
        building.spec,
        forecaster=build_forecaster(campus.forecast),
        priority_model=PriorityModel(a.priority_weights),
        bid_generator=BidGenerator(a.pricing),
        resolution_minutes=campus.simulation.resolution_minutes,
        history_limit=a.history_limit,
        deprivation_alpha=a.deprivation_alpha,
        spike_detector=SpikeDetector() if a.spike_detection else None,
    )


def build_agents(campus: CampusConfig) -> dict[str, BuildingAgent]:
    return {b.spec.building_id: build_agent(b, campus) for b in campus.buildings}


def build_simulators(campus: CampusConfig, periods: int | None = None) -> dict[str, BuildingSimulator]:
    sim = campus.simulation
    periods = sim.periods if periods is None else periods
    out = {}
    for b in campus.buildings:
        gen = DemandGenerator(b.profile, b.spec.capacity_kw, derive_seed(sim.seed, b.spec.building_id),
                              sim.resolution_minutes)
        out[b.spec.building_id] = BuildingSimulator(b.spec, gen.generate(sim.start, periods))
    return out
