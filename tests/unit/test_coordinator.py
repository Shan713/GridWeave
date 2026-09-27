"""Reference coordinator safety (audit F7): validation, failure path, recovery."""
from __future__ import annotations

import dataclasses

import pytest

from gridweave.agents import AgentPhase
from gridweave.config import load_campus_config
from gridweave.factory import build_agents, build_simulators
from gridweave.mocks import MockAuctioneer, MockCoordinator, MockGrid, MockSupply, SettlementError
from gridweave.models import ClearingResult
from gridweave.utils.validation import ValidationError


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


# ------------------------------------------------ settlement-failure recovery (post-audit I.2)
class CorruptingEnv:
    """Wraps an environment and reports impossible demand (above capacity), or raises, at chosen slots."""

    def __init__(self, env, bad_steps, mode="over_capacity"):
        self.env, self.bad_steps, self.mode, self.calls = env, set(bad_steps), mode, 0

    @property
    def has_next(self):
        return self.env.has_next

    def step(self):
        obs = self.env.step()
        self.calls += 1
        if self.calls in self.bad_steps:
            if self.mode == "raise":
                raise OSError("meter feed unavailable")
            return dataclasses.replace(obs, measured_demand_kw=self.env.spec.capacity_kw * 2)
        return obs

    def apply_settlement(self, settlement):
        self.env.apply_settlement(settlement)


def corrupted_coordinator(bad_building, bad_steps, mode="over_capacity", periods=12):
    cfg = load_campus_config()
    agents, sims = build_agents(cfg), build_simulators(cfg, periods)
    envs = {k: (CorruptingEnv(v, bad_steps, mode) if k == bad_building else v) for k, v in sims.items()}
    return agents, MockCoordinator(agents, envs, MockAuctioneer(), MockSupply(MockGrid(1000)))


def test_settlement_failure_does_not_strand_other_agents():
    """Test A: a middle agent's realised demand is impossible (2x capacity) in the first market slot."""
    order = list(load_campus_config().buildings)
    bad = order[1].spec.building_id                                   # hostel_b: agents before AND after it
    agents, c = corrupted_coordinator(bad, bad_steps={2})              # call 1 = warm-up, call 2 = first slot
    with pytest.raises(SettlementError) as info:
        c.run_step()
    err = info.value
    assert isinstance(err.__cause__, ValidationError) and "exceeds capacity" in str(err.__cause__)
    assert set(err.failures) == {bad}
    record = err.record
    assert c.records[-1] is record and set(record.settlement_failures) == {bad}
    assert set(record.settlements) == set(agents) - {bad}             # everyone else genuinely settled
    for building_id, agent in agents.items():
        assert agent.pending_bid is None
        assert agent.phase is (AgentPhase.OBSERVED if building_id == bad else AgentPhase.SETTLED)
        assert agent.history[-1].timestamp == record.time_slot.start  # all agents synchronised on the slot
    failed = agents[bad]
    assert failed.stats.slots_settled == 0                            # no service result was fabricated
    assert [e.kind for e in failed.events][-2:] == ["abort", "observe"]
    assert failed.history[-1].demand_kw == failed.history[-2].demand_kw  # carried forward, not the bad value


def test_failed_agents_take_part_in_the_next_cycles():
    """Test D: recovery leaves the failed agent usable, not just in a different phase."""
    bad = "hostel_b"
    agents, c = corrupted_coordinator(bad, bad_steps={2})
    with pytest.raises(SettlementError):
        c.run_step()
    for _ in range(3):
        record = c.run_step()
        assert not record.failure and not record.settlement_failures
        assert set(record.settlements) == set(agents)
    assert agents[bad].stats.slots_settled == 3 and agents[bad].phase is AgentPhase.SETTLED
    assert agents[bad].energy_balance_kwh() == pytest.approx(0, abs=1e-9)


def test_environment_failure_is_reported_and_recovered():
    agents, c = corrupted_coordinator("eng_lab", bad_steps={2}, mode="raise")
    with pytest.raises(SettlementError) as info:
        c.run_step()
    assert isinstance(info.value.__cause__, OSError)
    assert all(a.pending_bid is None for a in agents.values())
    assert set(c.run_step().settlements) == set(agents)


def test_settlement_failure_is_raised_even_with_settle_zero_policy():
    """Data errors are never swallowed: on_failure only governs market/dispatch failures."""
    _, c = corrupted_coordinator("hostel_a", bad_steps={2})
    assert c.on_failure == "settle_zero"
    with pytest.raises(SettlementError):
        c.run_step()
    assert c.failures == 1


def test_market_failure_with_raise_policy_can_retry_the_same_slot():
    agents, c = coordinator(Crashing(fail_times=1), on_failure="raise")
    with pytest.raises(RuntimeError):
        c.run_step()
    slot = next(iter(agents.values())).next_slot()
    record = c.run_step()                                              # environments did not advance
    assert record.time_slot == slot and set(record.settlements) == set(agents)


def test_normal_multi_cycle_path_is_unchanged():
    """Test B: observe -> bid -> clear -> dispatch -> settle, repeatedly, with no failures."""
    agents, c = coordinator(MockAuctioneer(), MockSupply(MockGrid(1000)), periods=6)
    records = []
    for k in range(5):
        r = c.run_step()
        records.append(r)
        assert r.failure is None and r.settlement_failures == {}
        assert set(r.settlements) == set(agents) and r.clearing is not None and r.dispatch_results
        assert all(len(a.history) == k + 2 for a in agents.values())      # warm-up slot + one per cycle
    assert not c.has_next and c.failures == 0
    assert all(a.phase is AgentPhase.SETTLED and a.stats.slots_settled == 5 for a in agents.values())
    assert all(r.to_dict()["settlement_failures"] == {} for r in records)
