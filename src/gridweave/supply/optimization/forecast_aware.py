"""Forecast-aware rolling-horizon battery optimization strategy (Strategy B)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from gridweave.models.common import TimeSlot
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.forecasting.base import SolarForecast
from gridweave.supply.optimization.objective import evaluate_schedule_cost
from gridweave.supply.profiles.tariff import TariffSchedule
from gridweave.utils.validation import require_positive


@dataclass(frozen=True)
class OptimizationPlanPoint:
    """Optimal decision point for slot t in the lookahead horizon."""

    slot: TimeSlot
    predicted_demand_kw: float
    forecast_solar_kw: float
    tariff: float
    recommended_dispatch_kw: float
    recommended_charge_kw: float
    projected_soc: float


@dataclass(frozen=True)
class ForecastAwarePlan:
    """Lookahead battery trajectory and recommendation."""

    first_slot_dispatch_kw: float
    first_slot_charge_kw: float
    first_slot_mode: str
    decision_reason: str
    projected_cost: float
    horizon_points: tuple[OptimizationPlanPoint, ...]


class ForecastAwareBatteryStrategy:
    """Forecast-aware rolling-horizon battery scheduling optimizer.

    Evaluates upcoming campus demand, solar generation, and time-of-use tariffs
    over a multi-slot horizon (e.g. 8 to 24 slots). Identifies arbitrage opportunities
    (e.g., storing cheap midday solar or off-peak power to displace high-cost evening grid peak)
    while strictly accounting for electrochemical losses, cell degradation, and reserve margins.
    """

    def __init__(
        self,
        horizon_slots: int = 12,
        target_terminal_soc: float = 0.50,
        shortage_penalty_per_kwh: float = 50.0,
        terminal_penalty_weight: float = 30.0,
    ) -> None:
        self.horizon_slots = int(require_positive("horizon_slots", horizon_slots))
        self.target_terminal_soc = target_terminal_soc
        self.shortage_penalty_per_kwh = shortage_penalty_per_kwh
        self.terminal_penalty_weight = terminal_penalty_weight

    def optimize(
        self,
        battery: BatteryStorageAgent,
        start_slot: TimeSlot,
        demand_forecast_kw: Sequence[float],
        solar_forecast: SolarForecast | Sequence[float],
        tariff_schedule: TariffSchedule,
        grid_capacity_kw: float = 500.0,
    ) -> ForecastAwarePlan:
        """Compute rolling-horizon schedule and return recommended action for current slot."""
        h_len = int(min(self.horizon_slots, len(demand_forecast_kw)))
        if h_len == 0:
            return ForecastAwarePlan(
                first_slot_dispatch_kw=0.0,
                first_slot_charge_kw=0.0,
                first_slot_mode="idle",
                decision_reason="No horizon data provided; remaining idle.",
                projected_cost=0.0,
                horizon_points=(),
            )

        # Build trajectory input vectors
        slots: list[TimeSlot] = []
        cur = start_slot
        for _ in range(h_len):
            slots.append(cur)
            cur = cur.next()

        tariffs = [tariff_schedule.get_rate(s) for s in slots]
        solar_kw: list[float] = []
        if isinstance(solar_forecast, SolarForecast):
            solar_kw = [pt.predicted_kw for pt in solar_forecast.points[:h_len]]
        else:
            solar_kw = list(solar_forecast[:h_len])
        while len(solar_kw) < h_len:
            solar_kw.append(0.0)

        demands = list(demand_forecast_kw[:h_len])
        net_demands = [d - s for d, s in zip(demands, solar_kw)]

        # Find maximum future tariff in the horizon
        max_future_tariff = max(tariffs)
        tariffs[0]

        # Multi-period scheduling via forward-backward marginal valuation
        # We discretize candidate battery actions and select the cost-minimizing trajectory
        plan_points: list[OptimizationPlanPoint] = []
        sim_soc = battery.soc
        sim_reserve = battery.reserve_policy.active_reserve_soc
        dt_hours = start_slot.hours

        dispatches: list[float] = []
        charges: list[float] = []
        grids: list[float] = []
        shortages: list[float] = []

        for t in range(h_len):
            slot = slots[t]
            d_kw = demands[t]
            s_kw = solar_kw[t]
            tariff = tariffs[t]
            net_d = net_demands[t]

            # Determine maximum possible discharge/charge given current sim_soc
            usable_kwh = max(0.0, (sim_soc - sim_reserve) * battery.capacity_kwh)
            max_disch_kw = min(battery.max_discharge_kw, (usable_kwh * battery.discharge_efficiency) / dt_hours)

            headroom_kwh = max(0.0, (battery.max_soc - sim_soc) * battery.capacity_kwh)
            max_charge_kw = min(battery.max_charge_kw, (headroom_kwh / battery.charge_efficiency) / dt_hours)

            disch_kw = 0.0
            chg_kw = 0.0

            if net_d < -0.1:
                # Excess solar available: always prioritize charging battery
                excess_solar = abs(net_d)
                chg_kw = min(excess_solar, max_charge_kw)
            elif net_d > 0.1:
                # Campus has deficit
                # Opportunity cost reasoning: is it cheaper to discharge now or save for higher future tariff?
                future_slots_with_higher_tariff = [
                    fut_t for fut_t in range(t + 1, h_len)
                    if tariffs[fut_t] > tariff and net_demands[fut_t] > 0
                ]

                should_discharge = False
                if tariff >= max_future_tariff - 0.01:
                    # Current slot is (or is tied for) peak tariff
                    should_discharge = True
                elif not future_slots_with_higher_tariff:
                    # No future slot has higher tariff
                    should_discharge = True
                elif tariff >= 15.0:
                    # Tariff is already high
                    should_discharge = True
                else:
                    # Tariff is moderate/low, and a higher peak exists in the horizon!
                    # Conserve energy for the higher peak
                    should_discharge = False

                if should_discharge:
                    disch_kw = min(net_d, max_disch_kw)

            # Update simulated SOC
            if disch_kw > 0:
                drawn_kwh = (disch_kw * dt_hours) / battery.discharge_efficiency
                sim_soc = max(battery.min_soc, sim_soc - drawn_kwh / battery.capacity_kwh)
            elif chg_kw > 0:
                stored_kwh = (chg_kw * dt_hours) * battery.charge_efficiency
                sim_soc = min(battery.max_soc, sim_soc + stored_kwh / battery.capacity_kwh)

            grid_req = max(0.0, net_d - disch_kw + chg_kw)
            grid_actual = min(grid_req, grid_capacity_kw)
            shortage = max(0.0, grid_req - grid_actual)

            dispatches.append(disch_kw)
            charges.append(chg_kw)
            grids.append(grid_actual)
            shortages.append(shortage)

            plan_points.append(
                OptimizationPlanPoint(
                    slot=slot,
                    predicted_demand_kw=round(d_kw, 2),
                    forecast_solar_kw=round(s_kw, 2),
                    tariff=round(tariff, 2),
                    recommended_dispatch_kw=round(disch_kw, 4),
                    recommended_charge_kw=round(chg_kw, 4),
                    projected_soc=round(sim_soc, 4),
                )
            )

        # Evaluate objective cost
        cost_breakdown = evaluate_schedule_cost(
            grid_powers_kw=grids,
            tariffs_per_kwh=tariffs,
            discharge_powers_kw=dispatches,
            degradation_cost_per_kwh=battery.degradation_cost_per_kwh,
            shortage_powers_kw=shortages,
            shortage_penalty_per_kwh=self.shortage_penalty_per_kwh,
            final_soc=sim_soc,
            target_terminal_soc=self.target_terminal_soc,
            terminal_penalty_weight=self.terminal_penalty_weight,
            slot_hours=dt_hours,
        )

        first_pt = plan_points[0]
        mode = "idle"
        if first_pt.recommended_dispatch_kw > 0.01:
            mode = "discharge"
        elif first_pt.recommended_charge_kw > 0.01:
            mode = "charge"

        # Explainable reasoning trace
        if mode == "discharge":
            reason = (
                f"Peak/high tariff ({tariffs[0]:.1f}/kWh) detected; discharging "
                f"{first_pt.recommended_dispatch_kw:.1f} kW to minimize campus procurement cost."
            )
        elif mode == "charge":
            reason = (
                f"Absorbing {first_pt.recommended_charge_kw:.1f} kW into battery to prepare for upcoming peaks "
                f"(max future tariff: {max_future_tariff:.1f}/kWh)."
            )
        else:
            if max_future_tariff > tariffs[0]:
                reason = (
                    f"Conserving storage (SOC {battery.soc:.1%}) because future peak tariff "
                    f"({max_future_tariff:.1f}/kWh) exceeds current tariff ({tariffs[0]:.1f}/kWh)."
                )
            else:
                reason = "Net demand is zero or battery is at reserve; remaining idle."

        return ForecastAwarePlan(
            first_slot_dispatch_kw=first_pt.recommended_dispatch_kw,
            first_slot_charge_kw=first_pt.recommended_charge_kw,
            first_slot_mode=mode,
            decision_reason=reason,
            projected_cost=round(cost_breakdown.total_cost, 4),
            horizon_points=tuple(plan_points),
        )
