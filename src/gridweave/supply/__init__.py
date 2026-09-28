"""GridWeave Energy Supply Subsystem (Person 3).

Provides autonomous Grid, Solar, and Battery energy agents, solar generation
and forecasting models, battery optimization and degradation tracking,
and the production CampusSupplyProvider.
"""
from gridweave.supply.accounting import ConservationViolation, SlotEnergyRecord, SupplyAccountant
from gridweave.supply.battery_agent import (
    BatteryOperatingMode,
    BatteryOperationalState,
    BatteryReservePolicy,
    BatteryStorageAgent,
)
from gridweave.supply.constraints import PhysicalConstraintViolation, validate_source_dispatch, validate_supply_offer
from gridweave.supply.dispatcher import DispatchExecutionError, SupplyDispatcher
from gridweave.supply.events import SupplyEventHandler, SupplyEventRecord, SupplyEventType
from gridweave.supply.forecasting import (
    PersistenceMode,
    PersistenceSolarForecaster,
    SolarForecast,
    SolarForecaster,
    SolarForecastPoint,
    TimeOfDaySolarForecaster,
    WeatherAwareSolarForecaster,
    calculate_mae,
    calculate_rmse,
    rolling_origin_evaluation,
)
from gridweave.supply.grid_agent import GridOperationalState, GridSupplyAgent
from gridweave.supply.metrics import SupplyMetrics, SupplyMetricsCalculator
from gridweave.supply.optimization import (
    BuildingDemandOutlookAdapter,
    DemandOutlookProvider,
    ForecastAwareBatteryStrategy,
    ForecastAwarePlan,
    OptimizationPlanPoint,
    RuleBasedAction,
    RuleBasedBatteryStrategy,
    ScheduleCostBreakdown,
    evaluate_schedule_cost,
)
from gridweave.supply.profiles import GridProfile, OutageWindow, SolarProfile, TariffPeriod, TariffSchedule
from gridweave.supply.provider import CampusSupplyProvider
from gridweave.supply.solar_agent import SolarEnergyAgent, SolarOperationalState

__all__ = [
    "BatteryOperatingMode",
    "BatteryOperationalState",
    "BatteryReservePolicy",
    "BatteryStorageAgent",
    "BuildingDemandOutlookAdapter",
    "CampusSupplyProvider",
    "ConservationViolation",
    "DispatchExecutionError",
    "DemandOutlookProvider",
    "ForecastAwareBatteryStrategy",
    "ForecastAwarePlan",
    "GridOperationalState",
    "GridProfile",
    "GridSupplyAgent",
    "OptimizationPlanPoint",
    "OutageWindow",
    "PersistenceMode",
    "PersistenceSolarForecaster",
    "PhysicalConstraintViolation",
    "RuleBasedAction",
    "RuleBasedBatteryStrategy",
    "ScheduleCostBreakdown",
    "SlotEnergyRecord",
    "SolarEnergyAgent",
    "SolarForecast",
    "SolarForecastPoint",
    "SolarForecaster",
    "SolarOperationalState",
    "SolarProfile",
    "SupplyAccountant",
    "SupplyDispatcher",
    "SupplyEventHandler",
    "SupplyEventRecord",
    "SupplyEventType",
    "SupplyMetrics",
    "SupplyMetricsCalculator",
    "TariffPeriod",
    "TariffSchedule",
    "TimeOfDaySolarForecaster",
    "WeatherAwareSolarForecaster",
    "calculate_mae",
    "calculate_rmse",
    "evaluate_schedule_cost",
    "rolling_origin_evaluation",
    "validate_source_dispatch",
    "validate_supply_offer",
]
