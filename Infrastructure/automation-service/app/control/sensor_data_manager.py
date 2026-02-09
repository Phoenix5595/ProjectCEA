"""Sensor Data Manager - Handles sensor data retrieval and processing."""

from __future__ import annotations

import asyncio
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)


class SensorDataManager:
    """Manages sensor data retrieval and processing for control operations."""

    def __init__(self, database_manager):
        """Initialize sensor data manager.

        Args:
            database_manager: Database manager instance for sensor queries
        """
        self.database = database_manager
        self._sensor_cache: dict[str, dict[str, float | None]] = {}
        self._cache_timestamp: float | None = None
        self._cache_ttl_seconds = 30.0  # Cache sensor values for 30 seconds

    async def get_sensor_values(
        self, location: str, cluster: str, sensor_mapping: dict[str, Any]
    ) -> dict[str, float | None]:
        """Get sensor values for a location/cluster with caching optimization.

        Args:
            location: Location name
            cluster: Cluster name
            sensor_mapping: Sensor mapping from config

        Returns:
            Dict mapping sensor names to values
        """
        cache_key = f"{location}/{cluster}"
        current_time = asyncio.get_event_loop().time()

        # Check cache validity
        cache_hit = (
            self._cache_timestamp
            and current_time - self._cache_timestamp < self._cache_ttl_seconds
            and cache_key in self._sensor_cache
        )

        # Debug logging removed

        if cache_hit:
            return self._sensor_cache[cache_key].copy()

        # Cache miss - fetch fresh data
        sensor_values = {}

        location_sensors = sensor_mapping.get(location, {})
        cluster_sensors = location_sensors.get(cluster, {})

        # Batch fetch sensor values for better performance
        sensor_names = [name for name in cluster_sensors.values() if name]
        if sensor_names:
            try:
                # Use batch retrieval if available, otherwise fetch individually
                if hasattr(self.database, "get_sensor_values_batch"):
                    batch_values = await self.database.sensor_repo.get_sensor_values_batch(
                        sensor_names
                    )
                    sensor_values.update(batch_values)
                else:
                    # Fallback to individual fetches with concurrency
                    tasks = [
                        self.database.sensor_repo.get_sensor_value(sensor_name)
                        for sensor_name in sensor_names
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for sensor_name, result in zip(sensor_names, results, strict=True):
                        if isinstance(result, Exception):
                            logger.warning(
                                f"Failed to get sensor value for {sensor_name}: {result}"
                            )
                            sensor_values[sensor_name] = None
                        else:
                            sensor_values[sensor_name] = result

            except Exception as e:
                logger.error(f"Error fetching sensor values for {location}/{cluster}: {e}")

        # Update cache
        self._sensor_cache[cache_key] = sensor_values.copy()
        self._cache_timestamp = current_time

        return sensor_values

    def get_sensor_for_setpoint_type(
        self, sensor_mapping: dict[str, Any], location: str, cluster: str, setpoint_type: str
    ) -> str | None:
        """Get the appropriate sensor name for a setpoint type.

        Args:
            sensor_mapping: Sensor mapping configuration
            location: Location name
            cluster: Cluster name
            setpoint_type: Type of setpoint (heating, cooling, humidity, co2, vpd)

        Returns:
            Sensor name or None if not found
        """
        location_sensors = sensor_mapping.get(location, {})
        cluster_sensors = location_sensors.get(cluster, {})

        # Map setpoint types to sensor types
        sensor_type_mapping = {
            "heating": "temperature",
            "cooling": "temperature",
            "humidity": "humidity",
            "co2": "co2",
            "vpd": "vpd",  # VPD is calculated from temp/humidity
        }

        sensor_type = sensor_type_mapping.get(setpoint_type)
        if sensor_type:
            return cluster_sensors.get(sensor_type)

        return None

    async def get_last_good_sensor_values(
        self,
        location: str,
        cluster: str,
        sensor_mapping: dict[str, Any],
        max_age_seconds: int = 300,
    ) -> dict[str, float | None]:
        """Get last good sensor values with age validation.

        Args:
            location: Location name
            cluster: Cluster name
            sensor_mapping: Sensor mapping configuration
            max_age_seconds: Maximum age in seconds for sensor values

        Returns:
            Dict of sensor names to values, None if too old or missing
        """
        values = await self.get_sensor_values(location, cluster, sensor_mapping)
        result = {}

        for sensor_name, value in values.items():
            if value is not None:
                # Check age of sensor value
                age_result = await self.database.check_last_good_age(sensor_name, max_age_seconds)
                if age_result and age_result[0]:  # is_valid
                    result[sensor_name] = value
                    age_seconds = age_result[1].total_seconds() if age_result[1] else 0
                    logger.debug(
                        f"Using last good value for {sensor_name}: {value} (age: {age_seconds:.1f}s)"
                    )
                else:
                    logger.warning(f"Sensor value for {sensor_name} is too old or invalid")
                    result[sensor_name] = None
            else:
                result[sensor_name] = None

        return result

    def clear_cache(self) -> None:
        """Clear the sensor value cache."""
        self._sensor_cache.clear()
        self._cache_timestamp = None
        logger.debug("Sensor data cache cleared")

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics for monitoring."""
        return {
            "cache_size": len(self._sensor_cache),
            "cache_age_seconds": (
                asyncio.get_event_loop().time() - self._cache_timestamp
                if self._cache_timestamp
                else None
            ),
            "cache_ttl_seconds": self._cache_ttl_seconds,
        }
