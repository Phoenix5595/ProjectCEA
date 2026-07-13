"""Control engine that orchestrates rules, schedules, and PID control."""

from __future__ import annotations

# Standard library imports
import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
import json
from typing import Any

from app.alarm_manager import AlarmManager
from app.automation.rules_engine import RulesEngine
from app.config import ConfigLoader
from app.control.climate_resolver import ClimatePeriodResolver
from app.control.device_controller import DeviceController
from app.control.device_processor import DeviceProcessor
from app.control.engine_config_cache import EngineConfigCache
from app.control.light_effective_setpoint_logging import log_light_effective_intensities_for_cluster
from app.control.performance_monitor import get_performance_monitor
from app.control.pid_controller_manager import PIDControllerManager
from app.control.relay_manager import RelayManager
from app.control.scheduler import LOCAL_TZ, Scheduler
from app.control.sensor_data_manager import SensorDataManager
from app.control.sensor_reader import SensorReader
from app.control.setpoint_calculator import SetpointCalculator
from app.control.setpoint_manager import SetpointManager
from app.control.vpd_cascade_controller import (
    VPDCascadeController,
)
from app.database import DatabaseManager
from app.redis.schema import relay_raw_override_key
from app.state import StateManager, get_state_manager

# Third-party imports
# (none in this file)
# Local imports
from shared.infra_logging import get_logger
from shared.room_light_authority import is_moon_authority_mode

logger = get_logger(__name__)


class ControlEngine:
    """Main control engine that executes automation logic."""

    def __init__(
        self,
        relay_manager: RelayManager,
        database: DatabaseManager,
        config: ConfigLoader,
        scheduler: Scheduler,
        rules_engine: RulesEngine,
        alarm_manager: AlarmManager | None = None,
        dfr0971_manager: Any | None = None,  # DFR0971Manager (avoid circular import)
    ):
        """Initialize control engine.

        Args:
            relay_manager: Relay manager instance
            database: Database manager instance
            config: Config loader instance
            scheduler: Scheduler instance
            rules_engine: Rules engine instance
            alarm_manager: Optional alarm manager instance
            dfr0971_manager: Optional DFR0971 manager for light intensity logging
        """
        self.relay_manager = relay_manager
        self.database = database
        self.config = config
        self.scheduler = scheduler
        self.rules_engine = rules_engine
        self.alarm_manager = alarm_manager
        self.dfr0971_manager = dfr0971_manager

        # Initialize extracted components
        self.sensor_data_manager = SensorDataManager(database)
        self.pid_controller_manager = PIDControllerManager(database)
        control_cfg = config.get_control_config() if config is not None else {}
        self.device_controller = DeviceController(
            relay_manager,
            database,
            dfr0971_manager,
            binary_hysteresis=float(control_cfg.get("binary_hysteresis", 0.1)),
        )

        # VPD Cascade Controller for intelligent actuator selection (create before DeviceProcessor)
        self.vpd_cascade_controller = VPDCascadeController(
            vpd_deadband=0.05,  # 0.05 kPa deadband
            kp=20.0,
            ki=0.5,
            kd=2.0,
        )

        # Initialize new extracted components
        self.device_processor = DeviceProcessor(
            self.device_controller,
            database,
            dfr0971_manager,
            scheduler,
            pid_controller_manager=self.pid_controller_manager,
            vpd_cascade_controller=self.vpd_cascade_controller,
        )

        # StateManager for fast in-memory state access (<1ms reads)
        self._state: StateManager = get_state_manager()

        # Initialize SetpointManager AFTER _state is defined
        self.setpoint_manager = SetpointManager(
            database=database,
            redis_client=database._automation_redis,
            state_manager=self._state,
        )

        # Initialize pipeline components
        self.sensor_reader = SensorReader(database, self._state)
        self.climate_resolver = ClimatePeriodResolver(scheduler, self.setpoint_manager, self._state)
        self.setpoint_calculator = SetpointCalculator()

        # Ramp restoration will be done asynchronously after Redis is available
        # See _restore_ramps_on_startup() called from run()
        self._ramps_restored = False

        # Track automation context for logging
        self._automation_context: dict[tuple[str, str, str], dict[str, Any]] = {}

        # Track current climate mode per location/cluster
        self._current_climate_mode: dict[tuple[str, str], str] = {}

        # Track current climate period per location/cluster (for period transition detection)
        self._current_period_name: dict[tuple[str, str], str] = {}

        # Moon-authority rooms (drying, sleep): scheduled lighting is MOON; manual override still allowed.
        self._moon_authority_forced_moon: set[tuple[str, str]] = set()

        # Track effective setpoints per location/cluster
        # Format: (location, cluster) -> {
        #   'effective_heating_setpoint': float,
        #   'effective_cooling_setpoint': float,
        #   'nominal_heating_setpoint': float,
        #   'nominal_cooling_setpoint': float,
        #   'ramp_progress_heating': float or None,
        #   'ramp_progress_cooling': float or None
        # }
        self._effective_setpoints: dict[tuple[str, str], dict[str, Any]] = {}

        # Light effective setpoint logging interval (seconds per device).
        # With 1s control tick and 6 dimmers (3 DFR0971 boards × 2 channels):
        #   6 devices × 1/10s = 0.6 writes/s to effective_setpoints hypertable.
        # TimescaleDB compression after 7 days keeps storage manageable.
        # REVIEW: 2026-05-31 — 10s chosen for faster diagnostic visibility.
        # Original was 60s. If DB write throughput becomes a concern, revert to 30s or 60s.
        self._last_light_effective_log: dict[tuple[str, str, str], datetime] = {}
        self._light_effective_log_interval_sec = 10
        self._last_light_sun_schedule_gap_error: dict[tuple[str, str, str], float] = {}

        # Config snapshot cache (device tree + sensor mapping, shared TTL)
        self._config_cache = EngineConfigCache(ttl_seconds=30.0)

        # Performance profiling
        self._profiling_enabled = True
        self._performance_stats: dict[str, list[float]] = {
            "total_loop_time": [],
            "sensor_reading_time": [],
            "setpoint_calculation_time": [],
            "device_processing_time": [],
        }
        self._max_stats_history = 100  # Keep last 100 measurements

        self._pending_db_writes: list[Coroutine[Any, Any, Any]] = []

        logger.info("Control engine initialized")

    def _record_performance_stat(self, key: str, value: float) -> None:
        """Record a performance measurement."""
        if not self._profiling_enabled:
            return

        stats_list = self._performance_stats[key]
        stats_list.append(value)
        if len(stats_list) > self._max_stats_history:
            stats_list.pop(0)

    async def _is_moon_authority_room_mode(self, location: str, cluster: str) -> bool:
        """Return true when active room mode forces 24h MOON for scheduled lights (drying, sleep)."""
        active_mode = await self.database.room_mode_repo.get_active_mode(location, cluster)
        return is_moon_authority_mode((active_mode or {}).get("mode_name"))

    async def _restore_ramps_on_startup(self) -> None:
        """Restore active ramps from StateManager (Redis-backed) on startup."""
        try:
            # Set Redis client for StateManager if available
            redis_client = self.database._automation_redis
            if redis_client and redis_client.redis_enabled:
                self._state.set_redis_client(redis_client.redis_client)
                # Also sync RampManager's redis for backward compatibility
                self.setpoint_manager.ramp_manager.set_redis(redis_client)

            # Restore ramps via StateManager (which handles Redis fallback)
            persisted_ramps = await self._state.get_persisted_ramps()
            if persisted_ramps:
                from datetime import datetime

                now = datetime.now()
                restored = 0
                for ramp_data in persisted_ramps:
                    try:
                        location = ramp_data.get("location")
                        cluster = ramp_data.get("cluster")
                        setpoint_type = ramp_data.get("setpoint_type")
                        start_time = ramp_data.get("start_time")
                        duration = ramp_data.get("duration_minutes", 0)
                        start_value = ramp_data.get("start_value")
                        target_value = ramp_data.get("target_value")

                        if location and cluster and setpoint_type and start_time:
                            # Check if ramp is still active
                            from datetime import timedelta

                            end_time = start_time + timedelta(minutes=int(duration))
                            if now < end_time:
                                self.setpoint_manager.ramp_manager.start_ramp(
                                    location,
                                    cluster,
                                    setpoint_type,
                                    float(start_value or 0),
                                    float(target_value or 0),
                                    float(duration),
                                    start_time,
                                )
                                restored += 1
                    except Exception as ramp_err:
                        logger.warning(f"Failed to restore ramp {ramp_data}: {ramp_err}")

                if restored > 0:
                    logger.info(
                        f"Control engine startup: restored {restored} active ramp(s) via StateManager"
                    )
                else:
                    logger.info("Control engine startup: no active ramps to restore")
            else:
                logger.info("Control engine startup: no persisted ramps found")
        except Exception as e:
            logger.error(f"Failed to restore ramps on startup: {e}")
            self.setpoint_manager.ramp_manager.clear_all_ramps()

    async def run_control_loop(self) -> None:
        """Run one iteration of the control loop with performance profiling."""
        if not self._ramps_restored:
            await self._restore_ramps_on_startup()
            self._ramps_restored = True

        # Expire manual overrides before processing devices
        try:
            await self._expire_manual_overrides()
        except Exception as e:
            logger.error(f"Failed to expire manual overrides: {e}")

        # Expire raw channel overrides before processing devices
        try:
            await self._expire_raw_channel_overrides()
        except Exception as e:
            logger.error(f"Failed to expire raw channel overrides: {e}")

        loop_start_time = datetime.now() if self._profiling_enabled else None

        current_time = datetime.now(tz=LOCAL_TZ)

        # Get cached device hierarchy and sensor mapping (performance optimization)
        devices = await self._config_cache.get_device_hierarchy(self.config.get_devices)
        sensor_mapping = self._config_cache.get_sensor_mapping(self.config.get_sensor_mapping)

        # Debug logging removed

        # Process each location/cluster
        device_processing_start = datetime.now() if self._profiling_enabled else None

        for location, clusters in devices.items():
            for cluster, cluster_devices in clusters.items():
                if location in ["Veg Room", "Flower Room"]:
                    logger.debug(
                        f"Control loop processing {location}/{cluster} with {len(cluster_devices)} devices"
                    )

                # Pipeline Step 1: Read sensor values
                sensor_start = datetime.now() if self._profiling_enabled else None
                sensor_values = await self.sensor_reader.read_sensors(
                    location, cluster, sensor_mapping
                )
                if self._profiling_enabled and sensor_start:
                    sensor_time = (datetime.now() - sensor_start).total_seconds() * 1000
                    self._record_performance_stat("sensor_reading_time", sensor_time)

                # Pipeline Step 2: Resolve climate period
                period_data = await self.climate_resolver.resolve_period(
                    location, cluster, current_time, self.database
                )

                active_period = period_data["active_period"]
                current_period_name = period_data["current_period_name"]
                setpoint_data = period_data["setpoint_data"]

                # Detect period transition for logging
                period_key = (location, cluster)
                previous_period = self._current_period_name.get(period_key)
                if active_period:
                    self._current_period_name[period_key] = current_period_name
                    if previous_period and previous_period != current_period_name:
                        logger.info(
                            f"PERIOD CHANGE: {location}/{cluster} {previous_period} -> {current_period_name}"
                        )
                else:
                    logger.warning(
                        f"No climate period found for {location}/{cluster} at {period_data['time_str']}. "
                        + "Cannot compute setpoints."
                    )

                # Pipeline Step 3: Calculate effective setpoints
                effective_data = None
                if setpoint_data:
                    effective_data = await self.setpoint_calculator.calculate_setpoints(
                        location,
                        cluster,
                        current_time,
                        current_period_name,
                        previous_period,
                        setpoint_data,
                        sensor_values,
                        self.setpoint_manager,
                    )

                    if effective_data:
                        # Add current VPD for humidifier/dehumidifier control
                        effective_data = self.setpoint_calculator.add_current_vpd(
                            effective_data, location, cluster, sensor_values, sensor_mapping
                        )

                        # Store in context
                        self._effective_setpoints[(location, cluster)] = effective_data

                        # Log to database
                        await self._log_effective_setpoints(
                            location, cluster, current_period_name, effective_data, current_time
                        )

                # Derive current_mode and previous_mode for device_processor compatibility
                current_mode = current_period_name if active_period else "NO_PERIOD"
                climate_mode_key = (location, cluster)
                previous_mode = self._current_climate_mode.get(climate_mode_key)
                self._current_climate_mode[climate_mode_key] = current_mode

                # Calculate is_sun for light control
                is_sun = self.climate_resolver.calculate_is_sun(current_time, location, cluster)
                room_key = (location, cluster)
                if await self._is_moon_authority_room_mode(location, cluster):
                    if room_key not in self._moon_authority_forced_moon:
                        logger.info(
                            "Moon-authority room mode active for %s/%s: forcing scheduled light authority to MOON",
                            location,
                            cluster,
                        )
                    self._moon_authority_forced_moon.add(room_key)
                    is_sun = False
                elif room_key in self._moon_authority_forced_moon:
                    self._moon_authority_forced_moon.remove(room_key)

                # 6. Process devices (using extracted component)
                await self.device_processor.process_devices(
                    location,
                    cluster,
                    cluster_devices,
                    sensor_values,
                    current_time,
                    effective_data,
                    current_mode,
                    is_sun=is_sun,
                    previous_climate_mode=previous_mode,
                )

                await log_light_effective_intensities_for_cluster(
                    location=location,
                    cluster=cluster,
                    cluster_devices=cluster_devices,
                    current_time=current_time,
                    is_sun=is_sun,
                    scheduler=self.scheduler,
                    database=self.database,
                    last_light_effective_log=self._last_light_effective_log,
                    interval_sec=float(self._light_effective_log_interval_sec),
                    redis_client=getattr(self.database, "_automation_redis", None),
                    last_sun_schedule_gap_error=self._last_light_sun_schedule_gap_error,
                    sun_schedule_gap_error_interval_sec=60.0,
                )

        if self._pending_db_writes:
            _ = await asyncio.gather(*self._pending_db_writes, return_exceptions=True)
            self._pending_db_writes.clear()

        # Log automation state for all devices
        await self._log_automation_state()

        # Record performance statistics
        if self._profiling_enabled and loop_start_time:
            total_time = (datetime.now() - loop_start_time).total_seconds() * 1000
            self._record_performance_stat("total_loop_time", total_time)
            get_performance_monitor().record_operation("total_loop_time", total_time / 1000.0)

            if device_processing_start:
                device_time = (datetime.now() - device_processing_start).total_seconds() * 1000
                self._record_performance_stat("device_processing_time", device_time)
                get_performance_monitor().record_operation(
                    "device_processing_time", device_time / 1000.0
                )

    # _get_sensor_values moved to SensorReader
    # _get_sensor_for_setpoint_type moved to SensorReader
    # _compute_effective_setpoints method moved to SetpointManager
    # _process_vpd_control moved to device_controller

    async def _expire_manual_overrides(self) -> None:
        """Sweep and expire manual overrides whose timer has passed.

        Queries control_history for rows where manual_expires_at <= NOW(),
        reverts each device to auto mode (state=0), logs the transition,
        and clears the expiry flag so the row is not re-processed.
        """
        expired = await self.database.control_action_repo.get_expired_manual_overrides()
        for row in expired:
            await self._set_device_state(
                row["location"],
                row["cluster"],
                row["device_name"],
                0,
                "auto",
                "manual_timer_expired",
                {},
            )
            await self.database.control_action_repo.clear_manual_expiry(
                row["location"], row["cluster"], row["device_name"]
            )

    async def _expire_raw_channel_overrides(self) -> None:
        """Sweep and expire raw channel overrides whose timer has passed.

        Checks each channel 0-15 for an expired Redis override record
        (cea:relay:manual_override:{channel}) and turns the channel OFF
        plus deletes the key.
        """
        automation_redis = self.database._automation_redis
        if automation_redis is None or automation_redis.redis_client is None:
            return

        redis_client = automation_redis.redis_client

        for channel in range(16):
            key = relay_raw_override_key(channel)
            raw = await asyncio.to_thread(redis_client.get, key)
            if raw is None:
                continue

            payload = json.loads(str(raw))
            expires_at = datetime.fromisoformat(payload["expires_at"])
            if expires_at <= datetime.now(UTC):
                await self.relay_manager.set_channel_state(channel, 0)
                await asyncio.to_thread(redis_client.delete, key)
                logger.info(f"Raw channel {channel} manual override expired, turned OFF")

    async def _set_device_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        state: int,
        mode: str,
        reason: str,
        sensor_values: dict[str, float | None],
        setpoint: float | None = None,
        load_percent: float | None = None,
    ) -> None:
        """Set device state and log action.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            state: New state (0/1)
            mode: Control mode
            reason: Control reason
            sensor_values: Sensor values for logging
            setpoint: Setpoint value for logging
            load_percent: Optional PID load (0-100) for log display
        """
        current_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0

        # Set device state
        success, error_reason = self.relay_manager.set_device_state(
            location, cluster, device_name, state, mode
        )

        if not success:
            logger.warning(f"Failed to set device state: {error_reason}")
            if error_reason and "interlock" in error_reason.lower():
                reason = "interlock"

        # Get channel for logging
        channel = self.relay_manager.get_channel(location, cluster, device_name) or 0

        # Get sensor value for logging (use first available)
        sensor_value = None
        for value in sensor_values.values():
            if value is not None:
                sensor_value = value
                break

        self._pending_db_writes.append(
            self.database.device_repo.set_device_state(
                location, cluster, device_name, channel, bool(state), mode
            )
        )
        self._pending_db_writes.append(
            self.database.control_action_repo.log_control_action(
                location,
                cluster,
                device_name,
                channel,
                current_state,
                state,
                mode,
                reason,
                sensor_value,
                setpoint,
                load_percent=load_percent,
            )
        )

    async def _log_effective_setpoints(
        self,
        location: str,
        cluster: str,
        current_period_name: str,
        effective_data: dict[str, Any],
        current_time: datetime,
    ) -> None:
        """Log effective setpoints to database."""
        await self.database.setpoint_repo.log_effective_setpoints(
            location=location,
            cluster=cluster,
            device_name="Main",
            mode=current_period_name,
            effective_heating_setpoint=effective_data["effective_heating_setpoint"],
            effective_cooling_setpoint=effective_data["effective_cooling_setpoint"],
            effective_humidity_setpoint=effective_data["effective_humidity_setpoint"],
            effective_co2_setpoint=effective_data["effective_co2_setpoint"],
            effective_vpd_setpoint=effective_data["effective_vpd_setpoint"],
            nominal_heating_setpoint=effective_data["nominal_heating_setpoint"],
            nominal_cooling_setpoint=effective_data["nominal_cooling_setpoint"],
            nominal_humidity_setpoint=effective_data["nominal_humidity_setpoint"],
            nominal_co2_setpoint=effective_data["nominal_co2_setpoint"],
            nominal_vpd_setpoint=effective_data["nominal_vpd_setpoint"],
            ramp_progress_heating=effective_data["ramp_progress_heating"],
            ramp_progress_cooling=effective_data["ramp_progress_cooling"],
            ramp_progress_humidity=effective_data["ramp_progress_humidity"],
            ramp_progress_co2=effective_data["ramp_progress_co2"],
            ramp_progress_vpd=effective_data["ramp_progress_vpd"],
            timestamp=current_time,
        )

    # _log_light_intensities logic moved inline to run_control_loop

    async def _log_automation_state(self) -> None:
        """Log automation state for all devices using batch INSERT."""
        devices = await self.config.get_devices()

        current_time = datetime.now()
        records: list[dict[str, Any]] = []

        for location, clusters in devices.items():
            for cluster, cluster_devices in clusters.items():
                for device_name in cluster_devices.keys():
                    key = (location, cluster, device_name)
                    context = self._automation_context.get(key, {})

                    current_state = (
                        self.relay_manager.get_device_state(location, cluster, device_name) or 0
                    )
                    current_mode = (
                        self.relay_manager.get_device_mode(location, cluster, device_name) or "auto"
                    )

                    records.append(
                        {
                            "timestamp": current_time,
                            "location": location,
                            "cluster": cluster,
                            "device_name": device_name,
                            "device_state": current_state,
                            "device_mode": current_mode,
                            "pid_output": context.get("pid_output"),
                            "duty_cycle_percent": context.get("duty_cycle_percent"),
                            "active_rule_ids": context.get("active_rule_ids", []),
                            "active_schedule_ids": context.get("active_schedule_ids", []),
                            "control_reason": context.get("control_reason", "unknown"),
                            "schedule_ramp_up_duration": context.get("schedule_ramp_up_duration"),
                            "schedule_ramp_down_duration": context.get(
                                "schedule_ramp_down_duration"
                            ),
                            "schedule_photoperiod_hours": context.get("schedule_photoperiod_hours"),
                            "pid_kp": context.get("pid_kp"),
                            "pid_ki": context.get("pid_ki"),
                            "pid_kd": context.get("pid_kd"),
                        }
                    )

        if records:
            await self.database.control_action_repo.log_automation_state_batch(records)

    async def restore_ramp_state_from_database(self) -> None:
        """Handle ramp state on service startup.

        Light ramp state is time-based and resumes automatically by recalculating
        intensity from elapsed time since schedule start. Climate ramps (setpoints)
        are restored from Redis if available to ensure continuity of environmental
        transitions.
        """
        await self._restore_ramps_on_startup()
