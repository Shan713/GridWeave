"""Mock supply sources: stand-ins for P3's grid, solar and battery agents.

Deliberately simple test doubles implementing the offer/dispatch contract.
They are **not** P3's models (no tariffs by time of day, no irradiance
model, no battery charging, no efficiency losses).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from gridweave.models.common import TimeSlot
from gridweave.models.supply import DispatchRequest, DispatchResult, SourceType, SupplyOffer
from gridweave.utils.validation import ValidationError, require_fraction, require_non_negative, require_positive


class MockGrid:
    """Fixed import capacity at a flat price, with optional time-of-day shortage windows."""

    def __init__(self, capacity_kw: float, shortage_windows: Sequence[Sequence[float]] = (),
                 price_per_kwh: float = 10.0, source_id: str = "grid") -> None:
        self.source_id = source_id
        self.capacity_kw = require_non_negative("capacity_kw", capacity_kw)
        self.price_per_kwh = require_non_negative("price_per_kwh", price_per_kwh)
        self.shortage_windows = []
        for w in shortage_windows:
            start, end, factor = (float(x) for x in w)
            if not (0 <= start < end <= 24 and factor >= 0):
                raise ValidationError(f"invalid shortage window {w!r}")
            self.shortage_windows.append((start, end, factor))

    def available_kw(self, slot: TimeSlot) -> float:
        hour = slot.start.hour + slot.start.minute / 60
        factor = min([f for s, e, f in self.shortage_windows if s <= hour < e], default=1.0)
        return self.capacity_kw * factor

    def offer(self, slot: TimeSlot) -> SupplyOffer:
        return SupplyOffer(self.source_id, SourceType.GRID, slot, self.available_kw(slot), self.price_per_kwh)

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        available = self.available_kw(request.time_slot)
        delivered = min(request.requested_kw, available)
        return DispatchResult(self.source_id, request.time_slot, request.requested_kw, delivered,
                              available - delivered)


class MockSolar:
    """Zero-marginal-cost PV with a triangular daily profile between sunrise and sunset."""

    def __init__(self, peak_kw: float, sunrise: float = 6.5, sunset: float = 18.5, source_id: str = "solar") -> None:
        self.source_id = source_id
        self.peak_kw = require_non_negative("peak_kw", peak_kw)
        if not 0 <= sunrise < sunset <= 24:
            raise ValidationError("need 0 <= sunrise < sunset <= 24")
        self.sunrise, self.sunset = sunrise, sunset

    def available_kw(self, slot: TimeSlot) -> float:
        hour = slot.start.hour + slot.start.minute / 60 + slot.duration_minutes / 120  # slot midpoint
        noon, half = (self.sunrise + self.sunset) / 2, (self.sunset - self.sunrise) / 2
        return self.peak_kw * max(0.0, 1.0 - abs(hour - noon) / half)

    def offer(self, slot: TimeSlot) -> SupplyOffer:
        return SupplyOffer(self.source_id, SourceType.SOLAR, slot, self.available_kw(slot), 0.0)

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        available = self.available_kw(request.time_slot)
        delivered = min(request.requested_kw, available)
        return DispatchResult(self.source_id, request.time_slot, request.requested_kw, delivered,
                              available - delivered)


class MockBattery:
    """Discharge-only battery with a state of charge (SOC). No charging, no losses."""

    def __init__(self, capacity_kwh: float, max_power_kw: float, soc: float = 0.8, min_soc: float = 0.2,
                 price_per_kwh: float = 7.0, source_id: str = "battery") -> None:
        self.source_id = source_id
        self.capacity_kwh = require_positive("capacity_kwh", capacity_kwh)
        self.max_power_kw = require_non_negative("max_power_kw", max_power_kw)
        self.soc = require_fraction("soc", soc)
        self.min_soc = require_fraction("min_soc", min_soc)
        self.price_per_kwh = require_non_negative("price_per_kwh", price_per_kwh)

    def available_kw(self, slot: TimeSlot) -> float:
        usable_kwh = max(0.0, self.soc - self.min_soc) * self.capacity_kwh
        return min(self.max_power_kw, usable_kwh / slot.hours)

    def offer(self, slot: TimeSlot) -> SupplyOffer:
        return SupplyOffer(self.source_id, SourceType.BATTERY, slot, self.available_kw(slot), self.price_per_kwh,
                           constraints={"soc": self.soc, "min_soc": self.min_soc, "capacity_kwh": self.capacity_kwh})

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        available = self.available_kw(request.time_slot)
        delivered = min(request.requested_kw, available)
        self.soc -= delivered * request.time_slot.hours / self.capacity_kwh
        return DispatchResult(self.source_id, request.time_slot, request.requested_kw, delivered,
                              self.available_kw(request.time_slot), {"soc": round(self.soc, 6)})


class MockSupply:
    """Aggregates mock sources behind the :class:`gridweave.interfaces.SupplyProvider` protocol."""

    def __init__(self, *sources: Any) -> None:
        ids = [s.source_id for s in sources]
        if len(ids) != len(set(ids)):
            raise ValidationError(f"duplicate source ids {ids}")
        self.sources = {s.source_id: s for s in sources}

    def offers(self, time_slot: TimeSlot) -> list[SupplyOffer]:
        return [s.offer(time_slot) for s in self.sources.values()]

    def dispatch(self, requests: Iterable[DispatchRequest]) -> list[DispatchResult]:
        results = []
        for r in requests:
            if r.source_id not in self.sources:
                raise ValidationError(f"dispatch to unknown source {r.source_id!r}")
            results.append(self.sources[r.source_id].dispatch(r))
        return results

    @classmethod
    def from_config(cls, supply: Mapping[str, Any]) -> "MockSupply":
        """Build from the ``"supply"`` section of a campus config."""
        makers = {"grid": MockGrid, "solar": MockSolar, "battery": MockBattery}
        sources = []
        for entry in supply.get("sources", []):
            entry = dict(entry)
            kind = entry.pop("type")
            if kind not in makers:
                raise ValidationError(f"unknown mock source type {kind!r}")
            sources.append(makers[kind](**entry))
        if not sources:
            raise ValidationError("supply config needs at least one source")
        return cls(*sources)
