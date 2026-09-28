"""Solar photovoltaic generation profile, clear-sky irradiance, and atmospheric modeling.

Academic simulation model combining:
  1. Solar geometric position (declination, hour angle, zenith/elevation)
  2. Clear-sky Global Horizontal Irradiance (GHI)
  3. Cloud attenuation and atmospheric transmissivity
  4. Panel temperature derating and inverter efficiency
  5. Deterministic seeded stochastic micro-variability
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gridweave.models.common import TimeSlot
from gridweave.utils.validation import ValidationError, require_fraction, require_non_negative


@dataclass
class SolarProfile:
    """Configurable solar plant generation model.

    Parameters
    ----------
    installed_capacity_kw : float
        DC plant rating under standard test conditions (STC).
    latitude_deg : float
        Geographic latitude in degrees (default 12.97° N, e.g. Bangalore campus).
    longitude_deg : float
        Geographic longitude in degrees (default 77.59° E).
    standard_meridian_deg : float
        Local standard time meridian in degrees (default 82.5° E for IST UTC+5:30).
    sunrise_hour : float
        Nominal local sunrise time (e.g. 6.0 = 06:00).
    sunset_hour : float
        Nominal local sunset time (e.g. 18.5 = 18:30).
    system_efficiency : float
        Overall DC-to-AC derating factor accounting for soiling, inverter losses, and wiring.
    cloud_cover : float
        Cloud cover fraction in [0.0, 1.0], where 0.0 is clear sky and 1.0 is heavy overcast.
    seed : int
        Deterministic random seed for stochastic variability.
    """

    installed_capacity_kw: float
    latitude_deg: float = 12.97
    longitude_deg: float = 77.59
    standard_meridian_deg: float = 82.5
    sunrise_hour: float = 6.0
    sunset_hour: float = 18.5
    system_efficiency: float = 0.85
    cloud_cover: float = 0.0
    seed: int = 42

    def __post_init__(self) -> None:
        require_non_negative("installed_capacity_kw", self.installed_capacity_kw)
        require_fraction("system_efficiency", self.system_efficiency)
        require_fraction("cloud_cover", self.cloud_cover)
        if not (0.0 <= self.sunrise_hour < self.sunset_hour <= 24.0):
            raise ValidationError(
                f"need 0 <= sunrise < sunset <= 24, got [{self.sunrise_hour}, {self.sunset_hour}]"
            )

    def calculate_solar_elevation(self, dt: datetime) -> float:
        """Calculate solar elevation angle (degrees above horizon).

        Uses solar declination and equation of time approximation.
        Returns 0.0 if the sun is below the horizon.
        """
        day_of_year = dt.timetuple().tm_yday
        # Solar declination angle delta (Spencer formula approximation)
        b = (2 * math.pi / 365.0) * (day_of_year - 1)
        declination_rad = (
            0.006918
            - 0.399912 * math.cos(b)
            + 0.070257 * math.sin(b)
            - 0.006758 * math.cos(2 * b)
            + 0.000907 * math.sin(2 * b)
        )

        # Equation of time (minutes)
        eot_minutes = 229.18 * (
            0.000075
            + 0.001868 * math.cos(b)
            - 0.032077 * math.sin(b)
            - 0.014615 * math.cos(2 * b)
            - 0.040849 * math.sin(2 * b)
        )

        # Solar time
        local_time_minutes = dt.hour * 60.0 + dt.minute + dt.second / 60.0
        time_offset = eot_minutes + 4.0 * (self.longitude_deg - self.standard_meridian_deg)
        solar_time_minutes = local_time_minutes + time_offset
        solar_hour = (solar_time_minutes / 60.0) % 24.0

        # Hour angle omega (-180° at midnight, 0° at solar noon)
        omega_deg = 15.0 * (solar_hour - 12.0)
        omega_rad = math.radians(omega_deg)
        lat_rad = math.radians(self.latitude_deg)

        # sin(elevation) = sin(lat)*sin(dec) + cos(lat)*cos(dec)*cos(omega)
        sin_elev = (
            math.sin(lat_rad) * math.sin(declination_rad)
            + math.cos(lat_rad) * math.cos(declination_rad) * math.cos(omega_rad)
        )

        if sin_elev <= 0:
            return 0.0

        elevation_rad = math.asin(sin_elev)
        return math.degrees(elevation_rad)

    def clear_sky_irradiance(self, elevation_deg: float) -> float:
        """Calculate clear-sky GHI (W/m^2) using simplified Haurwitz / Meinel formulation.

        Returns 0.0 when elevation <= 0.
        """
        if elevation_deg <= 0.0:
            return 0.0

        zenith_deg = 90.0 - elevation_deg
        zenith_rad = math.radians(zenith_deg)
        cos_z = max(0.0, math.cos(zenith_rad))

        if cos_z <= 0.001:
            return 0.0

        # Solar constant I0 approx 1367 W/m^2
        # Simplified clear sky transmission: I = I0 * cos_z * exp(-0.0034 * (90-elev)^1.1)
        # Yields ~950-1000 W/m^2 at zenith on a clear day
        ghi = 1098.0 * cos_z * math.exp(-0.057 / cos_z)
        return max(0.0, ghi)

    def generation_kw(
        self,
        slot: TimeSlot,
        cloud_override: float | None = None,
        apply_stochastic_noise: bool = True,
    ) -> float:
        """Calculate AC solar generation (kW) for the given TimeSlot.

        Guaranteed properties:
          - 0.0 kW during nighttime (elevation <= 0 or outside sunrise/sunset window)
          - Never exceeds installed_capacity_kw
          - Deterministic for identical slot and seed
        """
        # Evaluate at slot midpoint for average slot power
        mid_dt = slot.start + (slot.end - slot.start) / 2
        mid_hour = mid_dt.hour + mid_dt.minute / 60.0 + mid_dt.second / 3600.0

        # Hard astronomical night bounds
        if mid_hour < self.sunrise_hour or mid_hour > self.sunset_hour:
            return 0.0

        elev = self.calculate_solar_elevation(mid_dt)
        if elev <= 0.0:
            return 0.0

        # Clear-sky irradiance (W/m^2) normalized by STC irradiance (1000 W/m^2)
        ghi = self.clear_sky_irradiance(elev)
        norm_irradiance = min(1.0, ghi / 1000.0)

        # Cloud attenuation factor (Kasten & Czeplak model approximation: (1 - 0.75 * cloud^3.4))
        cloud = self.cloud_cover if cloud_override is None else cloud_override
        require_fraction("cloud_cover", cloud)
        cloud_transmission = max(0.10, 1.0 - 0.75 * (cloud ** 2.5))

        # Temperature / heat derating: panels lose efficiency when ambient/irradiance peaks
        # Modeled as a small midday derate factor (0.95 - 1.0)
        temp_derate = 1.0 - 0.05 * norm_irradiance

        # Base AC power (kW)
        base_kw = (
            self.installed_capacity_kw
            * norm_irradiance
            * cloud_transmission
            * self.system_efficiency
            * temp_derate
        )

        # Controlled stochastic variability (seeded deterministic pseudorandom)
        if apply_stochastic_noise and base_kw > 0.01:
            slot_id = int(slot.start.timestamp()) // (slot.duration_minutes * 60)
            rng = random.Random(self.seed + slot_id)
            noise_factor = 1.0 + rng.uniform(-0.04, 0.04) * (1.0 + cloud)
            base_kw *= noise_factor

        # Hard physical clamp: [0.0, installed_capacity_kw]
        return round(max(0.0, min(self.installed_capacity_kw, base_kw)), 4)

    def set_cloud_cover(self, cloud_cover: float) -> None:
        """Dynamically update cloud cover (for weather events)."""
        require_fraction("cloud_cover", cloud_cover)
        self.cloud_cover = float(cloud_cover)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed_capacity_kw": self.installed_capacity_kw,
            "latitude_deg": self.latitude_deg,
            "longitude_deg": self.longitude_deg,
            "sunrise_hour": self.sunrise_hour,
            "sunset_hour": self.sunset_hour,
            "system_efficiency": self.system_efficiency,
            "cloud_cover": self.cloud_cover,
            "seed": self.seed,
        }
