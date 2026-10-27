"""Read-only SQL for physical relay timelines: transitions, anchors, heartbeat, coverage and requested-PID load."""

from __future__ import annotations

RELAY_WATERMARK_SQL = """
SELECT COALESCE(MAX(observation_id), 0) AS watermark
FROM relay_observation
"""

RELAY_TRANSITIONS_FIRST_PAGE_SQL = """
WITH relay_timeline_page_source AS (
    SELECT
        ro.observation_id, ro."time" AS observed_at, ro.channel,
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
      AND ro."time" >= $2 AND ro."time" < $3
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
        ro.observation_id, ro."time" AS observed_at, ro.channel,
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
      AND ro."time" >= $2 AND ro."time" < $3
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
      AND (ro."time", ro.observation_id) > ($5, $6)
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
        ro.observation_id, ro."time" AS observed_at, ro.channel, ro.observed_state,
        ro.reason, ro.session_id, ro.registry_version, ro.device_id,
        ro.device_name, ro.device_type, ro.location, ro.cluster
    FROM relay_observation AS ro
    WHERE ro.channel IS NOT NULL
      AND ro."time" < $1
      AND ro.observation_id <= $2
    ORDER BY ro.channel, ro."time" DESC, ro.observation_id DESC
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
    SELECT observation_id, "time" AS observed_at, channel, observed_state, reason, session_id,
           registry_version, device_id, device_name, device_type, location, cluster,
           0::bigint AS window_count
    FROM relay_observation
    WHERE reason = 'heartbeat' AND "time" < $2 AND observation_id <= $1
    ORDER BY "time" DESC, observation_id DESC
    LIMIT 1
), relay_heartbeat_window AS (
    SELECT observation_id, "time" AS observed_at, channel, observed_state, reason, session_id,
           registry_version, device_id, device_name, device_type, location, cluster,
           COUNT(*) OVER ()::bigint AS window_count
    FROM relay_observation
    WHERE reason = 'heartbeat' AND "time" >= $2 AND "time" < $3
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
      AND "time" < $4
      AND location = $3 AND cluster = 'main'
      AND device_type IS DISTINCT FROM 'light'
), relay_channel_before_range AS (
    SELECT DISTINCT ON (ro.channel)
        ro.observation_id, ro."time" AS observed_at, ro.channel, ro.observed_state,
        ro.reason, ro.session_id, ro.device_id, ro.device_name, ro.device_type,
        ro.location, ro.cluster
    FROM relay_observation AS ro
    JOIN relay_candidate_channels AS candidate USING (channel)
    WHERE ro.observation_id <= $1 AND ro."time" < $2
    ORDER BY ro.channel, ro."time" DESC, ro.observation_id DESC
), relay_relevant_channels AS (
    SELECT channel
    FROM relay_channel_before_range
    WHERE location = $3 AND cluster = 'main'
      AND device_type IS DISTINCT FROM 'light'
    UNION
    SELECT DISTINCT channel
    FROM relay_observation
    WHERE observation_id <= $1
      AND "time" >= $2 AND "time" < $4
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
    SELECT ro.observation_id, ro."time" AS observed_at, ro.channel, ro.observed_state,
           ro.reason, ro.session_id, ro.device_id, ro.device_name, ro.device_type,
           ro.location, ro.cluster
    FROM relay_observation AS ro
    JOIN relay_relevant_channels AS relevant USING (channel)
    WHERE ro.observation_id <= $1
      AND ro."time" >= $2 AND ro."time" < $4
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
        ro.observation_id, ro."time" AS observed_at, ro.channel, ro.device_id,
        ro.device_name, ro.device_type, ro.location, ro.cluster, ro.reason
    FROM relay_observation AS ro
    WHERE ro.channel IS NOT NULL AND ro."time" < $2
      AND ro.observation_id <= $1
    ORDER BY ro.channel, ro."time" DESC, ro.observation_id DESC
), relay_assignment_history AS (
    SELECT * FROM relay_assignment_predecessors
    UNION ALL
    SELECT ro.observation_id, ro."time" AS observed_at, ro.channel, ro.device_id,
           ro.device_name, ro.device_type, ro.location, ro.cluster, ro.reason
    FROM relay_observation AS ro
    WHERE ro.channel IS NOT NULL
      AND ro."time" >= $2 AND ro."time" < $3
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
), relay_assignment_intervals AS MATERIALIZED (
    SELECT *, LEAD(observed_at) OVER (
        PARTITION BY channel ORDER BY observed_at, observation_id
    ) AS next_observed_at
    FROM relay_assignment_boundaries
)
"""

RELAY_RAW_PID_LOAD_SQL = (
    _RELAY_ASSIGNMENT_INTERVALS
    + """
, relay_matched_pid_samples AS (
    SELECT s.id, s."time" AS timestamp, s.device_name, s.pid_output,
           COUNT(DISTINCT assignment.device_id)::bigint AS owner_count,
           MIN(assignment.device_id) AS device_id
    FROM automation_state AS s
    JOIN relay_assignment_intervals AS assignment
      ON assignment.location = $4 AND assignment.cluster = 'main'
     AND assignment.device_type IN ('heating', 'cooling', 'co2')
     AND assignment.device_name = s.device_name
     AND s."time" >= assignment.observed_at
     AND (assignment.next_observed_at IS NULL
          OR s."time" < assignment.next_observed_at)
    WHERE s.location = $4 AND s.cluster = 'main'
      AND s."time" >= $2 AND s."time" < $3
    GROUP BY s.id, s."time", s.device_name, s.pid_output
)
SELECT id, timestamp, device_name,
       CASE WHEN owner_count = 1 THEN device_id ELSE NULL END AS device_id,
       CASE WHEN owner_count = 1 THEN pid_output ELSE NULL END AS pid_output,
       CASE WHEN owner_count = 1 THEN pid_output ELSE NULL END AS pid_output_min,
       CASE WHEN owner_count = 1 THEN pid_output ELSE NULL END AS pid_output_max,
       owner_count,
       COUNT(*) OVER ()::bigint AS total_count
FROM relay_matched_pid_samples
ORDER BY timestamp, device_id NULLS LAST, device_name, id
LIMIT $5
"""
)


def _aggregated_pid_load_sql(table_name: str, interval_seconds: int) -> str:
    """Build a static aggregate query with a checked table and interval constant."""
    if table_name not in {
        "monitoring_automation_state_1min",
        "monitoring_automation_state_5min",
    }:
        raise ValueError("unsupported relay timeline aggregate table")
    if interval_seconds not in {60, 300}:
        raise ValueError("unsupported relay timeline aggregate interval")
    return (
        _RELAY_ASSIGNMENT_INTERVALS
        + f"""
, relay_matched_pid_samples AS (
    SELECT aggregate.last_observed_at AS timestamp,
           aggregate.device_name,
           aggregate.pid_output_count,
           aggregate.pid_output_last,
           aggregate.pid_output_min,
           aggregate.pid_output_max,
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
       CASE WHEN owner_count = 1 THEN pid_output_min ELSE NULL END AS pid_output_min,
       CASE WHEN owner_count = 1 THEN pid_output_max ELSE NULL END AS pid_output_max,
       owner_count,
       COUNT(*) OVER ()::bigint AS total_count
FROM relay_matched_pid_samples
ORDER BY timestamp, device_id NULLS LAST, device_name
LIMIT $5
"""
    )


RELAY_1MIN_PID_LOAD_SQL = _aggregated_pid_load_sql("monitoring_automation_state_1min", 60)
RELAY_5MIN_PID_LOAD_SQL = _aggregated_pid_load_sql("monitoring_automation_state_5min", 300)
