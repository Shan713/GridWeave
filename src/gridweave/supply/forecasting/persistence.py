"""Baseline persistence and seasonal persistence solar forecasters."""
from __future__ import annotations

from enum import Enum

from gridweave.models.common import TimeSlot
from gridweave.supply.forecasting.base import SolarForecast, SolarForecastPoint
from gridweave.supply.profiles.solar import SolarProfile
from gridweave.utils.validation import require_non_negative, require_positive


class PersistenceMode(str, Enum):
    NAIVE_PERSISTENCE = "naive_persistence"  # Next slot = last observed slot
    SEASONAL_PERSISTENCE = "seasonal_persistence"  # Next slot = same slot yesterday (24h lookback)


class PersistenceSolarForecaster:
    """Baseline persistence solar forecaster.

    In NAIVE mode: predicts the most recently observed generation for immediate slots,
    decaying to zero if night approaches according to solar geometry.
    In SEASONAL mode: predicts the generation observed at the exact same time slot in the
    previous day (96 slots ago for 15-minute resolution).
    """

    def __init__(
        self,
        solar_profile: SolarProfile | None = None,
        mode: PersistenceMode = PersistenceMode.NAIVE_PERSISTENCE,
        slots_per_day: int = 96,
    ) -> None:
        self.solar_profile = solar_profile
        self.mode = mode
        self.slots_per_day = int(require_positive("slots_per_day", slots_per_day))
        self.history: list[tuple[TimeSlot, float]] = []
        self._last_observed_kw: float | None = None

    def observe(self, slot: TimeSlot, actual_kw: float, cloud_cover: float | None = None) -> None:
        require_non_negative("actual_kw", actual_kw)
        self.history.append((slot, actual_kw))
        self._last_observed_kw = actual_kw
        # Cap history to 7 days
        max_len = self.slots_per_day * 7
        if len(self.history) > max_len:
            self.history = self.history[-max_len:]

    def forecast(self, current_slot: TimeSlot, horizon: int = 8) -> SolarForecast:
        require_positive("horizon", horizon)
        points: list[SolarForecastPoint] = []
        target_slot = current_slot

        for h in range(horizon):
            clear_sky_kw = (
                self.solar_profile.generation_kw(target_slot, cloud_override=0.0, apply_stochastic_noise=False)
                if self.solar_profile
                else 0.0
            )

            # Determine prediction based on mode
            if self.mode == PersistenceMode.SEASONAL_PERSISTENCE:
                pred_kw = self._seasonal_lookup(target_slot, clear_sky_kw)
                confidence = 0.70
            else:
                pred_kw = self._naive_lookup(target_slot, h, clear_sky_kw)
                confidence = max(0.20, 0.90 - 0.08 * h)

            # Nighttime constraint: if clear sky is zero, generation must be zero
            if clear_sky_kw <= 0.001:
                pred_kw = 0.0

            # Never exceed installed capacity if profile is provided
            if self.solar_profile:
                pred_kw = min(pred_kw, self.solar_profile.installed_capacity_kw)

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

    def _naive_lookup(self, target_slot: TimeSlot, step_ahead: int, clear_sky_kw: float) -> float:
        if self._last_observed_kw is None:
            return clear_sky_kw * 0.5  # Prior guess

        # If clear sky is 0, sun is down
        if clear_sky_kw <= 0.001:
            return 0.0

        # Naive persistence with clear-sky scaling so night/day transitions do not persist noon sun
        if self.solar_profile and self.history:
            last_slot, last_kw = self.history[-1]
            last_cs = self.solar_profile.generation_kw(last_slot, cloud_override=0.0, apply_stochastic_noise=False)
            if last_cs > 0.01:
                clearness_index = min(1.2, last_kw / last_cs)
                return clear_sky_kw * clearness_index

        return self._last_observed_kw

    def _seasonal_lookup(self, target_slot: TimeSlot, clear_sky_kw: float) -> float:
        # Look back 24h (or nearest available day in history)
        if len(self.history) >= self.slots_per_day:
            # History is stored sequentially
            return self.history[-self.slots_per_day][1]
        elif self.history:
            return self.history[-1][1]
        return clear_sky_kw * 0.5

    def reset(self) -> None:
        self.history.clear()
        self._last_observed_kw = None
