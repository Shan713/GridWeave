"""Unit tests for the P2 BidValidator and input validation layer."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.auction.validator import BidValidator
from gridweave.models.bid import Bid
from gridweave.models.common import TimeSlot
from gridweave.models.supply import SourceType, SupplyOffer


@pytest.fixture
def slot():
    return TimeSlot(datetime(2026, 1, 5, 10, 0))


@pytest.fixture
def other_slot():
    return TimeSlot(datetime(2026, 1, 5, 10, 15))


@pytest.fixture
def valid_bid(slot):
    return Bid(
        bid_id="hostel_a:20260105T1000:r0",
        building_id="hostel_a",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 9, 45),
        requested_power_kw=40.0,
        minimum_power_kw=25.0,
        critical_power_kw=15.0,
        flexible_power_kw=25.0,
        priority_score=0.75,
        flexibility_score=0.375,
        willingness_to_pay=8.5,
        maximum_price=12.0,
        revision=0,
        capacity_kw=100.0,
    )


def test_validator_accepts_valid_bid(valid_bid, slot):
    validator = BidValidator()
    errs = validator.validate_bid(valid_bid, expected_slot=slot)
    assert errs == []


def test_validator_detects_slot_mismatch(valid_bid, other_slot):
    validator = BidValidator()
    errs = validator.validate_bid(valid_bid, expected_slot=other_slot)
    assert any("does not match market slot" in e for e in errs)


def test_validator_detects_empty_ids(slot):
    validator = BidValidator()
    # Construct bid using object.__new__ to test defensive layer against unvalidated objects
    b = object.__new__(Bid)
    object.__setattr__(b, "bid_id", "")
    object.__setattr__(b, "building_id", "")
    object.__setattr__(b, "time_slot", slot)
    object.__setattr__(b, "created_at", datetime.now())
    object.__setattr__(b, "requested_power_kw", 10.0)
    object.__setattr__(b, "minimum_power_kw", 5.0)
    object.__setattr__(b, "critical_power_kw", 2.0)
    object.__setattr__(b, "flexible_power_kw", 8.0)
    object.__setattr__(b, "priority_score", 0.5)
    object.__setattr__(b, "flexibility_score", 0.5)
    object.__setattr__(b, "willingness_to_pay", 5.0)
    object.__setattr__(b, "maximum_price", 10.0)
    object.__setattr__(b, "revision", 0)
    object.__setattr__(b, "capacity_kw", 50.0)
    object.__setattr__(b, "voluntary_reduction_kw", 0.0)

    errs = validator.validate_bid(b, expected_slot=slot)
    assert any("Invalid or empty bid_id" in e for e in errs)
    assert any("Invalid or empty building_id" in e for e in errs)


def test_validator_handles_revisions_correctly(valid_bid, slot):
    validator = BidValidator()

    # Create revised bid for hostel_a with revision=1
    revised_bid = Bid(
        bid_id="hostel_a:20260105T1000:r1",
        building_id="hostel_a",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 9, 50),
        requested_power_kw=35.0,
        minimum_power_kw=25.0,
        critical_power_kw=15.0,
        flexible_power_kw=20.0,
        priority_score=0.75,
        flexibility_score=0.285,
        willingness_to_pay=8.5,
        maximum_price=12.0,
        revision=1,
        capacity_kw=100.0,
    )

    # Submitting older revision first, then newer revision
    report = validator.validate_bids([valid_bid, revised_bid], expected_slot=slot)
    assert report.is_valid
    assert len(report.accepted_bids) == 1
    assert report.accepted_bids[0].revision == 1
    assert report.accepted_bids[0].requested_power_kw == 35.0
    assert any("supersedes revision 0" in w for w in report.warnings)


def test_validator_rejects_duplicate_revision(valid_bid, slot):
    validator = BidValidator()
    # Duplicate with same revision
    dup_bid = Bid(
        bid_id="hostel_a:20260105T1000:r0_dup",
        building_id="hostel_a",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 9, 45),
        requested_power_kw=40.0,
        minimum_power_kw=25.0,
        critical_power_kw=15.0,
        flexible_power_kw=25.0,
        priority_score=0.75,
        flexibility_score=0.375,
        willingness_to_pay=8.5,
        maximum_price=12.0,
        revision=0,
        capacity_kw=100.0,
    )

    report = validator.validate_bids([valid_bid, dup_bid], expected_slot=slot)
    assert not report.is_valid
    assert any("Duplicate revision" in e for e in report.errors)


def test_validator_rejects_stale_revision(valid_bid, slot):
    validator = BidValidator()
    rev1 = Bid(
        bid_id="hostel_a:20260105T1000:r1",
        building_id="hostel_a",
        time_slot=slot,
        created_at=datetime(2026, 1, 5, 9, 50),
        requested_power_kw=35.0,
        minimum_power_kw=25.0,
        critical_power_kw=15.0,
        flexible_power_kw=20.0,
        priority_score=0.75,
        flexibility_score=0.285,
        willingness_to_pay=8.5,
        maximum_price=12.0,
        revision=1,
    )

    # rev1 received first, then stale rev0
    report = validator.validate_bids([rev1, valid_bid], expected_slot=slot)
    assert not report.is_valid
    assert any("Stale revision" in e for e in report.errors)


def test_validator_rejects_duplicate_bid_id(valid_bid, slot):
    validator = BidValidator()
    report = validator.validate_bids([valid_bid, valid_bid], expected_slot=slot)
    assert not report.is_valid
    assert any("Duplicate bid_id" in e for e in report.errors)


def test_validator_validates_offers(slot, other_slot):
    validator = BidValidator()
    offers = [
        SupplyOffer("grid", SourceType.GRID, slot, 200.0, 10.0),
        SupplyOffer("solar", SourceType.SOLAR, slot, 50.0, 0.0),
    ]
    errs = validator.validate_offers(offers, expected_slot=slot)
    assert errs == []

    # Duplicate offer
    dup_offers = [
        SupplyOffer("grid", SourceType.GRID, slot, 200.0, 10.0),
        SupplyOffer("grid", SourceType.GRID, slot, 100.0, 9.0),
    ]
    errs = validator.validate_offers(dup_offers, expected_slot=slot)
    assert any("Duplicate offer" in e for e in errs)

    # Wrong slot
    wrong_slot_offers = [
        SupplyOffer("grid", SourceType.GRID, other_slot, 200.0, 10.0),
    ]
    errs = validator.validate_offers(wrong_slot_offers, expected_slot=slot)
    assert any("does not match clearing slot" in e for e in errs)
