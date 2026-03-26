"""Sensor Reader - Handles reading sensor values from Redis."""

from __future__ import annotations

from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class SensorReader:
    """Reads and validates sensor values from Redis."""

    def __init__(self, database: Any, state: Any):
        """Initialize sensor reader.

        Args:
            database: Database manager instance for sensor queries
            state: StateManager for fast in-memory state access
        """
        self.database = database
        self.state = state

    async def read_sensors(
        self, location: str, cluster: str, sensor_mapping: dict[str, Any]
    ) -> dict[str, float | None]:
        """Read all sensor values for a location/cluster.

        Args:
            location: Location name
            cluster: Cluster name
            sensor_mapping: Sensor mapping from config

        Returns:
            Dict mapping sensor names to values
        """
        sensor_values: dict[str, float | None] = {}

        location_sensors = sensor_mapping.get(location, {})
        cluster_sensors = location_sensors.get(cluster, {})

        for _sensor_type, sensor_name in cluster_sensors.items():
            if sensor_name:
                value = await self.database.sensor_repo.get_sensor_value(sensor_name)
                sensor_values[sensor_name] = value

        return sensor_values

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
