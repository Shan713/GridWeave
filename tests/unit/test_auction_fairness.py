"""Unit tests for P2 FairnessTracker and Jain's Fairness Index."""
from __future__ import annotations

import pytest

from gridweave.auction.fairness import FairnessTracker, jains_fairness_index


def test_jains_fairness_index():
    # Equal allocation satisfaction
    assert abs(jains_fairness_index([0.8, 0.8, 0.8, 0.8]) - 1.0) < 1e-4

    # Perfect fairness at 100%
    assert abs(jains_fairness_index([1.0, 1.0, 1.0]) - 1.0) < 1e-4

    # Single winner out of 4 (worst-case inequality)
    assert abs(jains_fairness_index([1.0, 0.0, 0.0, 0.0]) - 0.25) < 1e-4

    # Two equal winners out of 4
    assert abs(jains_fairness_index([1.0, 1.0, 0.0, 0.0]) - 0.50) < 1e-4

    # Empty list or all zeroes
    assert jains_fairness_index([]) == 1.0
    assert jains_fairness_index([0.0, 0.0]) == 1.0


def test_fairness_tracker_starvation_and_deprivation():
    tracker = FairnessTracker(starvation_threshold=0.50, max_boost_slots=4)

    # Building A gets 100% service (not starved)
    tracker.record_slot("building_a", requested_kw=40.0, allocated_kw=40.0)
    assert tracker.get_record("building_a").consecutive_starved_slots == 0
    assert tracker.get_deprivation_boost("building_a") == 0.0

    # Building B gets only 20% service (starved: 8 / 40 < 0.50)
    tracker.record_slot("building_b", requested_kw=40.0, allocated_kw=8.0)
    assert tracker.get_record("building_b").consecutive_starved_slots == 1
    assert tracker.get_deprivation_boost("building_b") == 0.25  # 1 / 4

    # Second starved slot for Building B
    tracker.record_slot("building_b", requested_kw=40.0, allocated_kw=5.0)
    assert tracker.get_record("building_b").consecutive_starved_slots == 2
    assert tracker.get_deprivation_boost("building_b") == 0.50  # 2 / 4

    # Third starved slot for Building B
    tracker.record_slot("building_b", requested_kw=40.0, allocated_kw=0.0)
    assert tracker.get_record("building_b").consecutive_starved_slots == 3
    assert tracker.get_deprivation_boost("building_b") == 0.75  # 3 / 4

    # Building B detected as starving
    starving = tracker.detect_starving_buildings(min_consecutive_slots=2)
    assert "building_b" in starving
    assert "building_a" not in starving

    # Fourth slot: Building B gets fully served -> resets consecutive starvation
    tracker.record_slot("building_b", requested_kw=40.0, allocated_kw=40.0)
    assert tracker.get_record("building_b").consecutive_starved_slots == 0
    assert tracker.get_deprivation_boost("building_b") == 0.0
