"""Typed configuration and the JSON campus-config loader."""
from gridweave.config.settings import (
    DEFAULT_CONFIG_RESOURCE,
    AgentSettings,
    BuildingConfig,
    CampusConfig,
    ForecastSettings,
    SimulationSettings,
    campus_from_dict,
    default_config_text,
    load_campus_config,
    parse_building,
    synthetic_campus,
)

__all__ = [
    "AgentSettings",
    "BuildingConfig",
    "CampusConfig",
    "DEFAULT_CONFIG_RESOURCE",
    "ForecastSettings",
    "SimulationSettings",
    "campus_from_dict",
    "default_config_text",
    "load_campus_config",
    "parse_building",
    "synthetic_campus",
]
