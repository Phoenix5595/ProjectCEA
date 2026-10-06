"""Shared pure ramp-skip thresholds used by runtime and projection code."""

from __future__ import annotations

from typing import Final

RAMP_SKIP_THRESHOLDS: Final[dict[str, float]] = {
    "heating": 0.1,
    "cooling": 0.1,
    "vpd": 0.01,
    "co2": 10.0,
    "humidity": 1.0,
}

DEFAULT_RAMP_SKIP_THRESHOLD: Final[float] = 0.1
