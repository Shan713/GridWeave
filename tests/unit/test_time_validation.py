"""15-minute slot grid: alignment, contiguity and regular history (audit F8)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from gridweave.agents import BuildingAgent
from gridweave.forecasting import EWMAForecaster, MovingAverageForecaster, SeasonalNaiveForecaster
from gridweave.models import (
    DemandSample,
    MisalignedTimestampError,
    MissingSlotError,
    Observation,
    TimeSlot,
    is_aligned,
)

T0 = datetime(2026, 1, 5, 18, 0)
STEP = timedelta(minutes=15)


@pytest.mark.parametrize("minute, ok", [(0, True), (15, True), (30, True), (45, True), (7, False), (59, False)])
def test_alignment_rule(minute, ok):
    assert is_aligned(T0.replace(minute=minute)) is ok


def test_seconds_break_alignment():
    assert not is_aligned(T0.replace(second=1))


def test_timeslot_rejects_off_grid_start():
    TimeSlot(T0 + STEP)
    with pytest.raises(MisalignedTimestampError):
        TimeSlot(T0 + timedelta(minutes=7))


def test_agent_rejects_off_grid_observation(hostel_spec):
    agent = BuildingAgent(hostel_spec)
    with pytest.raises(MisalignedTimestampError):
        agent.observe(Observation(hostel_spec.building_id, T0 + timedelta(minutes=7), 10.0))
    assert agent.history == ()


def test_agent_rejects_missing_slot(hostel_spec):
    agent = BuildingAgent(hostel_spec)
    agent.observe(Observation(hostel_spec.building_id, T0, 10.0))
    with pytest.raises(MissingSlotError):
        agent.observe(Observation(hostel_spec.building_id, T0 + 2 * STEP, 10.0))
    assert len(agent.history) == 1  # rejected observation left no trace


def test_agent_accepts_correct_15_minute_progression(hostel_spec):
    agent = BuildingAgent(hostel_spec)
    for i in range(8):
        agent.observe(Observation(hostel_spec.building_id, T0 + i * STEP, 10.0 + i))
    assert [s.timestamp for s in agent.history] == [T0 + i * STEP for i in range(8)]
    assert agent.next_slot() == TimeSlot(T0 + 8 * STEP)


@pytest.mark.parametrize("forecaster", [MovingAverageForecaster(2), EWMAForecaster(0.5), SeasonalNaiveForecaster(2)],
                         ids=lambda f: f.name)
def test_forecasters_reject_irregular_history(forecaster):
    gappy = [DemandSample(T0 + k * STEP, 10.0) for k in (0, 1, 2, 5)]
    with pytest.raises(MissingSlotError):
        forecaster.forecast(gappy, 1)


def test_forecaster_rejects_history_at_wrong_resolution():
    hourly = [DemandSample(T0 + timedelta(hours=k), 10.0) for k in range(4)]
    with pytest.raises(MissingSlotError):
        MovingAverageForecaster(2).forecast(hourly, 1, resolution_minutes=15)
    assert MovingAverageForecaster(2).forecast(hourly, 1).first.timestamp == T0 + timedelta(hours=4)
