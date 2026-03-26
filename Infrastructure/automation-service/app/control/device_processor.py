"""Device processor component for automation control."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.control.device_controller import DeviceController
from app.control.hardware_batch import HardwareBatchExecutor
from app.control.heating_safety import HeatingFailureSafety, SafetyState
from app.control.pid_controller_manager import PIDControllerManager
from app.control.scheduler import Scheduler
from app.control.vpd_cascade_controller import (
    ActuatorType,
    EnvironmentState,
    TempConstraints,
    VPDCascadeController,
)
from app.database import DatabaseManager
from app.hardware.dfr0971 import DFR0971Manager
from shared.infra_logging import get_logger

logger = get_logger(__name__)

# Fallback sun window when room has no schedule (climate stays NIGHT): 06:00–22:00 local
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
        pid_controller_manager: PIDControllerManager | None = None,
        vpd_cascade_controller: VPDCascadeController | None = None,
    ):
        """Initialize device processor.

        Args:
            device_controller: Device controller instance
            database: Database manager instance
            dfr0971_manager: Optional DFR0971 manager for light intensity
            scheduler: Optional scheduler for light intensity calculations
            pid_controller_manager: Optional PID controller manager for heating/cooling/CO2
            vpd_cascade_controller: Optional VPD cascade controller for intelligent actuator selection
        """
        self.device_controller = device_controller
        self.database = database
        self.dfr0971_manager = dfr0971_manager
        self.scheduler = scheduler
        self.pid_controller_manager = pid_controller_manager
        self.vpd_cascade_controller = vpd_cascade_controller
        # Throttle "no schedule" warning to once per 5 min per device (avoid log spam / CPU)
        self._last_no_schedule_log: dict[tuple[str, str, str], float] = {}
        self._no_schedule_log_interval_sec = 300.0
        # Heating failure safety: one instance per (location, cluster)
        self._heating_safety: dict[str, HeatingFailureSafety] = {}

    async def process_devices(
        self,
        location: str,
        cluster: str,
        cluster_devices: dict[str, dict[str, Any]],
        sensor_values: dict[str, float | None],
        current_time: datetime,
        effective_data: dict[str, Any] | None,
        current_mode: str | None,
        is_sun: bool = False,
        previous_climate_mode: str | None = None,
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
            is_sun: True if current time is inside room sun window (lights on); False = moon (lights off).
            previous_climate_mode: Previous climate mode for this location/cluster (for PID integrator reset).
        """
        batch_executor = HardwareBatchExecutor()

        # Track maximum heater demand across all heating devices for safety check
        max_heater_demand = 0.0

        for device_name, device_info in cluster_devices.items():
            # Build context for device processing
            context: dict[str, Any] = {}
            if effective_data:
                context = {
                    "effective_heating_setpoint": effective_data.get("effective_heating_setpoint"),
                    "effective_cooling_setpoint": effective_data.get("effective_cooling_setpoint"),
                    "effective_humidity_setpoint": effective_data.get(
                        "effective_humidity_setpoint"
                    ),
                    "effective_co2_setpoint": effective_data.get("effective_co2_setpoint"),
                    "effective_vpd_setpoint": effective_data.get("effective_vpd_setpoint"),
                    "current_vpd": effective_data.get("current_vpd"),
                    "failsafe_active": False,  # TODO: implement failsafe logic
                    "current_mode": current_mode,
                    "previous_climate_mode": {(location, cluster): previous_climate_mode}
                    if previous_climate_mode is not None
                    else {},
                }
            else:
                context = {
                    "effective_heating_setpoint": None,
                    "effective_cooling_setpoint": None,
                    "effective_humidity_setpoint": None,
                    "effective_co2_setpoint": None,
                    "effective_vpd_setpoint": None,
                    "current_vpd": None,
                    "failsafe_active": False,
                    "current_mode": current_mode,
                    "previous_climate_mode": {},
                }

            # PID path: only for heating, cooling, co2 (VPD is king: humidifier/dehumidifier use VPD only)
            if self.pid_controller_manager and not context.get("failsafe_active", False):
                device_type = device_info.get("device_type", "")
                device_mode = device_info.get("control_mode", "auto")
                if device_type in ["heating", "cooling", "co2"] and device_mode != "manual":
                    pid_output = await self.pid_controller_manager.process_pid_control(
                        location,
                        cluster,
                        device_name,
                        device_info,
                        sensor_values,
                        current_time,
                        context,
                        current_mode,
                    )
                    if pid_output is not None:
                        context["pid_output"] = pid_output
                        # Track max heater demand for safety check
                        if device_type == "heating":
                            max_heater_demand = max(max_heater_demand, pid_output)

            # VPD cascade path: for humidifier/dehumidifier with intelligent actuator selection
            device_type = device_info.get("device_type", "")
            device_mode = device_info.get("control_mode", "auto")
            if (
                self.vpd_cascade_controller
                and device_type in ["humidifier", "dehumidifier"]
                and device_mode != "manual"
            ):
                vpd_setpoint_raw = context.get("effective_vpd_setpoint")
                current_vpd = context.get("current_vpd")
                # Type guard: ensure values are valid numbers
                vpd_setpoint: float | None = None
                if isinstance(vpd_setpoint_raw, (int, float)):
                    vpd_setpoint = float(vpd_setpoint_raw)
                if vpd_setpoint is not None and isinstance(current_vpd, (int, float)):
                    # Build EnvironmentState from sensor values
                    air_temp = sensor_values.get("dry_bulb") or sensor_values.get("air_temp") or 0.0
                    humidity = sensor_values.get("humidity") or sensor_values.get("rh") or 50.0
                    outside_temp = sensor_values.get("outside_temp")
                    outside_humidity = sensor_values.get("outside_humidity")

                    env_state = EnvironmentState(
                        air_temp_c=float(air_temp) if air_temp is not None else 20.0,
                        humidity_pct=float(humidity) if humidity is not None else 50.0,
                        outside_temp_c=float(outside_temp) if outside_temp is not None else None,
                        outside_humidity_pct=float(outside_humidity)
                        if outside_humidity is not None
                        else None,
                    )

                    # Build TempConstraints from context with type safety
                    heating_setpoint_raw = context.get("effective_heating_setpoint")
                    cooling_setpoint_raw = context.get("effective_cooling_setpoint")
                    heating_setpoint = (
                        float(heating_setpoint_raw)
                        if isinstance(heating_setpoint_raw, (int, float))
                        else 20.0
                    )
                    cooling_setpoint = (
                        float(cooling_setpoint_raw)
                        if isinstance(cooling_setpoint_raw, (int, float))
                        else 30.0
                    )
                    temp_constraints = TempConstraints(
                        min_temp=10.0,
                        max_temp=40.0,
                        heating_setpoint=heating_setpoint,
                        cooling_setpoint=cooling_setpoint,
                    )

                    # Call VPD cascade controller
                    cascade_output = self.vpd_cascade_controller.update(
                        env=env_state,
                        target_vpd=vpd_setpoint,
                        temp_constraints=temp_constraints,
                        dt=1.0,  # 1 second control tick
                    )

                    # Store cascade output in context for device_controller
                    context["vpd_cascade_output"] = cascade_output

                    # Log cascade decision
                    if cascade_output.primary_command.actuator != ActuatorType.NONE:
                        logger.debug(
                            f"VPD cascade for {device_name}: {cascade_output.decision_reason} "
                            f"(actuator={cascade_output.primary_command.actuator.value}, "
                            f"output={cascade_output.primary_command.output_pct:.1f}%)"
                        )

            # Light intensity: moon = 0% from room sun bounds; sun = scheduler (ramps, target). All relevant rooms have a schedule.
            if device_info.get("dimming_enabled") and device_info.get("dimming_type") == "dfr0971":
                if not is_sun:
                    # Moon: lights off.
                    context["light_intensity"] = 0.0
                elif self.scheduler:
                    intensity_details = self.scheduler.get_light_intensity_details(
                        location, cluster, device_name, current_time
                    )
                    if intensity_details:
                        # Sun: use schedule (ramps, target). Convert 0-100% to 0.0-1.0
                        context["light_intensity"] = (
                            intensity_details["effective_intensity"] / 100.0
                        )
                        logger.debug(
                            f"Light {device_name} sun intensity: {intensity_details['effective_intensity']}%"
                        )
                    else:
                        # Sun window but no schedule details: command 0% (safe fallback)
                        context["light_intensity"] = 0.0
                        key = (location, cluster, device_name)
                        now_ts = current_time.timestamp()
                        last = self._last_no_schedule_log.get(key, 0.0)
                        if now_ts - last >= self._no_schedule_log_interval_sec:
                            self._last_no_schedule_log[key] = now_ts
                            logger.warning(
                                f"No sun schedule for {location}/{cluster}/{device_name}; "
                                f"commanding 0%. Re-save room schedule in ZoneConfig."
                            )

            await self.device_controller.process_device(
                location,
                cluster,
                device_name,
                device_info,
                sensor_values,
                current_time,
                context,
                batch_executor=batch_executor,
            )

        if batch_executor and batch_executor.pending_count > 0:
            result = await batch_executor.execute()
            if result.failure_count > 0:
                logger.warning(
                    f"Batch execution for {location}/{cluster}: "
                    f"{result.success_count} succeeded, {result.failure_count} failed"
                )
            else:
                logger.debug(
                    f"Batch execution for {location}/{cluster}: "
                    f"{result.success_count} devices processed"
                )

        # Heating failure safety check
        await self._check_heating_safety(
            location, cluster, sensor_values, max_heater_demand, current_time
        )

        # Note: Light intensity logging is handled by ControlEngine.run_control_loop()
        # to ensure scheduler access for proper ramp calculations

    async def _check_heating_safety(
        self,
        location: str,
        cluster: str,
        sensor_values: dict[str, float | None],
        heater_demand: float,
        current_time: datetime,
    ) -> None:
        """Check heating system safety and handle failures.

        Args:
            location: Location name
            cluster: Cluster name
            sensor_values: Current sensor values
            heater_demand: Current heater demand (0-100%)
            current_time: Current timestamp
        """
        # Get or create heating safety instance for this location/cluster
        safety_key = f"{location}:{cluster}"
        if safety_key not in self._heating_safety:
            self._heating_safety[safety_key] = HeatingFailureSafety(
                min_safe_temp=10.0,
                alert_callback=None,  # Use internal logging for now
            )
        heating_safety = self._heating_safety[safety_key]

        # Get current temperature (try dry_bulb first, then air_temp)
        current_temp = sensor_values.get("dry_bulb") or sensor_values.get("air_temp") or 0.0

        # Update safety monitor
        safety_state = heating_safety.update(current_temp, heater_demand, current_time)

        # Handle non-normal states
        if safety_state == SafetyState.WARNING:
            logger.warning(
                f"Heating safety WARNING for {location}/{cluster}: "
                f"temp={current_temp:.1f}°C, demand={heater_demand:.1f}%"
            )
        elif safety_state == SafetyState.CRITICAL:
            logger.critical(
                f"Heating safety CRITICAL for {location}/{cluster}: "
                f"temp={current_temp:.1f}°C, demand={heater_demand:.1f}% - "
                f"Heater not responding to demand"
            )
            # Inhibit exhaust fan to preserve heat
            # TODO: Implement exhaust inhibition via device controller
        elif safety_state == SafetyState.EMERGENCY:
            logger.critical(
                f"Heating safety EMERGENCY for {location}/{cluster}: "
                f"temp={current_temp:.1f}°C below emergency threshold! "
                f"Immediate action required."
            )
            # Trigger emergency response
            # TODO: Activate backup heating, close vents, send alerts
