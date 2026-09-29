"""Native Python / Terminal CLI Dashboard for GridWeave Campus Energy Simulation.

Provides rich text and ASCII visualization of simulation results and campus metrics
without external dependencies (pure standard-library Python).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gridweave.coordinator.metrics import CampusMetrics
    from gridweave.coordinator.records import SimulationResult


class CliDashboard:
    """Terminal dashboard generator for rendering ASCII simulation reports."""

    @staticmethod
    def render_summary(result: SimulationResult, metrics: CampusMetrics) -> str:
        """Render a formatted multi-section summary report as a string."""
        curtailment_rate = metrics.total_curtailed_kwh / max(1.0, metrics.total_demand_kwh)
        lines: list[str] = []

        border = "+" + "-" * 78 + "+"
        div = "|" + "-" * 78 + "|"

        lines.append(border)
        lines.append(f"| {'GRIDWEAVE CAMPUS ENERGY SIMULATION REPORT':^76} |")
        lines.append(border)
        lines.append(
            f"| Scenario: {metrics.scenario_name:<20} | Slots: {metrics.n_slots:<5} "
            f"| Resolution: {metrics.resolution_minutes:<2}m | Seed: {metrics.seed:<5} |"
        )
        lines.append(border)

        # Key Metrics Grid
        lines.append("| KEY PERFORMANCE INDICATORS                                                    |")
        lines.append(
            f"|  Total Demand: {metrics.total_demand_kwh:8.2f} kWh  "
            f"|  Served:     {metrics.total_served_kwh:8.2f} kWh ({metrics.overall_service_ratio:6.1%})  |"
        )
        lines.append(
            f"|  Deferred:     {metrics.total_deferred_kwh:8.2f} kWh  "
            f"|  Curtailed:  {metrics.total_curtailed_kwh:8.2f} kWh ({curtailment_rate:6.1%})  |"
        )
        lines.append(
            f"|  Jain Fairness:  {metrics.fairness.jains_index_final:6.4f}      "
            f"|  Re-auctions:{metrics.market.re_auction_slots:6d} / {metrics.n_slots:<5d}          |"
        )
        lines.append(
            f"|  Solar Gen:    {metrics.total_solar_kwh:8.2f} kWh  "
            f"|  Battery Discharge: {metrics.total_battery_discharge_kwh:7.2f} kWh          |"
        )
        lines.append(
            f"|  Grid Import:  {metrics.total_grid_kwh:8.2f} kWh  "
            f"|  Events Fired: {metrics.n_events:6d}                     |"
        )
        lines.append(border)

        # Per Building Breakdown
        lines.append("| PER-BUILDING SUMMARY                                                          |")
        lines.append(f"| Total Buildings: {len(result.building_summaries):<60} |")
        lines.append(div)
        lines.append("| Building ID     Demand(kWh) Served (kWh)  Def (kWh)  Curt (kWh)  Svc Ratio      |")
        lines.append(div)
        for b_id, bs in sorted(result.building_summaries.items()):
            lines.append(
                f"| {b_id:<15} {bs.demand_kwh:9.1f}   "
                f"{bs.served_kwh:11.1f}  "
                f"{bs.deferred_kwh:8.1f}  "
                f"{bs.curtailed_kwh:8.1f}  "
                f"{bs.service_ratio:8.1%}      |"
            )

        # Event Log
        if result.event_log:
            lines.append(border)
            lines.append("| EVENT LOG                                                                     |")
            for evt in result.event_log:
                lines.append(
                    f"|  Slot {evt.slot_index:3d} [{evt.time_slot_start.strftime('%Y-%m-%d %H:%M')}] "
                    f"Type: {evt.event_type:<20} Description: {str(evt.description)[:25]:<25} |"
                )

        lines.append(border)
        return "\n".join(lines)

    @staticmethod
    def render_time_series_ascii(result: SimulationResult, width: int = 60, height: int = 10) -> str:
        """Render ASCII bar chart of requested vs served energy over time."""
        slots = result.slots
        if not slots:
            return "No slot data to display."

        req_vals = [sr.requested_kw for sr in slots]
        srv_vals = [sr.served_kw for sr in slots]
        max_val = max(max(req_vals), max(srv_vals), 1.0)

        lines: list[str] = [
            "DEMAND & SUPPLY PROFILE (ASCII CHART)",
            f"Max kW: {max_val:.1f} | Key: # = Served kW, . = Shortfall/Unserved",
            "-" * (width + 12),
        ]

        step_size = max(1, len(slots) // width)
        sampled = slots[::step_size][:width]

        for level in range(height, 0, -1):
            threshold = (level / height) * max_val
            row_chars = []
            for sr in sampled:
                if sr.served_kw >= threshold:
                    row_chars.append("#")
                elif sr.requested_kw >= threshold:
                    row_chars.append(".")
                else:
                    row_chars.append(" ")
            lines.append(f"{threshold:6.1f} kW | " + "".join(row_chars))

        lines.append(" " * 9 + "+" + "-" * len(sampled))
        lines.append(" " * 9 + f"Slot 0 {' ' * (len(sampled) - 12)} Slot {len(slots)-1}")

        return "\n".join(lines)
