"""Rule-based heuristic battery control strategy (Strategy A)."""
from __future__ import annotations

from dataclasses import dataclass

from gridweave.models.common import TimeSlot
from gridweave.supply.battery_agent import BatteryStorageAgent
from gridweave.supply.profiles.tariff import TariffSchedule


@dataclass(frozen=True)
class RuleBasedAction:
    """Action recommended by the rule-based controller."""

    recommended_offer_kw: float
    recommended_charge_kw: float
    mode: str
    reason: str


class RuleBasedBatteryStrategy:
    """Explainable rule-based baseline strategy for battery scheduling.

    Decision Rules:
      1. Solar Surplus: If solar generation exceeds campus demand, absorb excess solar
         into battery storage up to maximum charge acceptance and SOC limits.
      2. Peak Tariff / High Demand: If tariff is in peak tier and demand exceeds solar,
         discharge battery to shave peak grid import down to reserve floor.
      3. Standard / Off-Peak: Conserve battery storage for upcoming peak hours unless
         a critical energy deficit is imminent.
    """

    def __init__(
        self,
        peak_tariff_threshold: float = 12.0,
        conserve_below_soc: float = 0.35,
    ) -> None:
        self.peak_tariff_threshold = peak_tariff_threshold
        self.conserve_below_soc = conserve_below_soc

    def decide(
        self,
        battery: BatteryStorageAgent,
        slot: TimeSlot,
        predicted_demand_kw: float,
        solar_generation_kw: float,
        tariff_schedule: TariffSchedule,
    ) -> RuleBasedAction:
        """Evaluate current slot and determine optimal battery offer / charge recommendation."""
        current_tariff = tariff_schedule.get_rate(slot)
        is_peak = tariff_schedule.is_peak(slot) or current_tariff >= self.peak_tariff_threshold
        net_demand_kw = predicted_demand_kw - solar_generation_kw

        # Rule 1: Solar Surplus -> Charge battery
        if net_demand_kw < -0.1:
            surplus_kw = abs(net_demand_kw)
            accept_kw = battery.available_charge_kw(slot)
            charge_kw = min(surplus_kw, accept_kw)
            return RuleBasedAction(
                recommended_offer_kw=0.0,
                recommended_charge_kw=round(charge_kw, 4),
                mode="charge",
                reason=f"Solar surplus of {surplus_kw:.1f} kW; absorbing {charge_kw:.1f} kW into battery.",
            )

        # Rule 2: Peak Tariff -> Discharge battery to displace expensive grid imports
        if is_peak and net_demand_kw > 0.1:
            avail_kw = battery.available_discharge_kw(slot, respect_reserve=True)
            dispatch_kw = min(net_demand_kw, avail_kw)
            if dispatch_kw > 0.1:
                return RuleBasedAction(
                    recommended_offer_kw=round(dispatch_kw, 4),
                    recommended_charge_kw=0.0,
                    mode="discharge",
                    reason=(
                        f"Peak tariff ({current_tariff:.1f}/kWh) and net demand {net_demand_kw:.1f} kW; "
                        f"discharging {dispatch_kw:.1f} kW to shave grid peak."
                    ),
                )

        # Rule 3: Low Tariff -> Inexpensive charging if battery is depleted
        if current_tariff <= 6.0 and battery.soc < 0.50 and net_demand_kw < 50.0:
            charge_headroom = battery.available_charge_kw(slot)
            charge_kw = min(20.0, charge_headroom)
            if charge_kw > 0.5:
                return RuleBasedAction(
                    recommended_offer_kw=0.0,
                    recommended_charge_kw=round(charge_kw, 4),
                    mode="charge",
                    reason=f"Off-peak tariff ({current_tariff:.1f}/kWh); pre-charging {charge_kw:.1f} kW.",
                )

        # Rule 4: Standard / Idle -> Conserve storage
        return RuleBasedAction(
            recommended_offer_kw=0.0,
            recommended_charge_kw=0.0,
            mode="idle",
            reason=f"Standard tariff ({current_tariff:.1f}/kWh); conserving storage (SOC {battery.soc:.1%}) for peak.",
        )
