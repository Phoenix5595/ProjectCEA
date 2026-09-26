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

RELAY_WATERMARK_SQL = """
SELECT COALESCE(MAX(observation_id), 0) AS watermark
FROM relay_observation
"""

RELAY_TRANSITIONS_FIRST_PAGE_SQL = """
WITH relay_timeline_page_source AS (
    SELECT
        ro.observation_id, ro.observed_at, ro.channel,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.observed_state ELSE NULL END AS observed_state,
        ro.reason, ro.session_id, ro.registry_version,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.device_id ELSE NULL END AS device_id,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.device_name ELSE NULL END AS device_name,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.device_type ELSE NULL END AS device_type,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.location ELSE NULL END AS location,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.cluster ELSE NULL END AS cluster
    FROM relay_observation AS ro
    WHERE ro.observation_id <= $1
      AND ro.observed_at >= $2 AND ro.observed_at < $3
      AND (
          ro.reason = 'heartbeat'
          OR ro.reason IN ('stale', 'recovered', 'recording_gap')
          OR ro.reason = 'assignment_changed'
          OR (
              ro.location = $4 AND ro.cluster = 'main'
              AND ro.channel IS NOT NULL
              AND ro.device_type IS DISTINCT FROM 'light'
          )
      )
)
SELECT observation_id, observed_at, channel, observed_state, reason, session_id,
       registry_version, device_id, device_name, device_type, location, cluster
FROM relay_timeline_page_source
ORDER BY observed_at, observation_id
LIMIT $5
"""

RELAY_TRANSITIONS_AFTER_CURSOR_SQL = """
WITH relay_timeline_page_source AS (
    SELECT
        ro.observation_id, ro.observed_at, ro.channel,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.observed_state ELSE NULL END AS observed_state,
        ro.reason, ro.session_id, ro.registry_version,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.device_id ELSE NULL END AS device_id,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.device_name ELSE NULL END AS device_name,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.device_type ELSE NULL END AS device_type,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.location ELSE NULL END AS location,
        CASE WHEN ro.location = $4 AND ro.cluster = 'main'
             THEN ro.cluster ELSE NULL END AS cluster
    FROM relay_observation AS ro
    WHERE ro.observation_id <= $1
      AND ro.observed_at >= $2 AND ro.observed_at < $3
      AND (
          ro.reason = 'heartbeat'
          OR ro.reason IN ('stale', 'recovered', 'recording_gap')
          OR ro.reason = 'assignment_changed'
          OR (
              ro.location = $4 AND ro.cluster = 'main'
              AND ro.channel IS NOT NULL
              AND ro.device_type IS DISTINCT FROM 'light'
          )
      )
      AND (ro.observed_at, ro.observation_id) > ($5, $6)
)
SELECT observation_id, observed_at, channel, observed_state, reason, session_id,
       registry_version, device_id, device_name, device_type, location, cluster
FROM relay_timeline_page_source
ORDER BY observed_at, observation_id
LIMIT $7
"""

RELAY_CHANNEL_ANCHORS_SQL = """
WITH relay_channel_anchors AS (
    SELECT DISTINCT ON (ro.channel)
        ro.observation_id, ro.observed_at, ro.channel, ro.observed_state,
        ro.reason, ro.session_id, ro.registry_version, ro.device_id,
        ro.device_name, ro.device_type, ro.location, ro.cluster
    FROM relay_observation AS ro
    WHERE ro.channel IS NOT NULL
      AND ro.observed_at < $1
      AND ro.observation_id <= $2
    ORDER BY ro.channel, ro.observed_at DESC, ro.observation_id DESC
)
SELECT observation_id, observed_at, channel, observed_state, reason, session_id,
       registry_version, device_id, device_name, device_type, location, cluster
FROM relay_channel_anchors
WHERE location = $3 AND cluster = 'main'
  AND device_type IS DISTINCT FROM 'light'
ORDER BY channel
"""

RELAY_HEARTBEAT_HISTORY_SQL = """
WITH relay_heartbeat_before_range AS (
    SELECT observation_id, observed_at, channel, observed_state, reason, session_id,
           registry_version, device_id, device_name, device_type, location, cluster,
           0::bigint AS window_count
    FROM relay_observation
    WHERE reason = 'heartbeat' AND observed_at < $2 AND observation_id <= $1
    ORDER BY observed_at DESC, observation_id DESC
    LIMIT 1
), relay_heartbeat_window AS (
    SELECT observation_id, observed_at, channel, observed_state, reason, session_id,
           registry_version, device_id, device_name, device_type, location, cluster,
           COUNT(*) OVER ()::bigint AS window_count
    FROM relay_observation
    WHERE reason = 'heartbeat' AND observed_at >= $2 AND observed_at < $3
      AND observation_id <= $1
), relay_heartbeat_bounded AS (
    SELECT * FROM relay_heartbeat_window
    ORDER BY observed_at, observation_id
    LIMIT 20160
), relay_latest_heartbeat_in_window AS (
    SELECT * FROM relay_heartbeat_window
    ORDER BY observed_at DESC, observation_id DESC
    LIMIT 1
)
SELECT * FROM (
    SELECT * FROM relay_heartbeat_before_range
    UNION
    SELECT * FROM relay_heartbeat_bounded
    UNION
    SELECT * FROM relay_latest_heartbeat_in_window
) AS relay_heartbeat_history
ORDER BY observed_at, observation_id
LIMIT 20162
"""

RELAY_COVERAGE_SUMMARY_SQL = """
WITH relay_candidate_channels AS (
    SELECT DISTINCT channel
    FROM relay_observation
    WHERE observation_id <= $1 AND channel IS NOT NULL
      AND observed_at < $4
      AND location = $3 AND cluster = 'main'
      AND device_type IS DISTINCT FROM 'light'
), relay_channel_before_range AS (
    SELECT DISTINCT ON (ro.channel)
        ro.observation_id, ro.observed_at, ro.channel, ro.observed_state,
        ro.reason, ro.session_id, ro.device_id, ro.device_name, ro.device_type,
        ro.location, ro.cluster
    FROM relay_observation AS ro
    JOIN relay_candidate_channels AS candidate USING (channel)
    WHERE ro.observation_id <= $1 AND ro.observed_at < $2
    ORDER BY ro.channel, ro.observed_at DESC, ro.observation_id DESC
), relay_relevant_channels AS (
    SELECT channel
    FROM relay_channel_before_range
    WHERE location = $3 AND cluster = 'main'
      AND device_type IS DISTINCT FROM 'light'
    UNION
    SELECT DISTINCT channel
    FROM relay_observation
    WHERE observation_id <= $1
      AND observed_at >= $2 AND observed_at < $4
      AND location = $3 AND cluster = 'main'
      AND (
          device_type IS DISTINCT FROM 'light'
          OR (
              reason = 'assignment_changed'
              AND channel IN (SELECT channel FROM relay_candidate_channels)
          )
      )
), relay_channel_history AS (
    SELECT before_row.*
    FROM relay_channel_before_range AS before_row
    JOIN relay_relevant_channels AS relevant USING (channel)
    UNION ALL
    SELECT ro.observation_id, ro.observed_at, ro.channel, ro.observed_state,
           ro.reason, ro.session_id, ro.device_id, ro.device_name, ro.device_type,
           ro.location, ro.cluster
    FROM relay_observation AS ro
    JOIN relay_relevant_channels AS relevant USING (channel)
    WHERE ro.observation_id <= $1
      AND ro.observed_at >= $2 AND ro.observed_at < $4
), relay_sequenced_channel_history AS (
    SELECT *,
           LAG(observation_id) OVER channel_order AS previous_observation_id,
           LAG(session_id) OVER channel_order AS previous_session_id,
           LAG(device_id) OVER channel_order AS previous_device_id,
           LAG(device_name) OVER channel_order AS previous_device_name,
           LAG(device_type) OVER channel_order AS previous_device_type,
           LAG(location) OVER channel_order AS previous_location,
           LAG(cluster) OVER channel_order AS previous_cluster
    FROM relay_channel_history
    WINDOW channel_order AS (
        PARTITION BY channel ORDER BY observed_at, observation_id
    )
)
SELECT
    (SELECT COUNT(*) FROM relay_relevant_channels)::bigint AS relevant_channel_count,
    (SELECT COUNT(*)
     FROM relay_channel_before_range AS before_row
     JOIN relay_relevant_channels AS relevant USING (channel)
     WHERE before_row.location = $3 AND before_row.cluster = 'main'
       AND before_row.device_type IS DISTINCT FROM 'light'
       AND before_row.observed_state IS NOT NULL
       AND before_row.reason NOT IN ('stale', 'recording_gap'))::bigint
       AS valid_anchor_count,
    EXISTS (
        SELECT 1 FROM relay_sequenced_channel_history
        WHERE reason IN ('stale', 'recovered', 'recording_gap', 'assignment_changed')
    ) AS has_coverage_break,
    EXISTS (
        SELECT 1 FROM relay_sequenced_channel_history
        WHERE previous_observation_id IS NOT NULL
          AND session_id IS DISTINCT FROM previous_session_id
    ) AS has_session_change,
    EXISTS (
        SELECT 1 FROM relay_sequenced_channel_history
        WHERE previous_observation_id IS NOT NULL
          AND ROW(device_id, device_name, device_type, location, cluster)
              IS DISTINCT FROM
              ROW(previous_device_id, previous_device_name, previous_device_type,
                  previous_location, previous_cluster)
    ) AS has_owner_change
"""

_RELAY_ASSIGNMENT_INTERVALS = """
WITH relay_assignment_predecessors AS (
    SELECT DISTINCT ON (ro.channel)
        ro.observation_id, ro.observed_at, ro.channel, ro.device_id,
        ro.device_name, ro.device_type, ro.location, ro.cluster, ro.reason
    FROM relay_observation AS ro
    WHERE ro.channel IS NOT NULL AND ro.observed_at < $2
      AND ro.observation_id <= $1
    ORDER BY ro.channel, ro.observed_at DESC, ro.observation_id DESC
), relay_assignment_history AS (
    SELECT * FROM relay_assignment_predecessors
    UNION ALL
    SELECT ro.observation_id, ro.observed_at, ro.channel, ro.device_id,
           ro.device_name, ro.device_type, ro.location, ro.cluster, ro.reason
    FROM relay_observation AS ro
    WHERE ro.channel IS NOT NULL
      AND ro.observed_at >= $2 AND ro.observed_at < $3
      AND ro.observation_id <= $1
), relay_assignment_ordered AS (
    SELECT *,
           LAG(observation_id) OVER channel_order AS previous_observation_id,
           LAG(device_id) OVER channel_order AS previous_device_id,
           LAG(device_name) OVER channel_order AS previous_device_name,
           LAG(device_type) OVER channel_order AS previous_device_type,
           LAG(location) OVER channel_order AS previous_location,
           LAG(cluster) OVER channel_order AS previous_cluster
    FROM relay_assignment_history
    WINDOW channel_order AS (
        PARTITION BY channel ORDER BY observed_at, observation_id
    )
), relay_assignment_boundaries AS (
    SELECT observation_id, observed_at, channel, device_id,
           device_name, device_type, location, cluster, reason
    FROM relay_assignment_ordered
    WHERE previous_observation_id IS NULL
       OR ROW(device_id, device_name, device_type, location, cluster)
          IS DISTINCT FROM
          ROW(previous_device_id, previous_device_name, previous_device_type,
              previous_location, previous_cluster)
       OR reason IN ('stale', 'recovered', 'recording_gap', 'assignment_changed')
), relay_assignment_intervals AS (
    SELECT *, LEAD(observed_at) OVER (
        PARTITION BY channel ORDER BY observed_at, observation_id
    ) AS next_observed_at
    FROM relay_assignment_boundaries
)
"""

RELAY_RAW_PID_LOAD_SQL = _RELAY_ASSIGNMENT_INTERVALS + """
, relay_matched_pid_samples AS (
    SELECT s.id, s.timestamp, s.device_name, s.pid_output,
           COUNT(DISTINCT assignment.device_id)::bigint AS owner_count,
           MIN(assignment.device_id) AS device_id
    FROM automation_state AS s
    JOIN relay_assignment_intervals AS assignment
      ON assignment.location = $4 AND assignment.cluster = 'main'
     AND assignment.device_type IN ('heating', 'cooling', 'co2')
     AND assignment.device_name = s.device_name
     AND s.timestamp >= assignment.observed_at
     AND (assignment.next_observed_at IS NULL
          OR s.timestamp < assignment.next_observed_at)
    WHERE s.location = $4 AND s.cluster = 'main'
      AND s.timestamp >= $2 AND s.timestamp < $3
    GROUP BY s.id, s.timestamp, s.device_name, s.pid_output
)
SELECT id, timestamp, device_name,
       CASE WHEN owner_count = 1 THEN device_id ELSE NULL END AS device_id,
       CASE WHEN owner_count = 1 THEN pid_output ELSE NULL END AS pid_output,
       owner_count,
       COUNT(*) OVER ()::bigint AS total_count
FROM relay_matched_pid_samples
ORDER BY timestamp, device_id NULLS LAST, device_name, id
LIMIT $5
"""



def _aggregated_pid_load_sql(table_name: str, interval_seconds: int) -> str:
    """Build a static aggregate query with a checked table and interval constant."""
    if table_name not in {
        "monitoring_automation_state_1min",
        "monitoring_automation_state_5min",
    }:
        raise ValueError("unsupported relay timeline aggregate table")
    if interval_seconds not in {60, 300}:
        raise ValueError("unsupported relay timeline aggregate interval")
    return _RELAY_ASSIGNMENT_INTERVALS + f"""
, relay_matched_pid_samples AS (
    SELECT aggregate.last_observed_at AS timestamp,
           aggregate.device_name,
           aggregate.pid_output_count,
           aggregate.pid_output_last,
           ownership.owner_count,
           ownership.device_id
    FROM {table_name} AS aggregate
    LEFT JOIN LATERAL (
        SELECT
            COUNT(DISTINCT assignment.device_id) FILTER (
                WHERE aggregate.bucket >= assignment.observed_at
                  AND (assignment.next_observed_at IS NULL
                       OR aggregate.bucket + INTERVAL '{interval_seconds} seconds'
                          <= assignment.next_observed_at)
            )::bigint AS owner_count,
            MIN(assignment.device_id) FILTER (
                WHERE aggregate.bucket >= assignment.observed_at
                  AND (assignment.next_observed_at IS NULL
                       OR aggregate.bucket + INTERVAL '{interval_seconds} seconds'
                          <= assignment.next_observed_at)
            ) AS device_id,
            BOOL_OR(assignment.device_id IS NOT NULL) AS has_candidate
        FROM relay_assignment_intervals AS assignment
        WHERE assignment.location = $4 AND assignment.cluster = 'main'
          AND assignment.device_type IN ('heating', 'cooling', 'co2')
          AND assignment.device_name = aggregate.device_name
          AND assignment.observed_at
              < aggregate.bucket + INTERVAL '{interval_seconds} seconds'
          AND (assignment.next_observed_at IS NULL
               OR assignment.next_observed_at > aggregate.bucket)
    ) AS ownership ON TRUE
    WHERE aggregate.location = $4 AND aggregate.cluster = 'main'
      AND aggregate.bucket >= $2 AND aggregate.bucket < $3
      AND aggregate.last_observed_at >= $2 AND aggregate.last_observed_at < $3
      AND ownership.has_candidate
)
SELECT timestamp, device_name,
       CASE WHEN owner_count = 1 THEN device_id ELSE NULL END AS device_id,
       CASE WHEN owner_count = 1 AND pid_output_count > 0
            THEN pid_output_last ELSE NULL END AS pid_output,
       owner_count,
       COUNT(*) OVER ()::bigint AS total_count
FROM relay_matched_pid_samples
ORDER BY timestamp, device_id NULLS LAST, device_name
LIMIT $5
"""
RELAY_1MIN_PID_LOAD_SQL = _aggregated_pid_load_sql("monitoring_automation_state_1min", 60)
RELAY_5MIN_PID_LOAD_SQL = _aggregated_pid_load_sql("monitoring_automation_state_5min", 300)


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
    points = [row for row in rows[:MAX_RELAY_LOAD_ROWS] if isinstance(row.get("timestamp"), datetime)]
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
                keep.add(min(finite_indices, key=lambda index: _requested_percent(group[index][1].get("pid_output")) or 0.0))
                keep.add(max(finite_indices, key=lambda index: _requested_percent(group[index][1].get("pid_output")) or 0.0))
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

    selected.sort(key=lambda entry: (entry[1]["timestamp"], entry[1].get("device_id") or -1, str(entry[1].get("device_name"))))
    if len(selected) > MAX_RELAY_LOAD_ROWS:
        selected = selected[:MAX_RELAY_LOAD_ROWS]
        truncated = True
    result = tuple(
        RelayTimelineLoadPoint(
            device_id=(
                row.get("device_id")
                if isinstance(row.get("device_id"), int)
                and not isinstance(row.get("device_id"), bool)
                else None
            ),
            device_name=str(row["device_name"]),
            timestamp=row["timestamp"],
            requested_percent=(
                None if _as_int(row.get("owner_count")) != 1 else _requested_percent(row.get("pid_output"))
            ),
            aggregated=False,
            interval_seconds=1,
        )
        for _, row in selected
    )
    return result, truncated


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
        result.append(
            RelayTimelineLoadPoint(
                device_id=(
                    row.get("device_id")
                    if isinstance(row.get("device_id"), int)
                    and not isinstance(row.get("device_id"), bool)
                    else None
                ),
                device_name=device_name,
                timestamp=timestamp,
                requested_percent=(
                    None
                    if _as_int(row.get("owner_count")) != 1
                    else _requested_percent(row.get("pid_output"))
                ),
                aggregated=True,
                interval_seconds=interval_seconds,
            )
        )
    result.sort(key=lambda point: (point.timestamp, point.device_id or -1, point.device_name))
    return tuple(result), truncated


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
