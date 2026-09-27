"""Synthetic demand: realism, configurability and reproducibility."""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean

import pytest

from gridweave.models import BuildingSpec, BuildingType
from gridweave.simulation import (
    BuildingSimulator,
    DemandGenerator,
    DemandProfile,
    derive_seed,
    generate_campus_demand,
    get_profile,
    read_demand_csv,
    total_demand,
    write_demand_csv,
)
from gridweave.utils.validation import ValidationError

MONDAY = datetime(2026, 1, 5)
SATURDAY = datetime(2026, 1, 10)


def hourly_mean(samples, hour_from, hour_to):
    return mean(s.demand_kw for s in samples if hour_from <= s.timestamp.hour < hour_to)


def test_same_seed_same_series_different_seed_different_series():
    gen = lambda seed: DemandGenerator(get_profile("hostel"), 100, seed=seed).generate(MONDAY, 96)
    assert gen(7) == gen(7)
    assert gen(7) != gen(8)


def test_derive_seed_is_stable_and_distinct():
    assert derive_seed(42, "hostel_a") == derive_seed(42, "hostel_a")
    assert derive_seed(42, "hostel_a") != derive_seed(42, "hostel_b")


def test_fifteen_minute_resolution_and_bounds():
    samples = DemandGenerator(get_profile("lab"), 150, seed=1).generate(MONDAY, 96 * 3)
    assert len(samples) == 288
    assert all(b.timestamp - a.timestamp == timedelta(minutes=15) for a, b in zip(samples, samples[1:]))
    assert all(0 <= s.demand_kw <= 150 for s in samples)


def test_hostel_evening_peak_exceeds_night():
    samples = DemandGenerator(get_profile("hostel"), 100, seed=3).generate(MONDAY, 96)
    assert hourly_mean(samples, 19, 23) > 2.5 * hourly_mean(samples, 2, 5)
    assert hourly_mean(samples, 19, 23) > hourly_mean(samples, 10, 15)  # students in class


def test_building_types_have_different_shapes():
    lab = DemandGenerator(get_profile("lab"), 100, seed=1).generate(MONDAY, 96)
    hostel = DemandGenerator(get_profile("hostel"), 100, seed=1).generate(MONDAY, 96)
    academic = DemandGenerator(get_profile("academic"), 100, seed=1).generate(MONDAY, 96)
    assert hourly_mean(lab, 10, 12) > hourly_mean(hostel, 10, 12)
    assert hourly_mean(hostel, 20, 22) > hourly_mean(academic, 20, 22)
    # labs keep always-on equipment at night; academic blocks do not
    assert hourly_mean(lab, 1, 4) > 3 * hourly_mean(academic, 1, 4)


def test_weekend_variation():
    gen = DemandGenerator(get_profile("academic"), 100, seed=1)
    weekday = gen.generate(MONDAY, 96)
    weekend = gen.generate(SATURDAY, 96)
    assert hourly_mean(weekend, 9, 16) < 0.4 * hourly_mean(weekday, 9, 16)


def test_noiseless_profile_matches_expected_demand():
    profile = get_profile("hostel").with_overrides(noise_std=0.0, spike_probability=0.0)
    gen = DemandGenerator(profile, 80, seed=1)
    for s in gen.generate(MONDAY, 96):
        assert s.demand_kw == pytest.approx(gen.expected_demand(s.timestamp), abs=1e-3)


def test_spikes_raise_demand():
    calm = get_profile("admin").with_overrides(noise_std=0.0, spike_probability=0.0)
    spiky = calm.with_overrides(spike_probability=0.3, spike_magnitude=0.3)
    a = DemandGenerator(calm, 100, seed=5).generate(MONDAY, 96)
    b = DemandGenerator(spiky, 100, seed=5).generate(MONDAY, 96)
    assert sum(y.demand_kw > x.demand_kw + 1 for x, y in zip(a, b)) > 10


def test_configurable_peak_periods():
    profile = DemandProfile("custom", 0.1, 0.9, weekday_periods=((3.0, 4.0, 1.0),), smoothing_slots=1, noise_std=0)
    samples = DemandGenerator(profile, 100, seed=0).generate(MONDAY, 96)
    peak = max(samples, key=lambda s: s.demand_kw)
    assert peak.timestamp.hour == 3 and peak.demand_kw == pytest.approx(90)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(base_fraction=0.9, peak_fraction=0.5),
        dict(weekday_periods=((5.0, 3.0, 1.0),)),
        dict(weekday_periods=((1.0, 2.0, 1.5),)),
        dict(weekday_periods=()),
        dict(noise_autocorrelation=1.0),
    ],
)
def test_invalid_profiles_rejected(kwargs):
    base = dict(name="x", base_fraction=0.1, peak_fraction=0.8, weekday_periods=((8.0, 17.0, 1.0),))
    base.update(kwargs)
    with pytest.raises(ValidationError):
        DemandProfile(**base)


def test_profile_from_dict():
    p = DemandProfile.from_dict({"name": "x", "base_fraction": 0.1, "peak_fraction": 0.5,
                                 "weekday_periods": [[8, 17, 1.0]]})
    assert p.weekday_periods == ((8.0, 17.0, 1.0),)


def test_unknown_profile():
    with pytest.raises(ValidationError):
        get_profile("stadium")


def test_campus_generation_and_csv_roundtrip(tmp_path, hostel_spec, lab_spec):
    series = generate_campus_demand(
        [(hostel_spec, get_profile("hostel")), (lab_spec, get_profile("lab"))], MONDAY, 96, seed=42
    )
    assert set(series) == {"hostel_a", "eng_lab"}
    agg = total_demand(series)
    assert agg[10].demand_kw == pytest.approx(series["hostel_a"][10].demand_kw + series["eng_lab"][10].demand_kw)
    path = write_demand_csv(tmp_path / "d.csv", series)
    assert read_demand_csv(path) == series


def test_building_simulator_steps_and_modifier(hostel_spec):
    sim = BuildingSimulator.from_profile(hostel_spec, MONDAY, 4, seed=1)
    observations = [sim.step() for _ in range(4)]
    assert [o.timestamp for o in observations] == [s.timestamp for s in sim.series]
    assert not sim.has_next
    with pytest.raises(StopIteration):
        sim.step()
    sim.reset()
    sim.demand_modifier = lambda ts, kw: kw * 10  # heat-wave event, capped at capacity
    assert sim.step().measured_demand_kw <= hostel_spec.capacity_kw


def test_every_builtin_profile_generates_valid_data():
    for btype in BuildingType:
        spec = BuildingSpec("b", "B", btype, capacity_kw=100)
        samples = BuildingSimulator.from_profile(spec, MONDAY, 96 * 7).series
        assert len(samples) == 672 and max(s.demand_kw for s in samples) > 0
