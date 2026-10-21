"""Sensor reader for current database measurements."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import time
from typing import Any

from shared.cluster_topology import device_cluster_for, sensor_subclusters_for
from shared.infra_logging import get_logger
from shared.redis_keys import sensor_full, sensor_full_ts

logger = get_logger(__name__)


class SensorReader:
    """Reads configured current sensor values from the database."""

    def __init__(self, database: Any, state: Any, config: Any):
        """Initialize sensor reader.

        Args:
            database: Database manager instance for sensor queries
            state: StateManager for fast in-memory state access
            config: Service configuration, including the last-good hold period
        """
        self.database = database
        self.state = state
        self.config = config
        self._last_db_warning_at: float | None = None

    async def read_sensors(
        self, location: str, cluster: str, sensor_mapping: Mapping[str, Any]
    ) -> dict[str, float | None]:
        """Read all sensor values for a location/cluster."""
        room_values = await self.read_sensors_for_rooms([(location, cluster)], sensor_mapping)
        return room_values[(location, cluster)]

    async def read_sensors_for_rooms(
        self,
        rooms: Sequence[tuple[str, str]],
        sensor_mapping: Mapping[str, Any],
    ) -> dict[tuple[str, str], dict[str, float | None]]:
        """Read all requested room sensors using one batched database query."""
        names_by_room: dict[tuple[str, str], list[str]] = {}
        for location, cluster in rooms:
            location_sensors = sensor_mapping.get(location, {})
            cluster_sensors = location_sensors.get(cluster, {})
            names_by_room[(location, cluster)] = [name for name in cluster_sensors.values() if name]

        unique_names = list(
            dict.fromkeys(name for names in names_by_room.values() for name in names)
        )
        try:
            values = await self.database.sensor_repo.get_sensor_values_batch(
                unique_names, max_age_sec=self.config.get_last_good_hold_period()
            )
        except Exception as error:
            now = time.monotonic()
            if self._last_db_warning_at is None or now - self._last_db_warning_at >= 60.0:
                self._last_db_warning_at = now
                logger.warning(
                    "Sensor DB read failed; using Redis live values: %s",
                    error,
                )
            return {
                room: self._redis_values(room[0], sensor_names)
                for room, sensor_names in names_by_room.items()
            }

        return {
            room: {name: values.get(name) for name in sensor_names}
            for room, sensor_names in names_by_room.items()
        }

    def _redis_values(self, location: str, sensor_names: list[str]) -> dict[str, float | None]:
        """Read the freshest recent topology-qualified Redis values for the requested sensors."""
        values: dict[str, float | None] = dict.fromkeys(sensor_names)
        try:
            automation_redis = getattr(self.database, "_automation_redis", None)
            redis_client = getattr(automation_redis, "redis_client", None)
            if redis_client is None:
                return values
            now_ms = time.time() * 1000
            max_age_ms = self.config.get_last_good_hold_period() * 1000
            clusters = (*sensor_subclusters_for(location), device_cluster_for(location))
            for sensor_name in sensor_names:
                freshest: tuple[float, float] | None = None
                for cluster in clusters:
                    raw_value = redis_client.get(sensor_full(location, cluster, sensor_name))
                    raw_timestamp = redis_client.get(sensor_full_ts(location, cluster, sensor_name))
                    if raw_value is None or raw_timestamp is None:
                        continue

                    if isinstance(raw_value, bytes):
                        raw_value = raw_value.decode()
                    if isinstance(raw_timestamp, bytes):
                        raw_timestamp = raw_timestamp.decode()
                    value = float(raw_value)
                    timestamp_ms = float(raw_timestamp)
                    age_ms = now_ms - timestamp_ms
                    if 0 <= age_ms <= max_age_ms and (freshest is None or age_ms < freshest[0]):
                        freshest = (age_ms, value)

                values[sensor_name] = None if freshest is None else freshest[1]
        except Exception:
            return dict.fromkeys(sensor_names)

        return values

    def get_sensor_for_setpoint_type(
        self, location: str, cluster: str, setpoint_type: str, sensor_mapping: dict[str, Any]
    ) -> str | None:
        """Get sensor name for a setpoint type.

        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Setpoint type (e.g., 'heating_setpoint', 'cooling_setpoint', 'vpd_setpoint', 'co2')
            sensor_mapping: Sensor mapping from config

        Returns:
            Sensor name or None if not found
        """
        location_sensors = sensor_mapping.get(location, {})
        cluster_sensors = location_sensors.get(cluster, {})

        # Map setpoint types to sensor names
        if setpoint_type in ["heating_setpoint", "cooling_setpoint"]:
            return cluster_sensors.get("temperature_sensor")
        elif setpoint_type in ["vpd", "vpd_setpoint"]:
            return cluster_sensors.get("vpd_sensor")
        elif setpoint_type == "co2":
            return cluster_sensors.get("co2_sensor")
        elif setpoint_type in ["humidity", "humidity_setpoint"]:
            return cluster_sensors.get("humidity_sensor")
        else:
            logger.warning(f"Unknown setpoint_type: {setpoint_type}")
            return None
