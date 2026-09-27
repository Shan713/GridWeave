"""SupplyOffer / DispatchRequest / DispatchResult / ClearingResult and the cross-party checks (audit F4)."""
from __future__ import annotations

from datetime import datetime

import pytest

from gridweave.contracts import ContractViolation, validate_clearing, validate_dispatch
from gridweave.models import (
    Allocation,
    Bid,
    ClearingResult,
    DispatchRequest,
    DispatchResult,
    SourceType,
    SupplyOffer,
    TimeSlot,
)
from gridweave.utils.validation import ValidationError

SLOT = TimeSlot(datetime(2026, 1, 5, 19, 0))


def bid(building, requested, critical=5.0):
    return Bid(f"{building}:r0", building, SLOT, SLOT.start, requested, critical, critical, requested - critical,
               0.5, 0.5, 7.0, 12.0)


def offer(source, kw, price, stype=SourceType.GRID):
    return SupplyOffer(source, stype, SLOT, kw, price)


def clearing(allocs, dispatch):
    return ClearingResult(SLOT, tuple(Allocation(f"{b}:r0", b, SLOT, kw) for b, kw in allocs),
                          tuple(DispatchRequest(s, SLOT, kw) for s, kw in dispatch))


BIDS = [bid("h1", 40), bid("h2", 30)]
GRID_SOLAR = [offer("grid", 100, 10), offer("solar", 25, 0, SourceType.SOLAR)]


# ---------------------------------------------------------------- models
def test_offer_validation():
    assert offer("solar", 25, 0, "solar").source_type is SourceType.SOLAR
    for bad in (dict(kw=-1, price=0), dict(kw=1, price=-1)):
        with pytest.raises(ValidationError):
            offer("x", bad["kw"], bad["price"])
    with pytest.raises(ValidationError):
        SupplyOffer("x", "nuclear", SLOT, 1, 1)


def test_dispatch_result_cannot_deliver_more_than_requested():
    DispatchResult("battery", SLOT, 10, 10, 5, {"soc": 0.72})
    with pytest.raises(ValidationError):
        DispatchResult("battery", SLOT, 10, 12, 5)
    assert DispatchResult("battery", SLOT, 10, 6, 0).shortfall_kw == 4


def test_clearing_result_rejects_duplicates_and_wrong_slot():
    with pytest.raises(ValidationError):
        clearing([("h1", 10), ("h1", 5)], [("grid", 15)])
    with pytest.raises(ValidationError):
        clearing([("h1", 10)], [("grid", 5), ("grid", 5)])
    with pytest.raises(ValidationError):
        ClearingResult(SLOT, (Allocation("h1:r0", "h1", SLOT.next(), 1.0),), ())


# ------------------------------------------------------- cross-party checks
def test_valid_multi_source_clearing_solar_plus_grid():
    result = clearing([("h1", 40), ("h2", 30)], [("solar", 25), ("grid", 45)])
    validate_clearing(result, BIDS, GRID_SOLAR)
    assert result.total_allocated_kw == result.total_dispatched_kw == 70


def test_solar_plus_battery_without_grid():
    offers = [offer("solar", 25, 0, SourceType.SOLAR), offer("battery", 15, 7, SourceType.BATTERY)]
    validate_clearing(clearing([("h1", 25), ("h2", 15)], [("solar", 25), ("battery", 15)]), BIDS, offers)


def test_zero_supply_zero_allocations_is_valid():
    validate_clearing(clearing([("h1", 0), ("h2", 0)], []), BIDS, [offer("grid", 0, 10)])


@pytest.mark.parametrize(
    "allocs, dispatch, why",
    [
        ([("h1", 40), ("h2", 30)], [("grid", 100), ("solar", 25)], "dispatch != allocation (energy balance)"),
        ([("h1", 90), ("h2", 30)], [("grid", 95), ("solar", 25)], "allocation above request"),
        ([("h1", 40)], [("grid", 40)], "bid h2 not allocated"),
        ([("h1", 40), ("h2", 30), ("h3", 1)], [("grid", 71)], "unknown bid"),
        ([("h1", 40), ("h2", 30)], [("solar", 40), ("grid", 30)], "dispatch above offer"),
        ([("h1", 40), ("h2", 30)], [("wind", 70)], "dispatch to a source that made no offer"),
    ],
)
def test_invalid_clearings_rejected(allocs, dispatch, why):
    with pytest.raises(ContractViolation):
        validate_clearing(clearing(allocs, dispatch), BIDS, GRID_SOLAR)


def test_total_allocation_cannot_exceed_total_supply():
    small = [offer("grid", 50, 10)]
    with pytest.raises(ContractViolation):
        validate_clearing(clearing([("h1", 40), ("h2", 30)], [("grid", 70)]), BIDS, small)


def test_allocation_naming_wrong_building_rejected():
    bad = ClearingResult(SLOT, (Allocation("h1:r0", "h2", SLOT, 10), Allocation("h2:r0", "h2", SLOT, 0)),
                         (DispatchRequest("grid", SLOT, 10),))
    with pytest.raises(ContractViolation):
        validate_clearing(bad, BIDS, GRID_SOLAR)


def test_validate_dispatch():
    reqs = [DispatchRequest("grid", SLOT, 30), DispatchRequest("battery", SLOT, 10)]
    ok = [DispatchResult("grid", SLOT, 30, 30, 70), DispatchResult("battery", SLOT, 10, 8, 0, {"soc": 0.1})]
    validate_dispatch(reqs, ok)
    with pytest.raises(ContractViolation):
        validate_dispatch(reqs, ok[:1])                                           # missing result
    with pytest.raises(ContractViolation):
        validate_dispatch(reqs, [ok[0], DispatchResult("battery", SLOT, 9, 8, 0)])  # does not echo request
