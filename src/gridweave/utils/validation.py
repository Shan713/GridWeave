"""Validation helpers used by the domain models.

Every model in :mod:`gridweave.models` validates itself in ``__post_init__``
so that a physically nonsensical object (negative power, critical load larger
than total demand, a priority outside ``[0, 1]``...) can never be constructed.
Raising early keeps errors close to their cause, which matters when four
people's code is exchanging these objects.
"""
from __future__ import annotations

import math

#: Absolute tolerance (kW) used when comparing power quantities. Floating point
#: arithmetic such as ``critical + flexible`` may differ from ``requested`` by a
#: few ULPs; anything within this tolerance is considered equal.
POWER_TOLERANCE_KW = 1e-6


class ValidationError(ValueError):
    """Raised when a domain object would violate one of its invariants."""


def require_finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValidationError(f"{name} must be finite, got {value!r}")
    return float(value)


def require_non_negative(name: str, value: float) -> float:
    value = require_finite(name, value)
    if value < -POWER_TOLERANCE_KW:
        raise ValidationError(f"{name} must be >= 0, got {value}")
    return max(value, 0.0)


def require_positive(name: str, value: float) -> float:
    value = require_finite(name, value)
    if value <= 0:
        raise ValidationError(f"{name} must be > 0, got {value}")
    return value


def require_fraction(name: str, value: float) -> float:
    """Require ``0 <= value <= 1``."""
    value = require_finite(name, value)
    if not 0.0 <= value <= 1.0:
        raise ValidationError(f"{name} must be within [0, 1], got {value}")
    return value


def require_le(small_name: str, small: float, big_name: str, big: float) -> None:
    """Require ``small <= big`` (within :data:`POWER_TOLERANCE_KW`)."""
    if small > big + POWER_TOLERANCE_KW:
        raise ValidationError(f"{small_name} ({small}) must be <= {big_name} ({big})")


def require_close(a_name: str, a: float, b_name: str, b: float) -> None:
    """Require ``a == b`` (within :data:`POWER_TOLERANCE_KW`)."""
    if abs(a - b) > POWER_TOLERANCE_KW:
        raise ValidationError(f"{a_name} ({a}) must equal {b_name} ({b})")


def require_non_empty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    return value


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
