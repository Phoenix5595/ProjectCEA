"""SQL statements for exact sensor read-model envelopes."""

from __future__ import annotations

from typing import Final

from monitoring_service.sensor_models import Tier


CAGGS: Final[dict[Tier, tuple[str, str]]] = {
    Tier.ONE_MINUTE: ("monitoring_measurement_1min", "1 minute"),
    Tier.FIVE_MINUTES: ("monitoring_measurement_5min", "5 minutes"),
}

RAW_SERIES_SQL: Final = """
SELECT time_bucket(INTERVAL '1 second', measurement.time) AS bucket,
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
WHERE room.name = $1 AND ($2 = '' OR sensor.name LIKE $2)
  AND measurement.time >= $3 AND measurement.time < $4
GROUP BY bucket, sensor.name, sensor.unit, sensor.data_type
ORDER BY bucket, sensor.name
"""

RAW_BUCKETED_SERIES_SQL: Final = """
SELECT time_bucket($5::interval, measurement.time, $3::timestamptz) AS bucket,
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
WHERE room.name = $1 AND ($2 = '' OR sensor.name LIKE $2)
  AND measurement.time >= $3 AND measurement.time < $4
GROUP BY bucket, sensor.name, sensor.unit, sensor.data_type
ORDER BY bucket, sensor.name
"""

_TIERED_SERIES_SQL: Final[str] = """
SELECT c.bucket AS bucket, sensor.name AS sensor, sensor.unit, sensor.data_type,
       (c.value_sum / c.sample_count)::double precision AS average,
       c.min_value::double precision AS minimum,
       c.max_value::double precision AS maximum,
       c.sample_count
FROM {relation} c
JOIN sensor ON sensor.sensor_id = c.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
WHERE room.name = $1 AND ($2 = '' OR sensor.name LIKE $2)
  AND c.bucket >= $3 AND c.bucket < $4
  AND c.bucket + INTERVAL '{width}' <= (SELECT max(bucket) FROM {relation})
ORDER BY c.bucket, sensor.name
"""

_TIERED_BUCKETED_SERIES_SQL: Final[str] = """
SELECT time_bucket($5::interval, c.bucket, $3::timestamptz) AS bucket,
       sensor.name AS sensor, sensor.unit, sensor.data_type,
       (sum(c.value_sum) / NULLIF(sum(c.sample_count), 0))::double precision AS average,
       min(c.min_value)::double precision AS minimum,
       max(c.max_value)::double precision AS maximum,
       sum(c.sample_count)::bigint AS sample_count
FROM {relation} c
JOIN sensor ON sensor.sensor_id = c.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
WHERE room.name = $1 AND ($2 = '' OR sensor.name LIKE $2)
  AND c.bucket >= $3 AND c.bucket < $4
  AND c.bucket + INTERVAL '{width}' <= (SELECT max(bucket) FROM {relation})
GROUP BY bucket, sensor.name, sensor.unit, sensor.data_type
ORDER BY bucket, sensor.name
"""

TIERED_STATEMENTS: Final[dict[str, str]] = {
    tier.value: _TIERED_SERIES_SQL.format(relation=relation, width=width)
    for tier, (relation, width) in CAGGS.items()
}
TIERED_BUCKETED_STATEMENTS: Final[dict[str, str]] = {
    tier.value: _TIERED_BUCKETED_SERIES_SQL.format(relation=relation, width=width)
    for tier, (relation, width) in CAGGS.items()
}

STATISTICS_SQL: Final = """SELECT sensor.name AS sensor, min(measurement.value)::double precision AS minimum, max(measurement.value)::double precision AS maximum, avg(measurement.value)::double precision AS average, coalesce(stddev_samp(measurement.value),0)::double precision AS stddev_samp, count(*)::bigint AS sample_count FROM measurement JOIN sensor ON sensor.sensor_id=measurement.sensor_id JOIN device ON device.device_id=sensor.device_id LEFT JOIN rack ON rack.rack_id=device.rack_id JOIN room ON room.room_id=rack.room_id WHERE room.name=$1 AND ($2='' OR sensor.name LIKE $2) AND measurement.time >= $3::timestamptz AND measurement.time < $4::timestamptz GROUP BY sensor.name ORDER BY sensor.name"""
STATISTICS_CAGG_SQL: Final = """SELECT sensor.name AS sensor, min(c.min_value)::double precision AS minimum, max(c.max_value)::double precision AS maximum, (sum(c.value_sum) / sum(c.sample_count))::double precision AS average, coalesce(stddev_samp((c.value_sum / c.sample_count)::double precision),0)::double precision AS stddev_samp, sum(c.sample_count)::bigint AS sample_count FROM monitoring_measurement_5min c JOIN sensor ON sensor.sensor_id=c.sensor_id JOIN device ON device.device_id=sensor.device_id LEFT JOIN rack ON rack.rack_id=device.rack_id JOIN room ON room.room_id=rack.room_id WHERE room.name=$1 AND ($2='' OR sensor.name LIKE $2) AND c.bucket >= $3::timestamptz AND c.bucket < $4::timestamptz GROUP BY sensor.name ORDER BY sensor.name"""
