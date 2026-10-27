"""Bounded read queries and cursor helpers for physical relay timelines."""

from __future__ import annotations

import base64
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping, Sequence

from monitoring_service.control_models import (
    RelayTimelineLoadPoint,
    RelayTimelineRange,
    RelayTimelineTransition,
)

MAX_RELAY_TIMELINE_LIMIT = 2_000
MAX_RELAY_LOAD_ROWS = 50_000
MAX_HEARTBEAT_ROWS = 20_160


@dataclass(frozen=True, slots=True)
class RelayTimelineCursor:
    """Validated state carried between deterministic observation pages."""

    location: str
    start: datetime
    end: datetime
    last_at: datetime
    last_id: int
    watermark: int
    last_heartbeat_at: datetime | None
    coverage_complete: bool


class RelayTimelineCursorError(ValueError):
    """A relay timeline cursor is malformed or bound to another request."""


def encode_cursor(cursor: RelayTimelineCursor) -> str:
    """Encode a location/range-bound stable page position as an opaque URL token."""
    payload = {
        "v": 1,
        "location": cursor.location,
        "start": _format_timestamp(cursor.start),
        "end": _format_timestamp(cursor.end),
        "last_at": _format_timestamp(cursor.last_at),
        "last_id": cursor.last_id,
        "watermark": cursor.watermark,
        "last_heartbeat_at": (
            _format_timestamp(cursor.last_heartbeat_at)
            if cursor.last_heartbeat_at is not None
            else None
        ),
        "coverage_complete": cursor.coverage_complete,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def decode_cursor(
    token: str,
    *,
    location: str,
    history_range: RelayTimelineRange,
) -> RelayTimelineCursor:
    """Validate cursor structure and its exact location/range binding."""
    if not token or len(token) > 4_096:
        raise RelayTimelineCursorError("invalid relay timeline cursor")
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {
            "v",
            "location",
            "start",
            "end",
            "last_at",
            "last_id",
            "watermark",
            "last_heartbeat_at",
            "coverage_complete",
        }:
            raise ValueError("unexpected cursor payload")
        if payload["v"] != 1 or type(payload["v"]) is not int:
            raise ValueError("unsupported cursor version")
        cursor_location = payload["location"]
        if not isinstance(cursor_location, str) or cursor_location != location:
            raise ValueError("cursor location mismatch")
        start = _parse_timestamp(payload["start"])
        end = _parse_timestamp(payload["end"])
        last_at = _parse_timestamp(payload["last_at"])
        heartbeat_at_value = payload["last_heartbeat_at"]
        heartbeat_at = (
            _parse_timestamp(heartbeat_at_value) if heartbeat_at_value is not None else None
        )
        last_id = payload["last_id"]
        watermark = payload["watermark"]
        coverage_complete = payload["coverage_complete"]
        if type(last_id) is not int or last_id <= 0:
            raise ValueError("invalid cursor observation id")
        if type(watermark) is not int or watermark < last_id:
            raise ValueError("invalid cursor watermark")
        if type(coverage_complete) is not bool:
            raise ValueError("invalid cursor coverage flag")
        if start != history_range.start or end != history_range.end:
            raise ValueError("cursor range mismatch")
        if not (start <= last_at < end):
            raise ValueError("cursor position is outside requested range")
        if heartbeat_at is not None and heartbeat_at >= end:
            raise ValueError("cursor heartbeat is outside requested range")
        return RelayTimelineCursor(
            location=location,
            start=start,
            end=end,
            last_at=last_at,
            last_id=last_id,
            watermark=watermark,
            last_heartbeat_at=heartbeat_at,
            coverage_complete=coverage_complete,
        )
    except (UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        if isinstance(exc, RelayTimelineCursorError):
            raise
        raise RelayTimelineCursorError("invalid relay timeline cursor") from exc


def coverage_from_rows(
    heartbeat_rows: Sequence[Mapping[str, object]],
    coverage_row: Mapping[str, object] | None,
    anchors: Sequence[RelayTimelineTransition],
    history_range: RelayTimelineRange,
) -> bool:
    """Return true only when recorder and event-time ownership support the whole range."""
    if coverage_row is None:
        return False
    relevant_count = _as_int(coverage_row.get("relevant_channel_count"))
    valid_anchor_count = _as_int(coverage_row.get("valid_anchor_count"))
    if relevant_count <= 0 or valid_anchor_count < relevant_count:
        return False
    if any(
        bool(coverage_row.get(flag))
        for flag in ("has_coverage_break", "has_session_change", "has_owner_change")
    ):
        return False

    heartbeats = [row for row in heartbeat_rows if isinstance(row.get("observed_at"), datetime)]
    before = [row for row in heartbeats if row["observed_at"] < history_range.start]
    if not before:
        return False
    previous = before[-1]
    previous_at = previous["observed_at"]
    assert isinstance(previous_at, datetime)
    if history_range.start - previous_at > timedelta(seconds=60):
        return False
    previous_session = previous.get("session_id")
    previous_at = previous["observed_at"]
    last_at = previous_at
    for row in heartbeats:
        observed_at = row.get("observed_at")
        if not isinstance(observed_at, datetime) or observed_at <= previous_at:
            continue
        if observed_at - last_at > timedelta(seconds=60):
            return False
        if row.get("session_id") != previous_session:
            return False
        previous_session = row.get("session_id")
        last_at = observed_at
    if history_range.end - last_at > timedelta(seconds=60):
        return False

    window_count = max((_as_int(row.get("window_count")) for row in heartbeats), default=0)
    if window_count > MAX_HEARTBEAT_ROWS:
        return False
    baseline_session = previous.get("session_id")
    for anchor in anchors:
        if anchor.reason in {"stale", "recording_gap"} or anchor.observed_state is None:
            return False
        if anchor.session_id != baseline_session:
            return False
    return True


def raw_load_points(
    rows: Sequence[Mapping[str, object]],
) -> tuple[tuple[RelayTimelineLoadPoint, ...], bool]:
    """Bound dense raw output while retaining extrema and every sampled null-gap edge."""
    total_count = max((_as_int(row.get("total_count")) for row in rows), default=len(rows))
    truncated = total_count > MAX_RELAY_LOAD_ROWS or any(
        _as_int(row.get("owner_count")) != 1 for row in rows
    )
    points = [
        row for row in rows[:MAX_RELAY_LOAD_ROWS] if isinstance(row.get("timestamp"), datetime)
    ]
    groups: dict[tuple[int | None, str], list[tuple[int, Mapping[str, object]]]] = defaultdict(list)
    for position, row in enumerate(points):
        device_name = row.get("device_name")
        if not isinstance(device_name, str):
            continue
        raw_id = row.get("device_id")
        device_id = raw_id if isinstance(raw_id, int) and not isinstance(raw_id, bool) else None
        groups[(device_id, device_name)].append((position, row))

    selected: list[tuple[int, Mapping[str, object]]] = []
    for group in groups.values():
        group.sort(key=lambda entry: (entry[1]["timestamp"], _as_int(entry[1].get("id"))))
        if len(group) <= 2_000:
            selected.extend(group)
            continue
        keep: set[int] = {0, len(group) - 1}
        bucket_count = 512
        for bucket in range(bucket_count):
            first = bucket * len(group) // bucket_count
            stop = (bucket + 1) * len(group) // bucket_count
            if first >= stop:
                continue
            keep.add(first)
            keep.add(stop - 1)
            finite_indices = [
                index
                for index in range(first, stop)
                if _requested_percent(group[index][1].get("pid_output")) is not None
            ]
            if finite_indices:
                keep.add(
                    min(
                        finite_indices,
                        key=lambda index: _requested_percent(group[index][1].get("pid_output")),
                    )
                )
                keep.add(
                    max(
                        finite_indices,
                        key=lambda index: _requested_percent(group[index][1].get("pid_output")),
                    )
                )
        for index, (_, row) in enumerate(group):
            is_null = _requested_percent(row.get("pid_output")) is None
            previous_null = (
                _requested_percent(group[index - 1][1].get("pid_output")) is None
                if index > 0
                else False
            )
            next_null = (
                _requested_percent(group[index + 1][1].get("pid_output")) is None
                if index + 1 < len(group)
                else False
            )
            if is_null and (not previous_null or not next_null):
                keep.add(index)
        selected.extend(group[index] for index in sorted(keep))

    selected.sort(
        key=lambda entry: (
            entry[1]["timestamp"],
            entry[1].get("device_id") or -1,
            str(entry[1].get("device_name")),
        )
    )
    if len(selected) > MAX_RELAY_LOAD_ROWS:
        selected = selected[:MAX_RELAY_LOAD_ROWS]
        truncated = True
    result: list[RelayTimelineLoadPoint] = []
    for _, row in selected:
        qualified = _as_int(row.get("owner_count")) == 1
        percent = _requested_percent(row.get("pid_output")) if qualified else None
        # A raw load point is one recorded sample: its valid extrema are the
        # sample itself, and dense-group reduction already retains the samples
        # holding each group's requested-percent peak and trough.
        result.append(
            RelayTimelineLoadPoint(
                device_id=(
                    row.get("device_id")
                    if isinstance(row.get("device_id"), int)
                    and not isinstance(row.get("device_id"), bool)
                    else None
                ),
                device_name=str(row["device_name"]),
                time=row["timestamp"],
                requested_percent=percent,
                requested_min_percent=percent,
                requested_max_percent=percent,
                aggregated=False,
                interval_seconds=30,
            )
        )
    return tuple(result), truncated


def aggregate_load_points(
    rows: Sequence[Mapping[str, object]], interval_seconds: int
) -> tuple[tuple[RelayTimelineLoadPoint, ...], bool]:
    """Shape bounded coarse output points without using aggregates for relay state."""
    total_count = max((_as_int(row.get("total_count")) for row in rows), default=len(rows))
    truncated = total_count > MAX_RELAY_LOAD_ROWS or any(
        _as_int(row.get("owner_count")) != 1 for row in rows
    )
    result: list[RelayTimelineLoadPoint] = []
    for row in rows[:MAX_RELAY_LOAD_ROWS]:
        timestamp = row.get("timestamp")
        device_name = row.get("device_name")
        if not isinstance(timestamp, datetime) or not isinstance(device_name, str):
            continue
        qualified = _as_int(row.get("owner_count")) == 1
        percent = _requested_percent(row.get("pid_output")) if qualified else None
        minimum, maximum = _valid_requested_percent_extrema(row) if qualified else (None, None)
        result.append(
            RelayTimelineLoadPoint(
                device_id=(
                    row.get("device_id")
                    if isinstance(row.get("device_id"), int)
                    and not isinstance(row.get("device_id"), bool)
                    else None
                ),
                device_name=device_name,
                time=timestamp,
                requested_percent=percent,
                requested_min_percent=minimum,
                requested_max_percent=maximum,
                aggregated=True,
                interval_seconds=interval_seconds,
            )
        )
    result.sort(key=lambda point: (point.time, point.device_id or -1, point.device_name))
    return tuple(result), truncated


def _valid_requested_percent_extrema(
    row: Mapping[str, object],
) -> tuple[float | None, float | None]:
    """Return aggregate percent extrema only when every sampled fraction is valid.

    The existing 1min/5min requested-output aggregates keep min/max over all
    non-null recorded fractions, including samples outside 0..1. Whenever the
    bucket's own bounds leave the valid range, validity cannot be proven
    without a raw scan, so no extremum is reported — an out-of-range recorded
    fraction must never be clamped into a plausible in-range bound.
    """
    low = _requested_percent(row.get("pid_output_min"))
    high = _requested_percent(row.get("pid_output_max"))
    if low is None or high is None:
        return None, None
    return low, high


def _requested_percent(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        fraction = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
        return None
    return fraction * 100.0


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("cursor timestamp must be text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("cursor timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
