"""Typed configuration objects and the JSON campus-config loader.

Everything campus-specific (buildings, capacities, load ratios, profiles,
forecaster, priority weights, pricing, seed, resolution) lives in a JSON
file such as the packaged ``gridweave/config/campus_default.json``. Adding "Hostel D" or a
100-building campus is a config change, never a code change.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

from gridweave.bidding.bid_generator import PricingPolicy
from gridweave.bidding.priority import PriorityWeights
from gridweave.models.building import BuildingSpec, BuildingType
from gridweave.simulation.profiles import DemandProfile, get_profile
from gridweave.utils.validation import ValidationError, require_fraction

#: The default campus ships *inside* the package (package data), so it is
#: available after a plain ``pip install .`` as well as an editable install.
DEFAULT_CONFIG_RESOURCE = "campus_default.json"


def default_config_text() -> str:
    """Contents of the packaged default campus config (copy it to start your own)."""
    return resources.files("gridweave.config").joinpath(DEFAULT_CONFIG_RESOURCE).read_text()


@dataclass(frozen=True)
class SimulationSettings:
    start: datetime = datetime(2026, 1, 5)  # a Monday
    days: int = 3
    resolution_minutes: int = 15
    seed: int = 42
    rebound_fraction: float = 0.0

    def __post_init__(self) -> None:
        require_fraction("simulation.rebound_fraction", self.rebound_fraction)
        if self.days < 1:
            raise ValidationError("simulation.days must be >= 1")
        if self.resolution_minutes <= 0 or 1440 % self.resolution_minutes:
            raise ValidationError("simulation.resolution_minutes must divide 1440")

    @property
    def periods(self) -> int:
        return self.days * 1440 // self.resolution_minutes


@dataclass(frozen=True)
class ForecastSettings:
    method: str = "seasonal_ewma"
    params: Mapping[str, Any] = field(default_factory=dict)
    fallback_method: str | None = "ewma"
    fallback_params: Mapping[str, Any] = field(default_factory=lambda: {"alpha": 0.6})


@dataclass(frozen=True)
class AgentSettings:
    priority_weights: PriorityWeights = field(default_factory=PriorityWeights)
    pricing: PricingPolicy = field(default_factory=PricingPolicy)
    deprivation_alpha: float = 0.3
    history_limit: int = 96 * 7
    spike_detection: bool = True


@dataclass(frozen=True)
class BuildingConfig:
    spec: BuildingSpec
    profile: DemandProfile


@dataclass(frozen=True)
class CampusConfig:
    name: str
    buildings: tuple[BuildingConfig, ...]
    simulation: SimulationSettings = field(default_factory=SimulationSettings)
    forecast: ForecastSettings = field(default_factory=ForecastSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    supply: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ids = [b.spec.building_id for b in self.buildings]
        if not ids:
            raise ValidationError("a campus needs at least one building")
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValidationError(f"duplicate building_id(s): {sorted(duplicates)}")

    @property
    def total_capacity_kw(self) -> float:
        return sum(b.spec.capacity_kw for b in self.buildings)

    def building(self, building_id: str) -> BuildingConfig:
        for b in self.buildings:
            if b.spec.building_id == building_id:
                return b
        raise KeyError(building_id)

    def with_seed(self, seed: int) -> "CampusConfig":
        from dataclasses import replace

        return replace(self, simulation=replace(self.simulation, seed=seed))


# ------------------------------------------------------------------ parsing
_SPEC_FIELDS = set(BuildingSpec.__dataclass_fields__)


def parse_building(data: Mapping[str, Any], defaults: Mapping[str, Any] | None = None) -> BuildingConfig:
    """Parse one building entry. ``defaults`` (e.g. per-type defaults) are
    applied first and overridden by the entry itself."""
    merged = {**(defaults or {}), **data}
    if "flexible_fraction" in merged:
        flex = float(merged.pop("flexible_fraction"))
        if "critical_fraction" in merged and abs(float(merged["critical_fraction"]) + flex - 1.0) > 1e-9:
            raise ValidationError(f"{merged.get('building_id')}: critical_fraction + flexible_fraction must be 1")
        merged["critical_fraction"] = 1.0 - flex
    profile_ref = merged.pop("profile", None)
    profile_overrides = merged.pop("profile_overrides", {})
    unknown = set(merged) - _SPEC_FIELDS
    if unknown:
        raise ValidationError(f"{merged.get('building_id')}: unknown building field(s) {sorted(unknown)}")
    spec = BuildingSpec(**merged)
    if profile_ref is None:
        profile = get_profile(spec.building_type)
    elif isinstance(profile_ref, str):
        profile = get_profile(profile_ref)
    else:
        profile = DemandProfile.from_dict(profile_ref)
    if profile_overrides:
        profile = DemandProfile.from_dict({**_profile_to_dict(profile), **profile_overrides})
    return BuildingConfig(spec, profile)


def _profile_to_dict(p: DemandProfile) -> dict:
    return {k: getattr(p, k) for k in DemandProfile.__dataclass_fields__}


def campus_from_dict(data: Mapping[str, Any]) -> CampusConfig:
    sim = dict(data.get("simulation", {}))
    if "start" in sim:
        sim["start"] = datetime.fromisoformat(sim["start"])
    fc = data.get("forecast", {})
    ag = dict(data.get("agent", {}))
    if "priority_weights" in ag:
        ag["priority_weights"] = PriorityWeights(**ag["priority_weights"])
    if "pricing" in ag:
        ag["pricing"] = PricingPolicy(**ag["pricing"])
    type_defaults = data.get("building_type_defaults", {})
    buildings = []
    for entry in data["buildings"]:
        btype = BuildingType(entry.get("building_type", "other")).value
        buildings.append(parse_building(entry, type_defaults.get(btype)))
    return CampusConfig(
        name=data.get("name", "campus"),
        buildings=tuple(buildings),
        simulation=SimulationSettings(**sim),
        forecast=ForecastSettings(**fc),
        agent=AgentSettings(**ag),
        supply=dict(data.get("supply", {})),
    )


def load_campus_config(path: str | Path | None = None) -> CampusConfig:
    """Load a campus config. Resolution order: ``path`` argument, then the
    ``GRIDWEAVE_CONFIG`` environment variable, then the packaged default.
    Relative paths are resolved against the current working directory.
    ``GRIDWEAVE_SEED`` (if set) overrides the configured seed."""
    chosen = path or os.environ.get("GRIDWEAVE_CONFIG")
    if chosen:
        try:
            data = json.loads(Path(chosen).read_text())
        except FileNotFoundError as exc:
            raise ValidationError(f"campus config not found: {Path(chosen).resolve()}") from exc
    else:
        data = json.loads(default_config_text())
    cfg = campus_from_dict(data)
    if os.environ.get("GRIDWEAVE_SEED"):
        cfg = cfg.with_seed(int(os.environ["GRIDWEAVE_SEED"]))
    return cfg


# ------------------------------------------------------------ scalability
_SYNTHETIC_MIX = (
    (BuildingType.HOSTEL, 120.0, 0.35, 10.0, 0.6),
    (BuildingType.LAB, 150.0, 0.70, 35.0, 0.9),
    (BuildingType.ACADEMIC, 180.0, 0.30, 8.0, 0.7),
    (BuildingType.LIBRARY, 90.0, 0.40, 8.0, 0.5),
    (BuildingType.ADMIN, 60.0, 0.30, 5.0, 0.4),
)


def synthetic_campus(n_buildings: int, seed: int = 42, name: str | None = None) -> CampusConfig:
    """Generate an ``n``-building campus from a fixed type mix (for
    scalability demos and tests). Deterministic for a given ``n``."""
    if n_buildings < 1:
        raise ValidationError("n_buildings must be >= 1")
    buildings = []
    for i in range(n_buildings):
        btype, cap, crit, floor, importance = _SYNTHETIC_MIX[i % len(_SYNTHETIC_MIX)]
        scale = 0.8 + 0.4 * ((i * 37) % 11) / 10  # deterministic size variety 0.8x..1.2x
        spec = BuildingSpec(
            building_id=f"{btype.value}_{i:03d}",
            name=f"{btype.value.title()} {i:03d}",
            building_type=btype,
            capacity_kw=round(cap * scale, 1),
            minimum_operational_kw=round(floor * scale, 1),
            critical_fraction=crit,
            importance=importance,
        )
        buildings.append(BuildingConfig(spec, get_profile(btype)))
    return CampusConfig(
        name=name or f"synthetic-{n_buildings}",
        buildings=tuple(buildings),
        simulation=SimulationSettings(seed=seed),
    )
