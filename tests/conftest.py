"""Shared fixtures. All randomness in the test-suite is seeded."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.models import BuildingSpec, BuildingType, TimeSlot

T0 = datetime(2026, 1, 5, 0, 0)  # a Monday


@pytest.fixture
def t0() -> datetime:
    return T0


@pytest.fixture
def slot() -> TimeSlot:
    return TimeSlot(datetime(2026, 1, 5, 18, 0), 15)


@pytest.fixture
def hostel_spec() -> BuildingSpec:
    return BuildingSpec(
        building_id="hostel_a",
        name="Hostel A",
        building_type=BuildingType.HOSTEL,
        capacity_kw=120.0,
        minimum_operational_kw=10.0,
        critical_fraction=0.4,
        min_flexible_fraction=0.0,
        deferrable_fraction=1.0,
        importance=0.6,
        forecast_horizon=4,
    )


@pytest.fixture
def lab_spec() -> BuildingSpec:
    return BuildingSpec(
        building_id="eng_lab",
        name="Engineering Lab",
        building_type=BuildingType.LAB,
        capacity_kw=150.0,
        minimum_operational_kw=35.0,
        critical_fraction=0.7,
        min_flexible_fraction=0.2,
        deferrable_fraction=0.5,
        importance=0.9,
    )
