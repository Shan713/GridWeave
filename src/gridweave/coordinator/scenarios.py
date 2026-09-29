"""Scenario and event scheduling system for Coordinator.

A :class:`Scenario` bundles simulation parameters (buildings, seed, duration)
with a list of :class:`ScheduledEvent` objects that instruct the coordinator
to call specific supply-event APIs at nominated slot indices.

Predefined scenarios are available in :data:`SCENARIOS` and can be retrieved
by name via :func:`get_scenario`.  Custom scenarios are built by constructing
:class:`Scenario` directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gridweave.utils.validation import ValidationError


# ---------------------------------------------------------------------------
# Default supply configuration for scenarios
# ---------------------------------------------------------------------------

_DEFAULT_SUPPLY_CFG: dict[str, Any] = {
    "sources": [
        {"type": "grid",    "source_id": "grid_main",    "nominal_capacity_kw": 400.0},
        {"type": "solar",   "source_id": "solar_roof",   "installed_capacity_kw": 200.0},
        {"type": "battery", "source_id": "battery_main", "capacity_kwh": 150.0,
         "initial_soc": 0.70},
    ]
}

_SCARCITY_SUPPLY_CFG: dict[str, Any] = {
    "sources": [
        {"type": "grid",    "source_id": "grid_main",    "nominal_capacity_kw": 150.0},
        {"type": "solar",   "source_id": "solar_roof",   "installed_capacity_kw": 60.0},
        {"type": "battery", "source_id": "battery_main", "capacity_kwh": 50.0,
         "initial_soc": 0.50},
    ]
}


# ---------------------------------------------------------------------------
# ScheduledEvent
# ---------------------------------------------------------------------------

#: All event types supported by P3's CampusSupplyProvider.events API.
VALID_EVENT_TYPES: frozenset[str] = frozenset({
    "grid_outage",
    "grid_restoration",
    "solar_drop",
    "solar_recovery",
    "tariff_spike",
    "tariff_normalization",
    "battery_derate",
    "battery_outage",
    "battery_restoration",
    "emergency_reserve_release",
})


@dataclass(frozen=True)
class ScheduledEvent:
    """An event to apply at a specific simulation slot index.

    Attributes
    ----------
    slot_index:
        Zero-based index of the slot at which the event fires.
        Negative indices count from the end (like Python lists).
    event_type:
        Must be one of :data:`VALID_EVENT_TYPES` (matching P3's event API).
    params:
        Keyword arguments forwarded to the corresponding
        ``CampusSupplyProvider.events.trigger_*`` method.
    source_id:
        If given, the event targets only that source.  ``None`` targets all
        sources of the relevant type (matches the P3 event API default).
    """

    slot_index: int
    event_type: str
    params: dict[str, Any] = field(default_factory=dict)
    source_id: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in VALID_EVENT_TYPES:
            raise ValidationError(
                f"Unknown event_type {self.event_type!r}. "
                f"Valid types: {sorted(VALID_EVENT_TYPES)}"
            )

    def resolved_index(self, n_slots: int) -> int:
        """Return the concrete non-negative slot index for a run with *n_slots* total."""
        if self.slot_index < 0:
            return max(0, n_slots + self.slot_index)
        return self.slot_index

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_index": self.slot_index,
            "event_type": self.event_type,
            "params": dict(self.params),
            "source_id": self.source_id,
        }


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    """Complete configuration for one simulation run.

    Parameters
    ----------
    name:
        Unique human-readable identifier.
    description:
        One-line explanation for display in dashboards/reports.
    days:
        Number of simulated days (each day = 96 × 15-min slots).
    n_buildings:
        Number of buildings in the synthetic campus.  Pass ``0`` to use the
        default campus from the package config (``campus_default.json``).
    seed:
        Master random seed for deterministic demand generation.
    events:
        Ordered sequence of :class:`ScheduledEvent` objects.
    supply_config:
        Configuration dict passed to
        ``CampusSupplyProvider.from_config()``.
    negotiation_rounds:
        Maximum number of bidding rounds per slot (1 = no re-auction).
    on_failure:
        ``"settle_zero"`` or ``"raise"`` — what happens when P2/P3 fail.
    """

    name: str
    description: str
    days: int = 3
    n_buildings: int = 5
    seed: int = 42
    events: tuple[ScheduledEvent, ...] = field(default_factory=tuple)
    supply_config: dict[str, Any] = field(default_factory=lambda: dict(_DEFAULT_SUPPLY_CFG))
    negotiation_rounds: int = 2
    on_failure: str = "settle_zero"

    def __post_init__(self) -> None:
        if self.days < 1:
            raise ValidationError("Scenario.days must be >= 1")
        if self.n_buildings < 0:
            raise ValidationError("Scenario.n_buildings must be >= 0")
        if self.negotiation_rounds < 1:
            raise ValidationError("Scenario.negotiation_rounds must be >= 1")
        if self.on_failure not in ("settle_zero", "raise"):
            raise ValidationError("Scenario.on_failure must be 'settle_zero' or 'raise'")

    @property
    def n_slots(self) -> int:
        return self.days * 96

    def events_at(self, slot_index: int) -> list[ScheduledEvent]:
        """Return all events scheduled for *slot_index* in this scenario."""
        total = self.n_slots
        return [
            e for e in self.events
            if e.resolved_index(total) == slot_index
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "days": self.days,
            "n_buildings": self.n_buildings,
            "seed": self.seed,
            "negotiation_rounds": self.negotiation_rounds,
            "on_failure": self.on_failure,
            "n_slots": self.n_slots,
            "events": [e.to_dict() for e in self.events],
        }


# ---------------------------------------------------------------------------
# Predefined scenarios
# ---------------------------------------------------------------------------

#: Baseline — no events, adequate supply.
NORMAL = Scenario(
    name="normal",
    description="Normal operation: grid + solar + battery, no disruptions.",
    days=3,
    n_buildings=5,
    seed=42,
    events=(),
    supply_config=_DEFAULT_SUPPLY_CFG,
)

#: Solar cloud event on day 1, afternoon slots (slots 48-56 ≈ noon-2 pm day 1).
SOLAR_DROP = Scenario(
    name="solar_drop",
    description="Sudden cloud cover event on day 1 afternoon, solar recovers on day 2.",
    days=2,
    n_buildings=5,
    seed=42,
    events=(
        ScheduledEvent(slot_index=52, event_type="solar_drop",
                       params={"cloud_cover": 0.90}),
        ScheduledEvent(slot_index=96, event_type="solar_recovery"),
    ),
    supply_config=_DEFAULT_SUPPLY_CFG,
)

#: Grid outage on day 1 evening, restoration day 2 morning.
GRID_OUTAGE = Scenario(
    name="grid_outage",
    description="Grid blackout at slot 72 (18:00 day 1), restored at slot 96 (day 2 start).",
    days=2,
    n_buildings=5,
    seed=42,
    events=(
        ScheduledEvent(slot_index=72, event_type="grid_outage"),
        ScheduledEvent(slot_index=72, event_type="emergency_reserve_release"),
        ScheduledEvent(slot_index=96, event_type="grid_restoration"),
    ),
    supply_config=_DEFAULT_SUPPLY_CFG,
)

#: Battery outage mid-simulation.
BATTERY_OUTAGE = Scenario(
    name="battery_outage",
    description="Battery storage goes offline at slot 48, restored at slot 144.",
    days=3,
    n_buildings=5,
    seed=42,
    events=(
        ScheduledEvent(slot_index=48,  event_type="battery_outage"),
        ScheduledEvent(slot_index=144, event_type="battery_restoration"),
    ),
    supply_config=_DEFAULT_SUPPLY_CFG,
)

#: Battery derate — reduced discharge power.
BATTERY_DERATE = Scenario(
    name="battery_derate",
    description="Battery discharge power derated to 20 kW from slot 24 onward.",
    days=2,
    n_buildings=5,
    seed=42,
    events=(
        ScheduledEvent(slot_index=24, event_type="battery_derate",
                       params={"max_discharge_kw": 20.0}),
    ),
    supply_config=_DEFAULT_SUPPLY_CFG,
)

#: Tariff spike during peak hours.
TARIFF_SPIKE = Scenario(
    name="tariff_spike",
    description="Grid tariff spikes 2.5× at slot 64 (peak), normalised at slot 80.",
    days=2,
    n_buildings=5,
    seed=42,
    events=(
        ScheduledEvent(slot_index=64, event_type="tariff_spike",
                       params={"multiplier": 2.5}),
        ScheduledEvent(slot_index=80, event_type="tariff_normalization"),
    ),
    supply_config=_DEFAULT_SUPPLY_CFG,
)

#: Deliberately constrained supply to trigger scarcity and demand response.
SCARCITY = Scenario(
    name="scarcity",
    description="Supply deliberately below total demand to exercise re-auction and demand response.",
    days=1,
    n_buildings=5,
    seed=42,
    events=(),
    supply_config=_SCARCITY_SUPPLY_CFG,
    negotiation_rounds=2,
)

#: Mixed stress — grid outage + solar drop + tariff spike.
MIXED_STRESS = Scenario(
    name="mixed_stress",
    description="Simultaneous grid outage, solar cloud event, and tariff spike on day 1.",
    days=3,
    n_buildings=5,
    seed=42,
    events=(
        ScheduledEvent(slot_index=48, event_type="solar_drop",
                       params={"cloud_cover": 0.80}),
        ScheduledEvent(slot_index=56, event_type="grid_outage"),
        ScheduledEvent(slot_index=56, event_type="emergency_reserve_release"),
        ScheduledEvent(slot_index=56, event_type="tariff_spike",
                       params={"multiplier": 2.0}),
        ScheduledEvent(slot_index=96, event_type="grid_restoration"),
        ScheduledEvent(slot_index=96, event_type="solar_recovery"),
        ScheduledEvent(slot_index=96, event_type="tariff_normalization"),
    ),
    supply_config=_DEFAULT_SUPPLY_CFG,
)

#: Registry mapping scenario name → Scenario object.
SCENARIOS: dict[str, Scenario] = {
    s.name: s for s in (
        NORMAL, SOLAR_DROP, GRID_OUTAGE, BATTERY_OUTAGE,
        BATTERY_DERATE, TARIFF_SPIKE, SCARCITY, MIXED_STRESS,
    )
}


def get_scenario(name: str) -> Scenario:
    """Return a predefined scenario by name (case-insensitive).

    Raises
    ------
    ValidationError
        If the name is not in :data:`SCENARIOS`.
    """
    key = name.strip().lower()
    if key not in SCENARIOS:
        raise ValidationError(
            f"Unknown scenario {name!r}. Available: {sorted(SCENARIOS)}"
        )
    return SCENARIOS[key]
