"""Device processor component for automation control."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Any

from app.control.device_controller import DeviceController
from app.control.scheduler import Scheduler
from app.database import DatabaseManager
from app.hardware.dfr0971 import DFR0971Manager
from shared.logging import get_logger

logger = get_logger(__name__)

# Fallback day window when room has no schedule (mode stays NIGHT): 06:00–22:00 local
_FALLBACK_DAY_START = time(6, 0)
_FALLBACK_DAY_END = time(22, 0)
_LOCAL_TZ = ZoneInfo("America/Toronto")


class DeviceProcessor:
    """Handles processing of devices in the automation loop."""

    def __init__(
        self,
        device_controller: DeviceController,
        database: DatabaseManager,
        dfr0971_manager: DFR0971Manager | None = None,
        scheduler: Scheduler | None = None,
    ):
        """Initialize device processor.

        Args:
            device_controller: Device controller instance
            database: Database manager instance
            dfr0971_manager: Optional DFR0971 manager for light intensity
            scheduler: Optional scheduler for light intensity calculations
        """
        self.device_controller = device_controller
        self.database = database
        self.dfr0971_manager = dfr0971_manager
        self.scheduler = scheduler
        # Throttle "no schedule" warning to once per 5 min per device (avoid log spam / CPU)
        self._last_no_schedule_log: dict[tuple[str, str, str], float] = {}
        self._no_schedule_log_interval_sec = 300.0

    async def process_devices(
        self,
        location: str,
        cluster: str,
        cluster_devices: dict[str, dict[str, Any]],
        sensor_values: dict[str, float | None],
        current_time: datetime,
        effective_data: dict[str, Any] | None,
        current_mode: str | None,
    ) -> None:
        """Process all devices for a location/cluster.

        Args:
            location: Location name
            cluster: Cluster name
            cluster_devices: Dict of device_name -> device_info
            sensor_values: Current sensor values
            current_time: Current timestamp
            effective_data: Effective setpoint data
            current_mode: Current climate mode
        """
        # Process each device
        for device_name, device_info in cluster_devices.items():
            # Build context for device processing
            if effective_data:
                context = {
                    "effective_heating_setpoint": effective_data.get("effective_heating_setpoint"),
                    "effective_cooling_setpoint": effective_data.get("effective_cooling_setpoint"),
                    "effective_humidity_setpoint": effective_data.get(
                        "effective_humidity_setpoint"
                    ),
                    "effective_co2_setpoint": effective_data.get("effective_co2_setpoint"),
                    "effective_vpd_setpoint": effective_data.get("effective_vpd_setpoint"),
                    "failsafe_active": False,  # TODO: implement failsafe logic
                    "current_mode": current_mode,
                }
            else:
                context = {
                    "effective_heating_setpoint": None,
                    "effective_cooling_setpoint": None,
                    "effective_humidity_setpoint": None,
                    "effective_co2_setpoint": None,
                    "effective_vpd_setpoint": None,
                    "failsafe_active": False,
                    "current_mode": current_mode,
                }

            # Add light intensity for dimmable lights
            if device_info.get("dimming_enabled") and device_info.get("dimming_type") == "dfr0971":
                if self.scheduler:
                    intensity_details = self.scheduler.get_light_intensity_details(
                        location, cluster, device_name, current_time
                    )
                    if intensity_details:
                        # Convert percentage (0-100) to ratio (0.0-1.0) for control output
                        context["light_intensity"] = (
                            intensity_details["effective_intensity"] / 100.0
                        )
                        logger.debug(
                            f"Light {device_name} scheduled intensity: {intensity_details['effective_intensity']}%"
                        )
                    else:
                        key = (location, cluster, device_name)
                        now_ts = current_time.timestamp()
                        last = self._last_no_schedule_log.get(key, 0.0)
                        if now_ts - last >= self._no_schedule_log_interval_sec:
                            self._last_no_schedule_log[key] = now_ts
                            logger.warning(
                                f"No active light schedule for {location}/{cluster}/{device_name}; "
                                f"light will not be updated. Re-save room schedule in ZoneConfig or call "
                                f"POST /api/room-schedule/{location}/{cluster}/sync-from-mode-parameters"
                            )
                    # System design: every dimmable light has an active schedule; no fallback.

            await self.device_controller.process_device(
                location, cluster, device_name, device_info, sensor_values, current_time, context
            )

        # Note: Light intensity logging is handled by ControlEngine.run_control_loop()
        # to ensure scheduler access for proper ramp calculations
