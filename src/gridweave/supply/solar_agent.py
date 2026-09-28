"""Autonomous Solar Energy Agent modeling campus photovoltaic generation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, DispatchResult, SourceType, SupplyOffer
from gridweave.supply.forecasting.base import SolarForecast, SolarForecaster
from gridweave.supply.forecasting.weather_aware import WeatherAwareSolarForecaster
from gridweave.supply.profiles.solar import SolarProfile
from gridweave.utils.validation import ValidationError, require_fraction, require_non_empty, require_non_negative


@dataclass
class SolarOperationalState:
    """Historical and instantaneous telemetry for the Solar Agent."""

    total_generated_kwh: float = 0.0
    total_delivered_kwh: float = 0.0
    total_curtailed_kwh: float = 0.0
    peak_generated_kw: float = 0.0
    last_generated_kw: float = 0.0
    last_dispatched_kw: float = 0.0
    last_curtailed_kw: float = 0.0
    dispatch_count: int = 0


class SolarEnergyAgent:
    """Autonomous agent modeling campus solar photovoltaic (PV) generation.

    Responsibilities:
      - Simulates physics-based solar generation governed by solar geometry, irradiance,
        cloud attenuation, panel temperature derating, and inverter efficiency
      - Provides multi-slot lookahead solar forecasting via interchangeable forecasters
      - Enforces zero night generation and never exceeds installed DC plant capacity
      - Generates zero-marginal-cost SupplyOffers for the auction market
      - Executes physical dispatch requests and tracks solar curtailment
      - Handles dynamic weather events (e.g. sudden cloud cover or panel derating)
    """

    def __init__(
        self,
        source_id: str = "solar",
        profile: SolarProfile | None = None,
        forecaster: SolarForecaster | None = None,
        marginal_price: float = 0.0,
        installed_capacity_kw: float = 200.0,
    ) -> None:
        self.source_id = require_non_empty("source_id", source_id)
        self.profile = profile or SolarProfile(installed_capacity_kw=installed_capacity_kw)
        self.forecaster = forecaster or WeatherAwareSolarForecaster(self.profile)
        self.marginal_price = require_non_negative("marginal_price", marginal_price)
        self.state = SolarOperationalState()
        self._cached_generation: dict[TimeSlot, float] = {}

    @property
    def installed_capacity_kw(self) -> float:
        return self.profile.installed_capacity_kw

    def generation_kw(self, slot: TimeSlot) -> float:
        """Compute actual AC solar generation for the given TimeSlot."""
        if slot not in self._cached_generation:
            gen = self.profile.generation_kw(slot)
            self._cached_generation[slot] = gen
        return self._cached_generation[slot]

    def forecast(self, current_slot: TimeSlot, horizon: int = 8) -> SolarForecast:
        """Produce lookahead solar forecast using the configured forecaster."""
        return self.forecaster.forecast(current_slot, horizon=horizon)

    def get_offer(self, slot: TimeSlot) -> SupplyOffer:
        """Generate a valid SupplyOffer for the auction engine.

        Pure query: Does not mutate physical state or advance simulation.
        """
        available = self.generation_kw(slot)
        elev = self.profile.calculate_solar_elevation(slot.start + (slot.end - slot.start) / 2)
        is_daylight = elev > 0.0 and available > 0.001

        constraints = {
            "installed_capacity_kw": self.installed_capacity_kw,
            "cloud_cover": self.profile.cloud_cover,
            "is_daylight": is_daylight,
            "elevation_deg": round(elev, 2),
        }

        return SupplyOffer(
            source_id=self.source_id,
            source_type=SourceType.SOLAR,
            time_slot=slot,
            available_kw=round(available, 4),
            marginal_price=self.marginal_price,
            constraints=constraints,
        )

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        """Execute dispatch instruction for solar generation.

        Any generated power above requested_kw is treated as curtailed solar.
        Updates cumulative generation, delivery, and curtailment ledgers.
        """
        if request.source_id != self.source_id:
            raise ValidationError(
                f"Solar Agent {self.source_id!r} received dispatch for wrong source {request.source_id!r}"
            )

        slot = request.time_slot
        gen_kw = self.generation_kw(slot)
        # Cannot deliver more than generated
        delivered_kw = min(request.requested_kw, gen_kw)
        curtailed_kw = max(0.0, gen_kw - delivered_kw)

        gen_kwh = gen_kw * slot.hours
        del_kwh = delivered_kw * slot.hours
        curt_kwh = curtailed_kw * slot.hours

        # Update telemetry
        self.state.total_generated_kwh += gen_kwh
        self.state.total_delivered_kwh += del_kwh
        self.state.total_curtailed_kwh += curt_kwh
        self.state.peak_generated_kw = max(self.state.peak_generated_kw, gen_kw)
        self.state.last_generated_kw = gen_kw
        self.state.last_dispatched_kw = delivered_kw
        self.state.last_curtailed_kw = curtailed_kw
        self.state.dispatch_count += 1

        # Feed back observation to online forecaster
        self.forecaster.observe(slot, gen_kw, self.profile.cloud_cover)

        remaining_capacity = max(0.0, gen_kw - delivered_kw)

        return DispatchResult(
            source_id=self.source_id,
            time_slot=slot,
            requested_kw=request.requested_kw,
            delivered_kw=min(request.requested_kw, round(delivered_kw, 6)),
            remaining_capacity_kw=round(remaining_capacity, 6),
            state={
                "generated_kw": round(gen_kw, 4),
                "curtailed_kw": round(curtailed_kw, 4),
                "curtailed_kwh": round(curt_kwh, 4),
                "cloud_cover": self.profile.cloud_cover,
                "solar_utilization": round(delivered_kw / gen_kw, 4) if gen_kw > 0 else 1.0,
            },
        )

    def set_cloud_cover(self, cloud_cover: float) -> None:
        """Trigger dynamic cloud event (e.g. 0.8 for sudden thunderstorm)."""
        require_fraction("cloud_cover", cloud_cover)
        self.profile.set_cloud_cover(cloud_cover)
        # Invalidate future slot cache
        self._cached_generation.clear()

    def reset(self) -> None:
        """Reset state and forecaster for reproducible simulation runs."""
        self.state = SolarOperationalState()
        self.forecaster.reset()
        self.profile.set_cloud_cover(0.0)
        self._cached_generation.clear()

    def snapshot(self) -> dict[str, Any]:
        """Telemetry snapshot for P4 coordinator and dashboard."""
        util = (
            self.state.total_delivered_kwh / self.state.total_generated_kwh
            if self.state.total_generated_kwh > 0
            else 1.0
        )
        return {
            "source_id": self.source_id,
            "installed_capacity_kw": self.installed_capacity_kw,
            "total_generated_kwh": round(self.state.total_generated_kwh, 4),
            "total_delivered_kwh": round(self.state.total_delivered_kwh, 4),
            "total_curtailed_kwh": round(self.state.total_curtailed_kwh, 4),
            "peak_generated_kw": round(self.state.peak_generated_kw, 4),
            "solar_utilization": round(util, 4),
            "dispatch_count": self.state.dispatch_count,
        }
