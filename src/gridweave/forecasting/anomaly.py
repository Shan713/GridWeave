"""Lightweight demand-spike detection (robust z-score)."""
from __future__ import annotations

from statistics import median
from typing import Sequence

from gridweave.utils.validation import ValidationError


class SpikeDetector:
    """Flags a new value as anomalous when it deviates from the recent median
    by more than ``z_threshold`` robust standard deviations (1.4826 * MAD).

    ``min_scale_kw`` stops a perfectly flat history from flagging tiny
    fluctuations as spikes.
    """

    def __init__(self, window: int = 16, z_threshold: float = 3.5, min_scale_kw: float = 1.0) -> None:
        if window < 3:
            raise ValidationError("window must be >= 3")
        if z_threshold <= 0 or min_scale_kw <= 0:
            raise ValidationError("z_threshold and min_scale_kw must be > 0")
        self.window = window
        self.z_threshold = z_threshold
        self.min_scale_kw = min_scale_kw

    def score(self, history: Sequence[float], value: float) -> float:
        recent = list(history)[-self.window:]
        if len(recent) < 3:
            return 0.0
        med = median(recent)
        mad = median(abs(x - med) for x in recent)
        scale = max(1.4826 * mad, self.min_scale_kw)
        return (value - med) / scale

    def is_anomaly(self, history: Sequence[float], value: float) -> bool:
        return abs(self.score(history, value)) > self.z_threshold
