"""Package-wide logging setup.

Library modules call :func:`get_logger` and never configure handlers
themselves; only entry points (scripts, examples) call :func:`configure`.
"""
from __future__ import annotations

import logging
import os

_ROOT = "gridweave"


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the ``gridweave`` namespace."""
    if not name.startswith(_ROOT):
        name = f"{_ROOT}.{name}"
    return logging.getLogger(name)


def configure(level: str | None = None) -> None:
    """Configure console logging for scripts. Honours ``GRIDWEAVE_LOG_LEVEL``."""
    level = (level or os.environ.get("GRIDWEAVE_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
