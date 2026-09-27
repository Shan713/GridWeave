"""Typed configuration and the JSON campus-config loader."""
from gridweave.config.settings import (
    DEFAULT_CONFIG_PATH,
    AgentSettings,
    BuildingConfig,
    CampusConfig,
    ForecastSettings,
    SimulationSettings,
    campus_from_dict,
    load_campus_config,
    parse_building,
    synthetic_campus,
)

__all__ = [
    "AgentSettings",
    "BuildingConfig",
    "CampusConfig",
    "DEFAULT_CONFIG_PATH",
    "ForecastSettings",
    "SimulationSettings",
    "campus_from_dict",
    "load_campus_config",
    "parse_building",
    "synthetic_campus",
]
