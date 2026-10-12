"""Deterministic overview bucket selection and exact aligned bucket bounds."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final, Literal, TypeAlias

from monitoring_service.sensor_models import (
    FrozenMonitoringModel,
    MonitoringRangeError,
    MonitoringValidationError,
)

MIN_REQUESTED_POINTS: Final = 10
MAX_REQUESTED_POINTS: Final = 1000

# Overview display buckets span every whole-hour width from one to twelve
# hours; the picker never widens past the twelve-hour cap.
OverviewBucketSeconds: TypeAlias = Literal[
    3600, 7200, 10800, 14400, 18000, 21600, 25200, 28800, 32400, 36000, 39600, 43200
]
FIXED_LADDER_SECONDS: Final[tuple[OverviewBucketSeconds, ...]] = (
    3600,
    7200,
    10800,
    14400,
    18000,
    21600,
    25200,
    28800,
    32400,
    36000,
    39600,
    43200,
)

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


def timedelta_microseconds(value: timedelta) -> int:
    """Return the exact integer microsecond count of a timedelta."""
    return value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds


class FixedHistoryBucket(FrozenMonitoringModel):
    """An epoch-aligned whole-hour overview bucket."""

    kind: Literal["fixed"] = "fixed"
    seconds: OverviewBucketSeconds


def validate_history_budget(max_points: int) -> int:
    """Return the budget when it fits the overview point budget, else 400."""
    if not isinstance(max_points, int) or isinstance(max_points, bool):
        raise MonitoringValidationError(
            "requested_max_points must be an integer between 10 and 1000"
        )
    if not MIN_REQUESTED_POINTS <= max_points <= MAX_REQUESTED_POINTS:
        raise MonitoringValidationError("requested_max_points must be between 10 and 1000")
    return max_points


def _require_aware_instant(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise MonitoringRangeError(f"bucket range {field_name} must be a datetime")
    if value.utcoffset() is None:
        raise MonitoringRangeError(f"bucket range {field_name} must include a timezone offset")
    return value.astimezone(UTC)


def _datetime_from_epoch_microseconds(value: int) -> datetime:
    return _EPOCH + timedelta(microseconds=value)


def _require_ordered_epoch_microseconds(start: datetime, end: datetime) -> tuple[int, int]:
    start_us = timedelta_microseconds(_require_aware_instant(start, "start") - _EPOCH)
    end_us = timedelta_microseconds(_require_aware_instant(end, "end") - _EPOCH)
    if end_us <= start_us:
        raise MonitoringRangeError("range end must be later than range start")
    return start_us, end_us


def choose_history_bucket(start: datetime, end: datetime, max_points: int) -> FixedHistoryBucket:
    """Select the finest whole-hour overview bucket whose intersecting count fits.

    Every ladder width is an epoch-aligned whole-hour step, so the intersecting
    count includes both clipped edge buckets exactly. When even the maximum
    twelve-hour width exceeds the budget the range/budget pair is rejected
    before any read work instead of widening further or raising the budget.
    """
    budget = validate_history_budget(max_points)
    start_us, end_us = _require_ordered_epoch_microseconds(start, end)
    for seconds in FIXED_LADDER_SECONDS:
        step_us = seconds * 1_000_000
        if (end_us - 1) // step_us - start_us // step_us + 1 <= budget:
            return FixedHistoryBucket(seconds=seconds)
    raise MonitoringValidationError(
        "history range and point budget cannot fit the maximum 12-hour bucket size"
    )


def history_bucket_bounds(
    start: datetime, end: datetime, bucket: FixedHistoryBucket
) -> tuple[tuple[datetime, datetime, bool], ...]:
    """Return ``(clipped_start, clipped_end, partial)`` for each intersecting bucket.

    Buckets align to multiples of their width from the epoch; outer buckets are
    clipped to the requested half-open range and flagged partial. Clipped bounds
    always stay inside the requested range, so every emitted datetime remains
    within the supported year 1..9999 domain.
    """
    start_us, end_us = _require_ordered_epoch_microseconds(start, end)
    step_us = bucket.seconds * 1_000_000
    first_bucket_us = start_us // step_us * step_us
    last_bucket_us = (end_us - 1) // step_us * step_us
    bounds: list[tuple[datetime, datetime, bool]] = []
    bucket_us = first_bucket_us
    while True:
        bucket_end_us = bucket_us + step_us
        clipped_start_us = bucket_us if bucket_us > start_us else start_us
        clipped_end_us = bucket_end_us if bucket_end_us < end_us else end_us
        bounds.append(
            (
                _datetime_from_epoch_microseconds(clipped_start_us),
                _datetime_from_epoch_microseconds(clipped_end_us),
                bucket_us < start_us or bucket_end_us > end_us,
            )
        )
        if bucket_us >= last_bucket_us:
            break
        bucket_us = bucket_end_us
    return tuple(bounds)
