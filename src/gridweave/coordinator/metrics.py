"""Metrics aggregation for GridWeave Coordinator.

All metric calculations live here.  Dashboards, exports and the CLI runner
read from :class:`CampusMetrics` rather than computing their own numbers,
so there is one canonical source of truth.

Usage::

    result: SimulationResult = coordinator.run()
    metrics = MetricsAggregator.compute(result)
    print(metrics.summary_text())
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

from gridweave.coordinator.records import SimulationResult

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketMetrics:
    """Market-level aggregate metrics for one simulation run."""

    total_bids: int
    total_clearing_rounds: int
    scarcity_slots: int
    re_auction_slots: int
    market_failure_slots: int
    avg_clearing_price: float | None
    min_clearing_price: float | None
    max_clearing_price: float | None
    price_std: float | None
    bid_acceptance_ratio: float         # allocated_kw / requested_kw overall
    supply_utilization: float           # delivered_kw / supply_kw overall
    avg_scarcity: float                 # mean scarcity signal across all slots

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_bids": self.total_bids,
            "total_clearing_rounds": self.total_clearing_rounds,
            "scarcity_slots": self.scarcity_slots,
            "re_auction_slots": self.re_auction_slots,
            "market_failure_slots": self.market_failure_slots,
            "avg_clearing_price": round(self.avg_clearing_price, 4) if self.avg_clearing_price else None,
            "min_clearing_price": round(self.min_clearing_price, 4) if self.min_clearing_price else None,
            "max_clearing_price": round(self.max_clearing_price, 4) if self.max_clearing_price else None,
            "price_std": round(self.price_std, 4) if self.price_std else None,
            "bid_acceptance_ratio": round(self.bid_acceptance_ratio, 4),
            "supply_utilization": round(self.supply_utilization, 4),
            "avg_scarcity": round(self.avg_scarcity, 4),
        }


@dataclass(frozen=True)
class FairnessMetrics:
    """Jain's fairness index and allocation equity metrics."""

    jains_index: float              # per-slot average of Jain's fairness index
    jains_index_final: float        # Jain's index on overall service ratios
    min_building_service_ratio: float
    max_building_service_ratio: float
    service_ratio_std: float        # std dev across buildings

    def to_dict(self) -> dict[str, Any]:
        return {
            "jains_index": round(self.jains_index, 4),
            "jains_index_final": round(self.jains_index_final, 4),
            "min_building_service_ratio": round(self.min_building_service_ratio, 4),
            "max_building_service_ratio": round(self.max_building_service_ratio, 4),
            "service_ratio_std": round(self.service_ratio_std, 4),
        }


@dataclass(frozen=True)
class EnergyConservationMetrics:
    """Validates energy accounting consistency across the simulation."""

    energy_balance_sum_kwh: float       # sum of agent energy_balance_kwh(); should be ~0
    max_slot_delivery_error_kwh: float  # max |delivered - allocated| per slot (kWh)
    mean_slot_delivery_error_kwh: float
    n_slots_with_delivery_shortfall: int

    @property
    def is_conserved(self) -> bool:
        return abs(self.energy_balance_sum_kwh) < 0.1 and self.max_slot_delivery_error_kwh < 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "energy_balance_sum_kwh": round(self.energy_balance_sum_kwh, 6),
            "max_slot_delivery_error_kwh": round(self.max_slot_delivery_error_kwh, 6),
            "mean_slot_delivery_error_kwh": round(self.mean_slot_delivery_error_kwh, 6),
            "n_slots_with_delivery_shortfall": self.n_slots_with_delivery_shortfall,
            "is_conserved": self.is_conserved,
        }


@dataclass(frozen=True)
class CampusMetrics:
    """Complete set of campus-level metrics for a simulation run."""

    # Identity
    scenario_name: str
    seed: int
    n_buildings: int
    n_slots: int
    resolution_minutes: int

    # Demand
    total_demand_kwh: float
    peak_demand_kw: float
    avg_demand_kw: float

    # Service
    total_requested_kwh: float
    total_allocated_kwh: float
    total_delivered_kwh: float
    total_served_kwh: float
    total_deferred_kwh: float
    total_curtailed_kwh: float
    total_critical_shortfall_kwh: float
    total_critical_shortfall_events: int
    total_unused_allocation_kwh: float

    # Ratios
    overall_service_ratio: float
    critical_service_ratio: float
    flexible_service_ratio: float
    bid_acceptance_ratio: float

    # Forecast
    forecast_mae_kw: float | None
    forecast_rmse_kw: float | None

    # Supply (from P3 SupplyMetrics)
    total_grid_kwh: float
    total_solar_kwh: float
    total_battery_discharge_kwh: float
    total_battery_charge_kwh: float
    renewable_penetration_rate: float
    total_procurement_cost: float
    peak_grid_import_kw: float
    co2_displaced_kg: float

    # Events
    n_events: int

    # Sub-metrics
    market: MarketMetrics
    fairness: FairnessMetrics
    conservation: EnergyConservationMetrics

    # Per-slot time series (for charts)
    slot_timestamps: list[str]
    slot_demand_kw: list[float]
    slot_supply_kw: list[float]
    slot_allocated_kw: list[float]
    slot_delivered_kw: list[float]
    slot_served_kw: list[float]
    slot_critical_shortfall_kw: list[float]
    slot_deferred_kw: list[float]
    slot_curtailed_kw: list[float]
    slot_service_ratio: list[float]
    slot_clearing_price: list[float | None]
    slot_events: list[list[str]]

    def summary_text(self) -> str:
        """Return a concise human-readable summary for CLI output."""
        lines = [
            f"{'=' * 70}",
            f"  GridWeave Simulation Summary — {self.scenario_name} (seed={self.seed})",
            f"{'=' * 70}",
            f"  Buildings : {self.n_buildings}",
            f"  Slots     : {self.n_slots} × {self.resolution_minutes} min",
            "",
            "  DEMAND & SERVICE",
            f"    Total demand        : {self.total_demand_kwh:,.1f} kWh",
            f"    Total served        : {self.total_served_kwh:,.1f} kWh",
            f"    Service ratio       : {self.overall_service_ratio:.2%}",
            f"    Critical shortfall  : {self.total_critical_shortfall_kwh:.1f} kWh "
            f"({self.total_critical_shortfall_events} events)",
            f"    Deferred            : {self.total_deferred_kwh:.1f} kWh",
            f"    Curtailed           : {self.total_curtailed_kwh:.1f} kWh",
            "",
            "  SUPPLY",
            f"    Grid                : {self.total_grid_kwh:,.1f} kWh",
            f"    Solar               : {self.total_solar_kwh:,.1f} kWh",
            f"    Battery discharge   : {self.total_battery_discharge_kwh:,.1f} kWh",
            f"    Renewable fraction  : {self.renewable_penetration_rate:.1%}",
            f"    Total cost          : {self.total_procurement_cost:,.2f}",
            "",
            "  MARKET",
            f"    Scarcity slots      : {self.market.scarcity_slots}",
            f"    Re-auction slots    : {self.market.re_auction_slots}",
            f"    Market failures     : {self.market.market_failure_slots}",
            f"    Avg clearing price  : {self.market.avg_clearing_price}",
            f"    Supply utilization  : {self.market.supply_utilization:.2%}",
            "",
            "  FAIRNESS",
            f"    Jain's index (avg)  : {self.fairness.jains_index:.4f}",
            f"    Jain's index (final): {self.fairness.jains_index_final:.4f}",
            f"    Min service ratio   : {self.fairness.min_building_service_ratio:.2%}",
            f"    Max service ratio   : {self.fairness.max_building_service_ratio:.2%}",
            "",
            f"  EVENTS : {self.n_events}",
            f"  ENERGY CONSERVATION : {'OK' if self.conservation.is_conserved else 'WARNING'}",
            f"{'=' * 70}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "scenario_name": self.scenario_name,
            "seed": self.seed,
            "n_buildings": self.n_buildings,
            "n_slots": self.n_slots,
            "resolution_minutes": self.resolution_minutes,
            "total_demand_kwh": round(self.total_demand_kwh, 2),
            "peak_demand_kw": round(self.peak_demand_kw, 2),
            "avg_demand_kw": round(self.avg_demand_kw, 2),
            "total_requested_kwh": round(self.total_requested_kwh, 2),
            "total_allocated_kwh": round(self.total_allocated_kwh, 2),
            "total_delivered_kwh": round(self.total_delivered_kwh, 2),
            "total_served_kwh": round(self.total_served_kwh, 2),
            "total_deferred_kwh": round(self.total_deferred_kwh, 2),
            "total_curtailed_kwh": round(self.total_curtailed_kwh, 2),
            "total_critical_shortfall_kwh": round(self.total_critical_shortfall_kwh, 3),
            "total_critical_shortfall_events": self.total_critical_shortfall_events,
            "overall_service_ratio": round(self.overall_service_ratio, 4),
            "critical_service_ratio": round(self.critical_service_ratio, 4),
            "flexible_service_ratio": round(self.flexible_service_ratio, 4),
            "bid_acceptance_ratio": round(self.bid_acceptance_ratio, 4),
            "forecast_mae_kw": round(self.forecast_mae_kw, 3) if self.forecast_mae_kw else None,
            "forecast_rmse_kw": round(self.forecast_rmse_kw, 3) if self.forecast_rmse_kw else None,
            "total_grid_kwh": round(self.total_grid_kwh, 2),
            "total_solar_kwh": round(self.total_solar_kwh, 2),
            "total_battery_discharge_kwh": round(self.total_battery_discharge_kwh, 2),
            "total_battery_charge_kwh": round(self.total_battery_charge_kwh, 2),
            "renewable_penetration_rate": round(self.renewable_penetration_rate, 4),
            "total_procurement_cost": round(self.total_procurement_cost, 2),
            "peak_grid_import_kw": round(self.peak_grid_import_kw, 2),
            "co2_displaced_kg": round(self.co2_displaced_kg, 2),
            "n_events": self.n_events,
            "market": self.market.to_dict(),
            "fairness": self.fairness.to_dict(),
            "conservation": self.conservation.to_dict(),
        }
        return d


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _jains_index(values: list[float]) -> float:
    """Jain's fairness index over a list of allocation ratios [0, 1]."""
    n = len(values)
    if n == 0:
        return 1.0
    s = sum(values)
    sq = sum(v * v for v in values)
    return (s * s) / (n * sq) if sq > 0 else 1.0


def _safe_mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _safe_stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


# ---------------------------------------------------------------------------
# MetricsAggregator
# ---------------------------------------------------------------------------

class MetricsAggregator:
    """Compute :class:`CampusMetrics` from a :class:`SimulationResult`.

    Usage::

        metrics = MetricsAggregator.compute(result, agents)
    """

    @staticmethod
    def compute(
        result: SimulationResult,
        agents: dict | None = None,
    ) -> CampusMetrics:
        """Aggregate all metrics from *result*.

        Parameters
        ----------
        result:
            Completed simulation result.
        agents:
            Optional mapping of building_id → BuildingAgent.  When provided,
            the energy-conservation check calls ``agent.energy_balance_kwh()``.
        """
        slots = result.slots
        h = result.resolution_minutes / 60.0

        # --- time-series arrays ---
        ts = [r.time_slot.start.isoformat() for r in slots]
        demand_kw    = [r.actual_demand_kw   for r in slots]
        supply_kw    = [r.supply_kw          for r in slots]
        alloc_kw     = [r.allocated_kw       for r in slots]
        deliv_kw     = [r.delivered_kw       for r in slots]
        served_kw    = [r.served_kw          for r in slots]
        crit_sf_kw   = [r.critical_shortfall_kw for r in slots]
        deferred_kw  = [r.deferred_kw        for r in slots]
        curtailed_kw = [r.curtailed_kw       for r in slots]
        svc_ratio    = [r.service_ratio      for r in slots]
        prices       = [r.clearing_price     for r in slots]
        evts         = [r.events_triggered   for r in slots]

        # --- campus totals ---
        total_demand   = sum(d * h for d in demand_kw)
        total_req      = sum(r.requested_kw * h for r in slots)
        total_alloc    = sum(a * h for a in alloc_kw)
        total_deliv    = sum(d * h for d in deliv_kw)
        total_served   = sum(s * h for s in served_kw)
        total_deferred = sum(d * h for d in deferred_kw)
        total_curtailed = sum(c * h for c in curtailed_kw)
        total_crit_sf  = sum(s.critical_shortfall_kwh for s in result.building_summaries.values())
        total_crit_events = sum(s.critical_shortfall_events for s in result.building_summaries.values())
        total_unused   = sum(s.unused_allocation_kwh for s in result.building_summaries.values())

        peak_demand = max(demand_kw, default=0.0)
        avg_demand  = _safe_mean(demand_kw)

        # --- service ratios ---
        total_critical_served = sum(
            s.critical_served_kw * h
            for r in slots for s in r.settlements.values()
        )
        total_critical_demand = sum(
            s.actual_critical_kw * h
            for r in slots for s in r.settlements.values()
        )
        crit_svc_ratio = (
            total_critical_served / total_critical_demand if total_critical_demand > 0 else 1.0
        )
        total_flex_served = sum(
            (s.new_flexible_served_kw + s.backlog_served_kw) * h
            for r in slots for s in r.settlements.values()
        )
        total_flex_demand = total_demand - total_critical_demand
        flex_svc_ratio = total_flex_served / total_flex_demand if total_flex_demand > 0 else 1.0

        bid_acc_ratio = total_alloc / total_req if total_req > 0 else 1.0

        # --- forecast metrics ---
        mae_vals = [s.forecast_mae_kw for s in result.building_summaries.values()
                    if s.forecast_mae_kw is not None]
        rmse_vals = [s.forecast_rmse_kw for s in result.building_summaries.values()
                     if s.forecast_rmse_kw is not None]
        forecast_mae  = _safe_mean(mae_vals) if mae_vals else None
        forecast_rmse = (
            math.sqrt(_safe_mean([v * v for v in rmse_vals])) if rmse_vals else None
        )

        # --- supply metrics from result.supply_metrics ---
        sm = result.supply_metrics
        total_grid   = sm.get("grid_import_kwh", 0.0)
        total_solar  = sm.get("solar_delivered_kwh", 0.0)
        total_batt_d = sm.get("battery_discharge_kwh", 0.0)
        total_batt_c = sm.get("battery_charge_kwh", 0.0)
        renew_pene   = sm.get("renewable_penetration_rate", 0.0)
        proc_cost    = sm.get("total_procurement_cost", 0.0)
        peak_grid    = sm.get("peak_grid_import_kw", 0.0)
        co2_disp     = sm.get("co2_displaced_kg", 0.0)

        # --- market metrics ---
        n_bids     = sum(len(r.bids) for r in slots)
        n_rounds   = sum(r.rounds for r in slots)
        scarcity_s = result.scarcity_slots
        re_auct_s  = result.re_auction_slots
        fail_s     = result.market_failure_slots

        valid_prices = [p for p in prices if p is not None]
        avg_price = _safe_mean(valid_prices) if valid_prices else None
        min_price = min(valid_prices) if valid_prices else None
        max_price = max(valid_prices) if valid_prices else None
        price_std = _safe_stdev(valid_prices) if len(valid_prices) >= 2 else None

        supply_util = (
            (sum(deliv_kw) / sum(supply_kw)) if sum(supply_kw) > 0 else 0.0
        )
        avg_scarcity = _safe_mean([r.scarcity for r in slots])

        market = MarketMetrics(
            total_bids=n_bids,
            total_clearing_rounds=n_rounds,
            scarcity_slots=scarcity_s,
            re_auction_slots=re_auct_s,
            market_failure_slots=fail_s,
            avg_clearing_price=avg_price,
            min_clearing_price=min_price,
            max_clearing_price=max_price,
            price_std=price_std,
            bid_acceptance_ratio=bid_acc_ratio,
            supply_utilization=supply_util,
            avg_scarcity=avg_scarcity,
        )

        # --- fairness metrics ---
        building_service_ratios = [
            s.service_ratio for s in result.building_summaries.values()
        ]
        # Per-slot Jain's index (average)
        slot_jains = []
        for r in slots:
            if r.settlements:
                ratios = [s.satisfaction_ratio for s in r.settlements.values()]
                slot_jains.append(_jains_index(ratios))
        avg_jains = _safe_mean(slot_jains) if slot_jains else 1.0
        final_jains = _jains_index(building_service_ratios)

        fairness = FairnessMetrics(
            jains_index=avg_jains,
            jains_index_final=final_jains,
            min_building_service_ratio=min(building_service_ratios, default=1.0),
            max_building_service_ratio=max(building_service_ratios, default=1.0),
            service_ratio_std=_safe_stdev(building_service_ratios),
        )

        # --- energy conservation ---
        balance_sum = 0.0
        if agents:
            for agent in agents.values():
                balance_sum += agent.energy_balance_kwh()

        delivery_errors = []
        for r in slots:
            err = abs(r.delivered_kw - r.allocated_kw) * h
            delivery_errors.append(err)
        n_shortfall_slots = sum(
            1 for r in slots if r.delivered_kw < r.allocated_kw - 1e-6
        )

        conservation = EnergyConservationMetrics(
            energy_balance_sum_kwh=balance_sum,
            max_slot_delivery_error_kwh=max(delivery_errors, default=0.0),
            mean_slot_delivery_error_kwh=_safe_mean(delivery_errors),
            n_slots_with_delivery_shortfall=n_shortfall_slots,
        )

        return CampusMetrics(
            scenario_name=result.scenario_name,
            seed=result.seed,
            n_buildings=result.n_buildings,
            n_slots=result.n_slots,
            resolution_minutes=result.resolution_minutes,
            total_demand_kwh=total_demand,
            peak_demand_kw=peak_demand,
            avg_demand_kw=avg_demand,
            total_requested_kwh=total_req,
            total_allocated_kwh=total_alloc,
            total_delivered_kwh=total_deliv,
            total_served_kwh=total_served,
            total_deferred_kwh=total_deferred,
            total_curtailed_kwh=total_curtailed,
            total_critical_shortfall_kwh=total_crit_sf,
            total_critical_shortfall_events=total_crit_events,
            total_unused_allocation_kwh=total_unused,
            overall_service_ratio=total_served / total_demand if total_demand > 0 else 1.0,
            critical_service_ratio=crit_svc_ratio,
            flexible_service_ratio=flex_svc_ratio,
            bid_acceptance_ratio=bid_acc_ratio,
            forecast_mae_kw=forecast_mae,
            forecast_rmse_kw=forecast_rmse,
            total_grid_kwh=total_grid,
            total_solar_kwh=total_solar,
            total_battery_discharge_kwh=total_batt_d,
            total_battery_charge_kwh=total_batt_c,
            renewable_penetration_rate=renew_pene,
            total_procurement_cost=proc_cost,
            peak_grid_import_kw=peak_grid,
            co2_displaced_kg=co2_disp,
            n_events=result.n_events,
            market=market,
            fairness=fairness,
            conservation=conservation,
            slot_timestamps=ts,
            slot_demand_kw=demand_kw,
            slot_supply_kw=supply_kw,
            slot_allocated_kw=alloc_kw,
            slot_delivered_kw=deliv_kw,
            slot_served_kw=served_kw,
            slot_critical_shortfall_kw=crit_sf_kw,
            slot_deferred_kw=deferred_kw,
            slot_curtailed_kw=curtailed_kw,
            slot_service_ratio=svc_ratio,
            slot_clearing_price=prices,
            slot_events=evts,
        )
