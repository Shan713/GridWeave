"""Configuration loading, validation, factory and scalability."""
from __future__ import annotations

import json

import pytest

from gridweave.agents import BuildingAgent
from gridweave.config import campus_from_dict, load_campus_config, parse_building, synthetic_campus
from gridweave.factory import build_agents, build_forecaster, build_simulators
from gridweave.config import ForecastSettings
from gridweave.forecasting import EWMAForecaster, FallbackForecaster
from gridweave.models import BuildingType
from gridweave.utils.validation import ValidationError


def test_default_config_loads():
    cfg = load_campus_config()
    ids = [b.spec.building_id for b in cfg.buildings]
    assert ids == ["hostel_a", "hostel_b", "hostel_c", "eng_lab", "academic_block"]
    lab = cfg.building("eng_lab").spec
    assert lab.building_type is BuildingType.LAB and lab.critical_fraction == 0.7  # from type defaults
    assert cfg.building("hostel_c").profile.noise_std == 0.08                         # profile override
    assert cfg.simulation.periods == 288


def test_env_overrides(monkeypatch, tmp_path):
    data = json.loads(load_campus_config.__globals__["DEFAULT_CONFIG_PATH"].read_text())
    data["buildings"] = data["buildings"][:1]
    path = tmp_path / "c.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("GRIDWEAVE_CONFIG", str(path))
    monkeypatch.setenv("GRIDWEAVE_SEED", "7")
    cfg = load_campus_config()
    assert len(cfg.buildings) == 1 and cfg.simulation.seed == 7


def test_adding_a_building_is_config_only():
    cfg = campus_from_dict({"buildings": [
        {"building_id": "hostel_d", "name": "Hostel D", "building_type": "hostel", "capacity_kw": 90},
        {"building_id": "library", "name": "Library", "building_type": "library", "capacity_kw": 80},
        {"building_id": "lab_2", "name": "Lab 2", "building_type": "lab", "capacity_kw": 60,
         "profile": {"name": "night_lab", "base_fraction": 0.3, "peak_fraction": 0.9,
                     "weekday_periods": [[20, 24, 1.0]]}},
    ]})
    agents = build_agents(cfg)
    assert set(agents) == {"hostel_d", "library", "lab_2"}
    assert all(type(a) is BuildingAgent for a in agents.values())
    assert cfg.building("lab_2").profile.name == "night_lab"


def test_flexible_fraction_alias_and_consistency():
    b = parse_building({"building_id": "x", "name": "X", "building_type": "lab", "capacity_kw": 10,
                        "flexible_fraction": 0.4})
    assert b.spec.critical_fraction == pytest.approx(0.6)
    with pytest.raises(ValidationError):
        parse_building({"building_id": "x", "name": "X", "building_type": "lab", "capacity_kw": 10,
                        "critical_fraction": 0.5, "flexible_fraction": 0.4})


def test_unknown_fields_and_duplicates_rejected():
    with pytest.raises(ValidationError):
        parse_building({"building_id": "x", "name": "X", "building_type": "lab", "capacity_kw": 10, "colour": "red"})
    entry = {"building_id": "x", "name": "X", "building_type": "lab", "capacity_kw": 10}
    with pytest.raises(ValidationError):
        campus_from_dict({"buildings": [entry, entry]})
    with pytest.raises(ValidationError):
        campus_from_dict({"buildings": []})


def test_missing_config_file():
    with pytest.raises(ValidationError):
        load_campus_config("/nonexistent/campus.json")


def test_forecaster_factory():
    assert isinstance(build_forecaster(ForecastSettings()), FallbackForecaster)
    assert isinstance(build_forecaster(ForecastSettings("ewma", {"alpha": 0.2}, None)), EWMAForecaster)


@pytest.mark.parametrize("n", [3, 10, 50, 100])
def test_synthetic_campus_scales(n):
    cfg = synthetic_campus(n)
    assert len(cfg.buildings) == n and len({b.spec.building_id for b in cfg.buildings}) == n
    sims = build_simulators(cfg, periods=4)
    assert len(sims) == n and all(len(s.series) == 4 for s in sims.values())
