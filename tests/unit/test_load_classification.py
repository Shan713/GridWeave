"""Critical / flexible load split, including boundary values."""
from __future__ import annotations

import random

import pytest

from gridweave.classification import LoadClassifier
from gridweave.models import BuildingSpec, BuildingType
from gridweave.utils.validation import ValidationError


def spec(**kw):
    base = dict(building_id="b", name="B", building_type=BuildingType.HOSTEL, capacity_kw=100.0)
    base.update(kw)
    return BuildingSpec(**base)


def test_configurable_fraction_split():
    c = LoadClassifier(spec(critical_fraction=0.6)).classify(50.0)
    assert (c.total_kw, c.critical_kw, c.flexible_kw, c.minimum_kw) == pytest.approx((50, 30, 20, 30))


def test_minimum_operational_floor_dominates_small_fraction():
    c = LoadClassifier(spec(critical_fraction=0.1, minimum_operational_kw=25)).classify(40.0)
    assert c.critical_kw == 25 and c.flexible_kw == 15


def test_critical_never_exceeds_demand_even_below_operational_floor():
    c = LoadClassifier(spec(minimum_operational_kw=25)).classify(10.0)
    assert c.critical_kw == 10 and c.flexible_kw == 0


def test_comfort_floor_raises_minimum_above_critical():
    c = LoadClassifier(spec(critical_fraction=0.5, min_flexible_fraction=0.25)).classify(40.0)
    assert c.critical_kw == 20 and c.minimum_kw == pytest.approx(25)


@pytest.mark.parametrize("fraction, expected_critical", [(0.0, 0.0), (1.0, 40.0)])
def test_boundary_fractions(fraction, expected_critical):
    c = LoadClassifier(spec(critical_fraction=fraction)).classify(40.0)
    assert c.critical_kw == expected_critical
    assert c.flexible_kw == 40.0 - expected_critical


def test_zero_demand():
    c = LoadClassifier(spec(minimum_operational_kw=10)).classify(0.0)
    assert (c.total_kw, c.critical_kw, c.flexible_kw, c.minimum_kw) == (0, 0, 0, 0)
    assert c.flexibility_ratio == 0


def test_demand_is_capped_at_capacity():
    c = LoadClassifier(spec(capacity_kw=100, critical_fraction=0.5)).classify(130.0)
    assert c.total_kw == 100 and c.critical_kw == 50


def test_backlog_is_always_flexible_and_capped():
    clf = LoadClassifier(spec(capacity_kw=100, critical_fraction=0.5, min_flexible_fraction=0.5))
    c = clf.classify(60.0, backlog_kw=30.0)
    assert c.total_kw == 90 and c.critical_kw == 30 and c.flexible_kw == 60
    assert c.minimum_kw == pytest.approx(30 + 0.5 * 30)  # comfort floor ignores backlog
    assert clf.classify(90.0, backlog_kw=30.0).total_kw == 100
    assert clf.backlog_included(90.0, 30.0) == 10


def test_negative_inputs_rejected():
    with pytest.raises(ValidationError):
        LoadClassifier(spec()).classify(-1.0)
    with pytest.raises(ValidationError):
        LoadClassifier(spec()).classify(10.0, backlog_kw=-1)


def test_invariants_hold_for_random_configurations():
    rng = random.Random(2026)
    for _ in range(500):
        cap = rng.uniform(1, 500)
        s = spec(capacity_kw=cap, minimum_operational_kw=rng.uniform(0, cap), critical_fraction=rng.random(),
                 min_flexible_fraction=rng.random())
        c = LoadClassifier(s).classify(rng.uniform(0, 1.5 * cap), rng.uniform(0, cap))
        assert 0 <= c.critical_kw <= c.minimum_kw <= c.total_kw <= cap + 1e-9
        assert c.critical_kw + c.flexible_kw == pytest.approx(c.total_kw)
