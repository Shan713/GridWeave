"""Reference coordinator safety (audit F7): validation, failure path, recovery."""
from __future__ import annotations

import dataclasses

import pytest

from gridweave.agents import AgentPhase
from gridweave.config import load_campus_config
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockCoordinator, MockGrid, MockSupply
from gridweave.models import ClearingResult


def coordinator(auctioneer, supply=None, periods=8, **kw):
    cfg = load_campus_config()
    agents, sims = build_agents(cfg), build_simulators(cfg, periods)
    return agents, MockCoordinator(agents, sims, auctioneer, supply or MockSupply(MockGrid(100)), **kw)


class OverAllocating(MockAuctioneer):
    def clear(self, slot, bids, offers):
        r = super().clear(slot, bids, offers)
        tripled = tuple(dataclasses.replace(a, allocated_power_kw=a.allocated_power_kw * 3, supply_mix={})
                        for a in r.allocations)
        return dataclasses.replace(r, allocations=tripled)


class Crashing(MockAuctioneer):
    def __init__(self, fail_times=1):
        super().__init__()
        self.fail_times = fail_times

    def clear(self, slot, bids, offers):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("P2 crashed")
        return super().clear(slot, bids, offers)


def test_over_allocation_is_rejected_and_settled_as_failure():
    agents, c = coordinator(OverAllocating())
    record = c.run_step()
    assert record.failure and "ContractViolation" in record.failure
    assert all(s.allocated_kw == 0 for s in record.settlements.values())
    assert all(a.phase is AgentPhase.SETTLED for a in agents.values())


def test_duplicate_allocation_cannot_be_constructed_or_passed():
    class Dup(MockAuctioneer):
        def clear(self, slot, bids, offers):
            r = super().clear(slot, bids, offers)
            dup = r.allocations + (r.allocations[0],)
            return ClearingResult(slot, dup, r.dispatch)               # raises: duplicate allocation

    _, c = coordinator(Dup())
    record = c.run_step()
    assert record.failure and "more than one allocation" in record.failure


def test_missing_allocation_is_a_contract_violation():
    class Missing(MockAuctioneer):
        def clear(self, slot, bids, offers):
            r = super().clear(slot, bids, offers)
            return ClearingResult(slot, r.allocations[1:], ())

    _, c = coordinator(Missing())
    assert "missing" in c.run_step().failure


def test_auction_exception_settles_everyone_and_next_step_recovers():
    agents, c = coordinator(Crashing(fail_times=1))
    first = c.run_step()
    assert first.failure == "RuntimeError: P2 crashed"
    assert all(a.phase is AgentPhase.SETTLED for a in agents.values())      # nobody stuck in bid_pending
    assert all(s.served_kw == 0 for s in first.settlements.values())
    second = c.run_step()
    assert second.failure is None and second.served_kw > 0
    assert c.failures == 1


def test_raise_policy_aborts_pending_bids_then_reraises():
    agents, c = coordinator(Crashing(fail_times=5), on_failure="raise")
    with pytest.raises(RuntimeError):
        c.run_step()
    assert all(a.phase is AgentPhase.OBSERVED and a.pending_bid is None for a in agents.values())


def test_under_delivering_source_scales_allocations_down():
    class HalfDelivery(MockGrid):
        """A source that honours only half of every dispatch (e.g. a feeder trip)."""

        def dispatch(self, request):
            r = super().dispatch(request)
            return dataclasses.replace(r, delivered_kw=r.delivered_kw / 2)

    agents, c = coordinator(MockAuctioneer(), MockSupply(HalfDelivery(1000)))
    record = c.run_step()
    assert record.failure is None
    cleared = {a.building_id: a.allocated_power_kw for a in record.clearing.allocations}
    for building_id, s in record.settlements.items():
        assert s.allocated_kw == pytest.approx(cleared[building_id] / 2)
    assert record.served_kw <= record.delivered_kw + 1e-6
