"""Unit tests for the P2 BidScorer and multi-criteria evaluation function."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.auction.scoring import BidScorer
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot


@pytest.fixture
def slot():
    return TimeSlot(datetime(2026, 1, 5, 12, 0))


@pytest.fixture
def sample_bid(slot):
    return Bid(
        bid_id="lab_a:20260105T1200:r0",
        building_id="lab_a",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 11, 45),
        requested_power_kw=50.0,
        minimum_power_kw=35.0,
        critical_power_kw=30.0,
        flexible_power_kw=20.0,
        priority_score=0.80,
        flexibility_score=0.30,
        willingness_to_pay=10.0,
        maximum_price=12.5,
    )


def test_scorer_breakdown_components(sample_bid):
    scorer = BidScorer(
        weight_criticality=0.35,
        weight_priority=0.30,
        weight_wtp=0.20,
        weight_fairness=0.15,
        weight_flexibility=0.05,
    )
    breakdown = scorer.compute_score(sample_bid, deprivation_boost=0.0)

    # Criticality ratio: 30 / 50 = 0.60 -> comp = 0.35 * 0.60 = 0.210
    assert abs(breakdown.criticality_component - 0.210) < 1e-4

    # Priority: 0.80 -> comp = 0.30 * 0.80 = 0.240
    assert abs(breakdown.priority_component - 0.240) < 1e-4

    # WTP: 10 / 12.5 = 0.80 -> comp = 0.20 * 0.80 = 0.160
    assert abs(breakdown.wtp_component - 0.160) < 1e-4

    # Flexibility discount: 0.05 * 0.30 = 0.015
    assert abs(breakdown.flexibility_discount - 0.015) < 1e-4

    # Deprivation: 0.0
    assert breakdown.deprivation_component == 0.0

    # Composite: 0.210 + 0.240 + 0.160 + 0.0 - 0.015 = 0.595
    assert abs(breakdown.composite_score - 0.595) < 1e-4


def test_scorer_deprivation_boost(sample_bid):
    scorer = BidScorer(weight_fairness=0.15)
    base_breakdown = scorer.compute_score(sample_bid, deprivation_boost=0.0)
    boosted_breakdown = scorer.compute_score(sample_bid, deprivation_boost=1.0)

    # Boosted composite score should be higher by exactly weight_fairness * 1.0 = 0.15
    diff = boosted_breakdown.composite_score - base_breakdown.composite_score
    assert abs(diff - 0.15) < 1e-4
    assert boosted_breakdown.deprivation_component == 0.15


def test_scorer_batch_scoring(sample_bid, slot):
    bid2 = Bid(
        bid_id="hostel_b:20260105T1200:r0",
        building_id="hostel_b",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 11, 45),
        requested_power_kw=20.0,
        minimum_power_kw=10.0,
        critical_power_kw=5.0,
        flexible_power_kw=15.0,
        priority_score=0.40,
        flexibility_score=0.50,
        willingness_to_pay=6.0,
        maximum_price=10.0,
    )
    scorer = BidScorer()
    dep_map = {"hostel_b": 0.5}
    scores = scorer.score_bids([sample_bid, bid2], deprivation_factors=dep_map)

    assert "lab_a:20260105T1200:r0" in scores
    assert "hostel_b:20260105T1200:r0" in scores
    assert scores["lab_a:20260105T1200:r0"].composite_score > scores["hostel_b:20260105T1200:r0"].composite_score
