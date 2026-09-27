"""Closed-loop demonstration: why forecast quality and demand response matter.

Three runs of the same campus day on the same multi-source mock supply
(grid + solar + battery), differing only in how the Building Agents
forecast and whether they respond to scarcity:

  A. good forecast       Seasonal EWMA (the default)
  B. poor forecast       a naive model that always predicts yesterday's daily mean
  C. no demand response  good forecast, but scarcity_response = 0 (price-only revisions)

All numbers come from settlements against REALISED demand.

Reading the result honestly: A vs B shows that forecast quality has real
consequences (shortfalls, unused allocation). A vs C shows that, in the
*mock* market, voluntary demand response changes almost nothing: the mock
allocates all available supply either way, and trimmed load is deferred.
Whether demand response pays off depends on P2's mechanism (e.g. rewarding
reductions or pricing scarcity), which is exactly what P2 must design.

Run: python examples/closed_loop_demo.py
"""
from dataclasses import replace
from statistics import fmean

from gridweave.config import load_campus_config
from gridweave.factory import build_agents, build_simulators
from gridweave.forecasting import BaseForecaster
from gridweave.mocks import MockAuctioneer, MockCoordinator, MockSupply, summarise


class FlatDailyMean(BaseForecaster):
    """Deliberately poor baseline: the mean of the last day, for every slot."""

    name = "flat_daily_mean"

    def _predict(self, values, horizon):
        return [fmean(values[-96:])] * horizon


def run(label, forecaster=None, scarcity_response=None):
    cfg = load_campus_config()
    cfg = replace(cfg, simulation=replace(cfg.simulation, days=2))   # day 1 = history, day 2 = evaluated
    if scarcity_response is not None:
        cfg = replace(cfg, buildings=tuple(replace(b, spec=b.spec.with_overrides(scarcity_response=scarcity_response))
                                           for b in cfg.buildings))
    agents, envs = build_agents(cfg), build_simulators(cfg)
    if forecaster is not None:
        for a in agents.values():
            a.forecaster = forecaster
    supply = MockSupply.from_config(cfg.supply)
    coord = MockCoordinator(agents, envs, MockAuctioneer(), supply)
    for building_id, env in envs.items():                            # day 1: observe only (no market)
        for _ in range(96):
            agents[building_id].observe(env.step())
    coord.run()
    s = summarise(coord.records, agents)
    mae = fmean(b["forecast_mae_kw"] for b in s["buildings"].values())
    battery_soc = supply.sources["battery"].soc
    print(f"{label:<24}{mae:>8.2f}{s['demand_kwh']:>10.0f}{s['served_kwh']:>9.0f}{s['deferred_kwh']:>9.0f}"
          f"{s['curtailed_kwh']:>9.0f}{s['expired_kwh']:>8.0f}{s['unused_allocation_kwh']:>8.0f}"
          f"{s['critical_shortfall_events']:>7}{s['critical_shortfall_kwh']:>8.2f}{battery_soc:>7.2f}")
    return s


print("Tuesday 2026-01-06, 5 buildings, 96 closed-loop 15-min cycles, mock supply grid+solar+battery\n")
print(f"{'scenario':<24}{'fc MAE':>8}{'demand':>10}{'served':>9}{'deferred':>9}{'curtail':>9}{'expired':>8}"
      f"{'unused':>8}{'crit#':>7}{'critkWh':>8}{'SOC':>7}")
print(f"{'':<24}{'kW':>8}{'kWh':>10}{'kWh':>9}{'kWh':>9}{'kWh':>9}{'kWh':>8}{'kWh':>8}")
run("A good forecast")
run("B poor forecast", forecaster=FlatDailyMean())
run("C no demand response", scarcity_response=0.0)
print("\n'unused' = allocated but not needed (over-forecast); 'crit#' = slots where realised critical load was not"
      " fully served.\nThese are mock-market results on synthetic data, not measurements.")
