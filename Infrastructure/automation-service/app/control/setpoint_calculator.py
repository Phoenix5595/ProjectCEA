"""Setpoint Calculator - Handles ramp interpolation for setpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class SetpointCalculator:
    """Calculates effective setpoints with ramp interpolation."""

    def __init__(self):
        """Initialize setpoint calculator."""
        pass

    async def calculate_setpoints(
        self,
        location: str,
        cluster: str,
        current_time: datetime,
        current_period_name: str,
        previous_period_name: str | None,
        setpoint_data: dict[str, Any] | None,
        sensor_values: dict[str, float | None],
        setpoint_manager: Any,
    ) -> dict[str, Any] | None:
        """Calculate effective setpoints applying ramps.

        Args:
            location: Location name
            cluster: Cluster name
            current_time: Current timestamp
            current_period_name: Current climate period name
            previous_period_name: Previous climate period name (for transition detection)
            setpoint_data: Setpoint data from climate period
            sensor_values: Current sensor values
            setpoint_manager: SetpointManager for computing effective setpoints

        Returns:
            Dict with effective setpoints or None if no setpoint data
        """
        if not setpoint_data:
            return None

        # Compute effective setpoints
        effective_data = await setpoint_manager.compute_effective_setpoints(
            location,
            cluster,
            current_time,
            current_period_name,
            setpoint_data,
            sensor_values,
            previous_period_name,
        )

        return effective_data

    def add_current_vpd(
        self,
        effective_data: dict[str, Any],
        location: str,
        cluster: str,
        sensor_values: dict[str, float | None],
        sensor_mapping: dict[str, Any],
    ) -> dict[str, Any]:
        """Add current VPD to effective data for humidifier/dehumidifier control.

        Args:
            effective_data: Effective setpoints dict to augment
            location: Location name
            cluster: Cluster name
            sensor_values: Current sensor values
            sensor_mapping: Sensor mapping from config

        Returns:
            Updated effective_data with current_vpd
        """
        location_sensors = sensor_mapping.get(location, {})
        cluster_sensors = location_sensors.get(cluster, {})
        vpd_sensor_name = cluster_sensors.get("vpd_sensor")

        if vpd_sensor_name:
            effective_data["current_vpd"] = sensor_values.get(vpd_sensor_name)
        else:
            effective_data["current_vpd"] = None

        return effective_data
