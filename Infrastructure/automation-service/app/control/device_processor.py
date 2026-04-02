"""Device processor component for automation control."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.control.device_control_context import build_initial_control_context
from app.control.device_controller import DeviceController
from app.control.hardware_batch import HardwareBatchExecutor
from app.control.light_decision import LightAuthorityResolver, LightDecision
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
        self.light_authority_resolver = LightAuthorityResolver()
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

        for device_name, device_info in cluster_devices.items():
            context: dict[str, Any] = build_initial_control_context(
                location,
                cluster,
                effective_data,
                current_mode,
                previous_climate_mode,
            )

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
                decision = self._build_light_decision(
                    location=location,
                    cluster=cluster,
                    device_name=device_name,
                    device_info=device_info,
                    current_time=current_time,
                    is_sun=is_sun,
                    failsafe_active=bool(context.get("failsafe_active", False)),
                )
                context["light_intensity"] = decision.effective_percent / 100.0
                context["light_decision"] = decision
                logger.debug(
                    f"Light decision {location}/{cluster}/{device_name}: "
                    f"{decision.effective_percent:.1f}% authority={decision.authority}"
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

            # Persist intended dimmer state so UI/Grafana reflects reality even when batching.
            # (Non-batch dimmer control writes to Redis inside DeviceController.)
            try:
                redis_client = getattr(self.database, "_automation_redis", None)
                if redis_client and getattr(result, "light_intents", None):
                    for device_key, intent in result.light_intents.items():
                        ok = bool(result.results.get(device_key))
                        if ok:
                            percent = int(intent.get("intensity_percent") or 0)
                        else:
                            percent = self.device_controller.get_last_applied_light_percent(
                                intent["location"], intent["cluster"], intent["device_name"]
                            )
                        self.device_controller.write_light_telemetry(
                            intent["location"],
                            intent["cluster"],
                            intent["device_name"],
                            percent,
                            intent.get("board_id"),
                            intent.get("channel"),
                        )
            except Exception as redis_err:
                logger.warning(f"Failed to persist batched light intents to Redis: {redis_err}")

        # Note: Light intensity logging is handled by ControlEngine.run_control_loop()
        # to ensure scheduler access for proper ramp calculations

    def _build_light_decision(
        self,
        *,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        current_time: datetime,
        is_sun: bool,
        failsafe_active: bool,
    ) -> LightDecision:
        scheduled_percent = 0.0
        nominal_percent: float | None = None
        ramp_progress: float | None = None

        if is_sun and self.scheduler:
            intensity_details = self.scheduler.get_light_intensity_details(
                location, cluster, device_name, current_time
            )
            if intensity_details:
                scheduled_percent = float(intensity_details["effective_intensity"])
                nominal_raw = intensity_details.get("nominal_intensity")
                nominal_percent = (
                    float(nominal_raw) if isinstance(nominal_raw, (int, float)) else None
                )
                ramp_raw = intensity_details.get("ramp_progress")
                ramp_progress = float(ramp_raw) if isinstance(ramp_raw, (int, float)) else None
            else:
                key = (location, cluster, device_name)
                now_ts = current_time.timestamp()
                last = self._last_no_schedule_log.get(key, 0.0)
                if now_ts - last >= self._no_schedule_log_interval_sec:
                    self._last_no_schedule_log[key] = now_ts
                    logger.warning(
                        f"No sun schedule for {location}/{cluster}/{device_name}; "
                        f"commanding 0%. Re-save room schedule in ZoneConfig."
                    )

        authority_device_info = dict(device_info)
        authority_device_info["_location"] = location
        authority_device_info["_cluster"] = cluster
        authority_device_info["_device_name"] = device_name
        return self.light_authority_resolver.resolve(
            current_time=current_time,
            device_info=authority_device_info,
            is_sun=is_sun,
            scheduled_percent=scheduled_percent,
            nominal_percent=nominal_percent,
            ramp_progress=ramp_progress,
            failsafe_active=failsafe_active,
        )
