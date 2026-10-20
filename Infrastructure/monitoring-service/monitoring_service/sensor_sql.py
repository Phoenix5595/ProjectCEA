"""SQL statements for exact sensor read-model envelopes."""

from __future__ import annotations

from typing import Final

from monitoring_service.sensor_models import Tier


CAGGS: Final[dict[Tier, tuple[str, str]]] = {
    Tier.ONE_MINUTE: ("monitoring_measurement_1min", "1 minute"),
    Tier.FIVE_MINUTES: ("monitoring_measurement_5min", "5 minutes"),
}
CAGG_CHAINS: Final[dict[Tier, tuple[str, ...]]] = {
    Tier.ONE_MINUTE: ("monitoring_measurement_1min",),
    Tier.FIVE_MINUTES: ("monitoring_measurement_5min", "monitoring_measurement_1min"),
}
CAGG_BUCKET_SECONDS: Final[dict[str, int]] = {
    "monitoring_measurement_1min": 60,
    "monitoring_measurement_5min": 300,
}

_NODE_PATTERNS_CTE: Final[str] = """
WITH node_patterns(node, pattern) AS (
    VALUES ($2::text, $3::text), ($4::text, $5::text)
)
"""

RAW_SERIES_SQL: Final[str] = (
    _NODE_PATTERNS_CTE
    + """
SELECT node_patterns.node,
       time_bucket(INTERVAL '1 second', measurement.time) AS bucket,
       sensor.name AS sensor, sensor.unit, sensor.data_type,
       avg(measurement.value)::double precision AS average,
       min(measurement.value)::double precision AS minimum,
       max(measurement.value)::double precision AS maximum,
       count(*)::bigint AS sample_count
FROM measurement
JOIN sensor ON sensor.sensor_id = measurement.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
JOIN node_patterns ON sensor.name LIKE node_patterns.pattern
WHERE room.name = $1 AND node_patterns.pattern <> ''
  AND measurement.time >= $6 AND measurement.time < $7
GROUP BY node_patterns.node, bucket, sensor.name, sensor.unit, sensor.data_type
ORDER BY node_patterns.node, bucket, sensor.name
"""
)

RAW_BUCKETED_SERIES_SQL: Final[str] = (
    _NODE_PATTERNS_CTE
    + """
SELECT node_patterns.node,
       time_bucket($8::interval, measurement.time, $6::timestamptz) AS bucket,
       sensor.name AS sensor, sensor.unit, sensor.data_type,
       avg(measurement.value)::double precision AS average,
       min(measurement.value)::double precision AS minimum,
       max(measurement.value)::double precision AS maximum,
       count(*)::bigint AS sample_count
FROM measurement
JOIN sensor ON sensor.sensor_id = measurement.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
JOIN node_patterns ON sensor.name LIKE node_patterns.pattern
WHERE room.name = $1 AND node_patterns.pattern <> ''
  AND measurement.time >= $6 AND measurement.time < $7
GROUP BY node_patterns.node, bucket, sensor.name, sensor.unit, sensor.data_type
ORDER BY node_patterns.node, bucket, sensor.name
"""
)


def _source_rows_cte(
    relation: str,
    width: str,
    chain: tuple[str, ...],
    frontier: str,
    *,
    include_squares: bool = False,
) -> str:
    """Build exact CAGG/raw contributions, excluding pending chain invalidations."""
    chain_sql = ", ".join(f"'{name}'" for name in chain)

    def pending(bucket: str) -> str:
        return f"""EXISTS (
            SELECT 1
            FROM monitoring_cagg_watermark pending
            WHERE pending.cagg_name IN ({chain_sql})
              AND pending.invalidation_source IS NOT NULL
              AND pending.invalidation_start < {bucket} + INTERVAL '{width}'
              AND pending.invalidation_end >= {bucket}
        )"""

    cagg_squares = ", c.value_sum_squares" if include_squares else ""
    raw_squares = (
        ", sum(measurement.value::NUMERIC * measurement.value::NUMERIC) AS value_sum_squares"
        if include_squares
        else ""
    )
    raw_result_squares = ", raw.value_sum_squares" if include_squares else ""
    return f"""
, source_rows AS (
    SELECT c.bucket, c.sensor_id, c.sample_count, c.value_sum,
           c.min_value, c.max_value{cagg_squares}
    FROM {relation} c
    WHERE c.bucket >= $6 AND c.bucket < $7
      AND c.bucket <= {frontier}
      AND NOT {pending("c.bucket")}
    UNION ALL
    SELECT raw.bucket, raw.sensor_id, raw.sample_count, raw.value_sum,
           raw.min_value, raw.max_value{raw_result_squares}
    FROM (
        SELECT time_bucket(INTERVAL '{width}', measurement.time) AS bucket,
               measurement.sensor_id,
               count(measurement.value)::bigint AS sample_count,
               sum(measurement.value::NUMERIC) AS value_sum,
               min(measurement.value::NUMERIC) AS min_value,
               max(measurement.value::NUMERIC) AS max_value
               {raw_squares}
        FROM measurement
        WHERE measurement.time >= time_bucket(INTERVAL '{width}', $6::timestamptz)
          AND measurement.time < time_bucket(INTERVAL '{width}', $7::timestamptz)
                                  + INTERVAL '{width}'
        GROUP BY 1, 2
    ) raw
    WHERE raw.bucket >= $6 AND raw.bucket < $7
      AND (raw.bucket > {frontier} OR {pending("raw.bucket")})
)
"""


def _tiered_series_sql(relation: str, width: str, chain: tuple[str, ...]) -> str:
    return (
        _NODE_PATTERNS_CTE
        + _source_rows_cte(relation, width, chain, "$8")
        + """
SELECT node_patterns.node,
       c.bucket AS bucket, sensor.name AS sensor, sensor.unit, sensor.data_type,
       (c.value_sum / NULLIF(c.sample_count, 0))::double precision AS average,
       c.min_value::double precision AS minimum,
       c.max_value::double precision AS maximum,
       c.sample_count
FROM source_rows c
JOIN sensor ON sensor.sensor_id = c.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
JOIN node_patterns ON sensor.name LIKE node_patterns.pattern
WHERE room.name = $1 AND node_patterns.pattern <> ''
ORDER BY node_patterns.node, c.bucket, sensor.name
"""
    )


def _tiered_bucketed_series_sql(relation: str, width: str, chain: tuple[str, ...]) -> str:
    return (
        _NODE_PATTERNS_CTE
        + _source_rows_cte(relation, width, chain, "$9")
        + """
SELECT node_patterns.node,
       time_bucket($8::interval, c.bucket, $6::timestamptz) AS bucket,
       sensor.name AS sensor, sensor.unit, sensor.data_type,
       (sum(c.value_sum) / NULLIF(sum(c.sample_count), 0))::double precision AS average,
       min(c.min_value)::double precision AS minimum,
       max(c.max_value)::double precision AS maximum,
       sum(c.sample_count)::bigint AS sample_count
FROM source_rows c
JOIN sensor ON sensor.sensor_id = c.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
JOIN node_patterns ON sensor.name LIKE node_patterns.pattern
WHERE room.name = $1 AND node_patterns.pattern <> ''
GROUP BY node_patterns.node, time_bucket($8::interval, c.bucket, $6::timestamptz),
         sensor.name, sensor.unit, sensor.data_type
ORDER BY node_patterns.node, bucket, sensor.name
"""
    )


TIERED_STATEMENTS: Final[dict[str, str]] = {
    tier.value: _tiered_series_sql(relation, width, CAGG_CHAINS[tier])
    for tier, (relation, width) in CAGGS.items()
}
TIERED_BUCKETED_STATEMENTS: Final[dict[str, str]] = {
    tier.value: _tiered_bucketed_series_sql(relation, width, CAGG_CHAINS[tier])
    for tier, (relation, width) in CAGGS.items()
}

STATISTICS_SQL: Final[str] = (
    _NODE_PATTERNS_CTE
    + """
SELECT node_patterns.node, sensor.name AS sensor,
       min(measurement.value)::double precision AS minimum,
       max(measurement.value)::double precision AS maximum,
       avg(measurement.value)::double precision AS average,
       coalesce(stddev_samp(measurement.value), 0)::double precision AS stddev_samp,
       count(*)::bigint AS sample_count
FROM measurement
JOIN sensor ON sensor.sensor_id = measurement.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
JOIN node_patterns ON sensor.name LIKE node_patterns.pattern
WHERE room.name = $1 AND node_patterns.pattern <> ''
  AND measurement.time >= $6::timestamptz AND measurement.time < $7::timestamptz
GROUP BY node_patterns.node, sensor.name
ORDER BY node_patterns.node, sensor.name
"""
)
STATISTICS_CAGG_SQL: Final[str] = (
    _NODE_PATTERNS_CTE
    + _source_rows_cte(
        "monitoring_measurement_5min",
        "5 minutes",
        CAGG_CHAINS[Tier.FIVE_MINUTES],
        "$8",
        include_squares=True,
    )
    + """
SELECT node_patterns.node, sensor.name AS sensor,
       min(c.min_value)::double precision AS minimum,
       max(c.max_value)::double precision AS maximum,
       (sum(c.value_sum) / sum(c.sample_count))::double precision AS average,
       CASE WHEN sum(c.sample_count) > 1 THEN
           sqrt((sum(c.value_sum_squares) - power(sum(c.value_sum), 2) / sum(c.sample_count)) /
                (sum(c.sample_count) - 1))::double precision
       ELSE 0::double precision END AS stddev_samp,
       sum(c.sample_count)::bigint AS sample_count
FROM source_rows c
JOIN sensor ON sensor.sensor_id = c.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
JOIN node_patterns ON sensor.name LIKE node_patterns.pattern
WHERE room.name = $1 AND node_patterns.pattern <> ''
GROUP BY node_patterns.node, sensor.name
ORDER BY node_patterns.node, sensor.name
"""
)
