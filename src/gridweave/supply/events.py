"""Dynamic supply events handler coordinating stochastic environmental and grid events."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.grid_agent import GridSupplyAgent
from gridweave.supply.solar_agent import SolarEnergyAgent
from gridweave.utils.validation import require_fraction, require_non_negative


class SupplyEventType(str, Enum):
    GRID_OUTAGE = "grid_outage"
    GRID_RESTORATION = "grid_restoration"
    SOLAR_DROP = "solar_drop"
    SOLAR_RECOVERY = "solar_recovery"
    TARIFF_SPIKE = "tariff_spike"
    TARIFF_NORMALIZATION = "tariff_normalization"
    BATTERY_DERATE = "battery_derate"
    BATTERY_OUTAGE = "battery_outage"
    BATTERY_RESTORATION = "battery_restoration"
    EMERGENCY_RESERVE_RELEASE = "emergency_reserve_release"


@dataclass(frozen=True)
class SupplyEventRecord:
    event_type: SupplyEventType
    description: str
    parameters: dict[str, Any]


class SupplyEventHandler:
    """Dispatches dynamic physical events to the respective energy source agents."""

    def __init__(
        self,
        grid_agents: list[GridSupplyAgent],
        solar_agents: list[SolarEnergyAgent],
        battery_agents: list[BatteryStorageAgent],
    ) -> None:
        self.grid_agents = grid_agents
        self.solar_agents = solar_agents
        self.battery_agents = battery_agents
        self.event_log: list[SupplyEventRecord] = []

    def trigger_grid_outage(self, source_id: str | None = None) -> SupplyEventRecord:
        """Simulate unexpected grid blackout event."""
        targets = [g for g in self.grid_agents if source_id is None or g.source_id == source_id]
        for g in targets:
            g.trigger_outage()
        rec = SupplyEventRecord(
            event_type=SupplyEventType.GRID_OUTAGE,
            description="Grid blackout triggered; import capacity set to 0.0 kW.",
            parameters={"source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def trigger_grid_restoration(self, source_id: str | None = None) -> SupplyEventRecord:
        """Restore grid following an outage."""
        targets = [g for g in self.grid_agents if source_id is None or g.source_id == source_id]
        for g in targets:
            g.restore_grid()
        rec = SupplyEventRecord(
            event_type=SupplyEventType.GRID_RESTORATION,
            description="Grid capacity fully restored.",
            parameters={"source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def trigger_solar_drop(self, cloud_cover: float = 0.85, source_id: str | None = None) -> SupplyEventRecord:
        """Simulate sudden overcast/thunderstorm cloud event."""
        require_fraction("cloud_cover", cloud_cover)
        targets = [s for s in self.solar_agents if source_id is None or s.source_id == source_id]
        for s in targets:
            s.set_cloud_cover(cloud_cover)
        rec = SupplyEventRecord(
            event_type=SupplyEventType.SOLAR_DROP,
            description=f"Sudden cloud cover event ({cloud_cover:.0%}); solar generation attenuated.",
            parameters={"cloud_cover": cloud_cover, "source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def trigger_solar_recovery(self, source_id: str | None = None) -> SupplyEventRecord:
        """Clear cloud event back to clear sky."""
        targets = [s for s in self.solar_agents if source_id is None or s.source_id == source_id]
        for s in targets:
            s.set_cloud_cover(0.0)
        rec = SupplyEventRecord(
            event_type=SupplyEventType.SOLAR_RECOVERY,
            description="Cloud cover cleared; clear-sky solar generation restored.",
            parameters={"source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def trigger_tariff_spike(self, multiplier: float = 2.5, source_id: str | None = None) -> SupplyEventRecord:
        """Simulate wholesale grid price spike."""
        require_non_negative("multiplier", multiplier)
        targets = [g for g in self.grid_agents if source_id is None or g.source_id == source_id]
        for g in targets:
            g.set_tariff_multiplier(multiplier)
        rec = SupplyEventRecord(
            event_type=SupplyEventType.TARIFF_SPIKE,
            description=f"Grid electricity price spike activated (multiplier: {multiplier:.2f}x).",
            parameters={"multiplier": multiplier, "source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def trigger_tariff_normalization(self, source_id: str | None = None) -> SupplyEventRecord:
        """Reset tariff multiplier back to nominal."""
        targets = [g for g in self.grid_agents if source_id is None or g.source_id == source_id]
        for g in targets:
            g.set_tariff_multiplier(1.0)
        rec = SupplyEventRecord(
            event_type=SupplyEventType.TARIFF_NORMALIZATION,
            description="Grid tariff restored to nominal schedule.",
            parameters={"source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def trigger_emergency_reserve_release(self, source_id: str | None = None) -> SupplyEventRecord:
        """Authorize battery discharge down to physical minimum SOC during grid emergency."""
        targets = [b for b in self.battery_agents if source_id is None or b.source_id == source_id]
        for b in targets:
            b.release_emergency_reserve(True)
        rec = SupplyEventRecord(
            event_type=SupplyEventType.EMERGENCY_RESERVE_RELEASE,
            description="Emergency reserve release authorized for battery storage.",
            parameters={"source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def trigger_battery_derate(self, max_discharge_kw: float, source_id: str | None = None) -> SupplyEventRecord:
        """Apply a temporary discharge-power derating to selected batteries."""
        require_non_negative("max_discharge_kw", max_discharge_kw)
        targets = [b for b in self.battery_agents if source_id is None or b.source_id == source_id]
        for battery in targets:
            battery.set_derated_discharge_kw(max_discharge_kw)
        rec = SupplyEventRecord(
            event_type=SupplyEventType.BATTERY_RESTORATION,
            description=f"Battery discharge power derated to {max_discharge_kw:.2f} kW.",
            parameters={"max_discharge_kw": max_discharge_kw, "source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def trigger_battery_outage(self, source_id: str | None = None) -> SupplyEventRecord:
        """Take selected batteries out of service."""
        targets = [b for b in self.battery_agents if source_id is None or b.source_id == source_id]
        for battery in targets:
            battery.set_unavailable(True)
        rec = SupplyEventRecord(
            event_type=SupplyEventType.BATTERY_OUTAGE,
            description="Battery storage unavailable for dispatch.",
            parameters={"source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def trigger_battery_restoration(self, source_id: str | None = None) -> SupplyEventRecord:
        """Restore selected batteries after an outage, preserving any derating."""
        targets = [b for b in self.battery_agents if source_id is None or b.source_id == source_id]
        for battery in targets:
            battery.set_unavailable(False)
        rec = SupplyEventRecord(
            event_type=SupplyEventType.BATTERY_DERATE,
            description="Battery storage restored to service.",
            parameters={"source_id": source_id},
        )
        self.event_log.append(rec)
        return rec

    def reset(self) -> None:
        self.event_log.clear()
