"""Shared interval policy for sensor read-model resolution."""

from __future__ import annotations

from datetime import timedelta
from math import ceil
from typing import Final


NICE_INTERVAL_SECONDS: Final[tuple[int, ...]] = (
    1,
    2,
    5,
    10,
    15,
    30,
    60,
    120,
    300,
    600,
    900,
    1800,
    3600,
    7200,
    14400,
    21600,
    43200,
    86400,
)


def resolve_interval_seconds(
    duration_seconds: int, max_points: int, source_bucket_seconds: int
) -> int:
    """Return the sensor repository's applied, ladder-safe bucket interval.

    The repository owns this policy: a budgeted read uses an interval no finer
    than the read model's source bucket or the requested duration-per-point.
    """
    minimum_seconds = max(source_bucket_seconds, ceil(duration_seconds / max_points))
    return next(interval for interval in NICE_INTERVAL_SECONDS if interval >= minimum_seconds)


def derive_interval_seconds(
    duration: timedelta, source_bucket_seconds: int, max_points: int | None
) -> int | None:
    """Return the applied interval when an optional budget requests bucketing."""
    if max_points is None:
        return None
    return resolve_interval_seconds(
        ceil(duration.total_seconds()), max_points, source_bucket_seconds
    )
