"""Advanced time-of-day and weather-aware solar generation forecasters."""
from __future__ import annotations

import math
from typing import Sequence

from gridweave.models.common import TimeSlot
from gridweave.supply.forecasting.base import SolarForecast, SolarForecastPoint
from gridweave.supply.profiles.solar import SolarProfile
from gridweave.utils.validation import require_positive


class TimeOfDaySolarForecaster:
    """Historical time-of-day empirical forecaster.

    Maintains an exponential moving average (EMA) of solar generation for each
    of the 96 daily time slots.
    """

    def __init__(
        self,
        solar_profile: SolarProfile | None = None,
        alpha: float = 0.3,
        slots_per_day: int = 96,
    ) -> None:
        self.solar_profile = solar_profile
        self.alpha = alpha
        self.slots_per_day = int(require_positive("slots_per_day", slots_per_day))
        # slot_of_day (0..96) -> estimated kw
        self.slot_profile: dict[int, float] = {}

    def _slot_index(self, slot: TimeSlot) -> int:
        dt = slot.start
        return (dt.hour * 60 + dt.minute) // slot.duration_minutes

    def observe(self, slot: TimeSlot, actual_kw: float, cloud_cover: float | None = None) -> None:
        idx = self._slot_index(slot)
        if idx in self.slot_profile:
            self.slot_profile[idx] = self.alpha * actual_kw + (1.0 - self.alpha) * self.slot_profile[idx]
        else:
            self.slot_profile[idx] = actual_kw

    def forecast(self, current_slot: TimeSlot, horizon: int = 8) -> SolarForecast:
        require_positive("horizon", horizon)
        points: list[SolarForecastPoint] = []
        target_slot = current_slot

        for h in range(horizon):
            idx = self._slot_index(target_slot)
            clear_sky_kw = (
                self.solar_profile.generation_kw(target_slot, cloud_override=0.0, apply_stochastic_noise=False)
                if self.solar_profile
                else 0.0
            )

            if clear_sky_kw <= 0.001:
                pred_kw = 0.0
                confidence = 0.99
            elif idx in self.slot_profile:
                pred_kw = min(clear_sky_kw, self.slot_profile[idx])
                confidence = max(0.40, 0.85 - 0.05 * h)
            else:
                pred_kw = clear_sky_kw * 0.75  # Default empirical prior
                confidence = 0.50

            points.append(
                SolarForecastPoint(
                    slot=target_slot,
                    predicted_kw=round(max(0.0, pred_kw), 4),
                    confidence=round(confidence, 4),
                    clear_sky_kw=round(clear_sky_kw, 4),
                )
            )
            target_slot = target_slot.next()

        return SolarForecast(generated_at=current_slot, points=tuple(points))

    def reset(self) -> None:
        self.slot_profile.clear()


class WeatherAwareSolarForecaster:
    """Physics-informed, weather-aware solar forecaster.

    Combines the physical clear-sky solar model with:
      1. Real-time atmospheric clearness index tracking (k_t = P_real / P_clearsky)
      2. Exponential smoothing of cloud transmission trend
      3. Lookahead cloud forecast integration (if available from meteorology)
      4. Physical nighttime and plant capacity clipping
    """

    def __init__(
        self,
        solar_profile: SolarProfile,
        smoothing_factor: float = 0.4,
    ) -> None:
        self.solar_profile = solar_profile
        self.smoothing_factor = smoothing_factor
        self._current_clearness_index: float = 0.85
        self._recent_observations: list[tuple[TimeSlot, float, float | None]] = []

    def observe(self, slot: TimeSlot, actual_kw: float, cloud_cover: float | None = None) -> None:
        self._recent_observations.append((slot, actual_kw, cloud_cover))
        # Keep last 96 observations
        if len(self._recent_observations) > 96:
            self._recent_observations = self._recent_observations[-96:]

        # Compute clear-sky potential for this slot
        cs_kw = self.solar_profile.generation_kw(slot, cloud_override=0.0, apply_stochastic_noise=False)
        if cs_kw > 1.0:
            observed_index = max(0.05, min(1.15, actual_kw / cs_kw))
            # Smooth clearness index
            self._current_clearness_index = (
                self.smoothing_factor * observed_index
                + (1.0 - self.smoothing_factor) * self._current_clearness_index
            )

    def forecast(
        self,
        current_slot: TimeSlot,
        horizon: int = 8,
        cloud_outlook: Sequence[float] | None = None,
    ) -> SolarForecast:
        require_positive("horizon", horizon)
        points: list[SolarForecastPoint] = []
        target_slot = current_slot

        for h in range(horizon):
            cs_kw = self.solar_profile.generation_kw(target_slot, cloud_override=0.0, apply_stochastic_noise=False)

            if cs_kw <= 0.001:
                # Night: guaranteed 0 kW
                pred_kw = 0.0
                confidence = 1.0
            else:
                if cloud_outlook is not None and h < len(cloud_outlook):
                    # Weather-aware forecast using external cloud outlook
                    cloud = cloud_outlook[h]
                    cloud_trans = max(0.10, 1.0 - 0.75 * (cloud ** 2.5))
                    pred_kw = cs_kw * cloud_trans
                    confidence = max(0.50, 0.92 - 0.04 * h)
                else:
                    # Persistence of clear-sky index with mean reversion to nominal (0.85)
                    decay = math.exp(-0.25 * h)
                    k_effective = decay * self._current_clearness_index + (1.0 - decay) * 0.85
                    pred_kw = cs_kw * k_effective
                    confidence = max(0.40, 0.90 - 0.06 * h)

            # Never exceed installed plant capacity
            pred_kw = min(self.solar_profile.installed_capacity_kw, max(0.0, pred_kw))

            points.append(
                SolarForecastPoint(
                    slot=target_slot,
                    predicted_kw=round(pred_kw, 4),
                    confidence=round(confidence, 4),
                    clear_sky_kw=round(cs_kw, 4),
                )
            )
            target_slot = target_slot.next()

        return SolarForecast(generated_at=current_slot, points=tuple(points))

    def reset(self) -> None:
        self._current_clearness_index = 0.85
        self._recent_observations.clear()
