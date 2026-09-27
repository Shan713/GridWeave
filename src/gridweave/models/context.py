"""BidContext: what the coordinator (P4) tells agents when it requests bids."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from gridweave.models.common import TimeSlot
from gridweave.utils.validation import ValidationError, require_fraction


@dataclass(frozen=True)
class BidContext:
    """Market information the coordinator (P4) passes when requesting bids.

    * ``time_slot`` — the slot being auctioned.
    * ``scarcity`` — expected supply shortfall in [0, 1] (0 = ample supply,
      1 = extreme shortage). P3/P4 can derive it as
      ``max(0, 1 - expected_supply / expected_demand)``.
    * ``created_at`` — decision time; defaults to the agent's last observation.
    """

    time_slot: TimeSlot
    scarcity: float = 0.0
    created_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.time_slot, TimeSlot):
            raise ValidationError("BidContext.time_slot must be a TimeSlot")
        require_fraction("scarcity", self.scarcity)
        object.__setattr__(self, "metadata", dict(self.metadata))
