"""Coordinator package.

Public API::

    from gridweave.coordinator import Coordinator, Scenario, get_scenario, SCENARIOS
    from gridweave.coordinator import MetricsAggregator, CampusMetrics
    from gridweave.coordinator import SimulationResult, SlotRecord, BuildingSummary
    from gridweave.coordinator import ScheduledEvent, NORMAL, SOLAR_DROP, GRID_OUTAGE
    from gridweave.coordinator import CliDashboard, serve_dashboard
"""
from __future__ import annotations

from gridweave.coordinator.cli_dashboard import CliDashboard
from gridweave.coordinator.coordinator import Coordinator, CoordinatorError, SettlementError
from gridweave.coordinator.metrics import CampusMetrics, FairnessMetrics, MarketMetrics, MetricsAggregator
from gridweave.coordinator.modes import DEFAULT_MODE, MODES, Mode, RunOutput, get_mode, run_simulation
from gridweave.coordinator.records import BuildingSummary, EventRecord, SimulationResult, SlotRecord
from gridweave.coordinator.scenarios import (
    BATTERY_DERATE,
    BATTERY_OUTAGE,
    GRID_OUTAGE,
    MIXED_STRESS,
    NORMAL,
    SCARCITY,
    SCENARIOS,
    SOLAR_DROP,
    TARIFF_SPIKE,
    Scenario,
    ScheduledEvent,
    get_scenario,
)
from gridweave.coordinator.web_dashboard import serve_dashboard

__all__ = [
    # Coordinator
    "Coordinator",
    "CoordinatorError",
    "SettlementError",
    # Records
    "SimulationResult",
    "SlotRecord",
    "BuildingSummary",
    "EventRecord",
    # Scenarios
    "Scenario",
    "ScheduledEvent",
    "SCENARIOS",
    "get_scenario",
    "NORMAL",
    "SOLAR_DROP",
    "GRID_OUTAGE",
    "BATTERY_OUTAGE",
    "BATTERY_DERATE",
    "TARIFF_SPIKE",
    "SCARCITY",
    "MIXED_STRESS",
    # Modes (before -> after comparison)
    "Mode",
    "MODES",
    "DEFAULT_MODE",
    "get_mode",
    "run_simulation",
    "RunOutput",
    # Metrics
    "MetricsAggregator",
    "CampusMetrics",
    "MarketMetrics",
    "FairnessMetrics",
    # Dashboards
    "CliDashboard",
    "serve_dashboard",
]
