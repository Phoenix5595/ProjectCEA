"""Control engine that orchestrates rules, schedules, and PID control."""

from __future__ import annotations

# Standard library imports
from datetime import datetime, timedelta
from typing import Any

from app.alarm_manager import AlarmManager
from app.automation.rules_engine import RulesEngine
from app.config import ConfigLoader
from app.control.device_controller import DeviceController
from app.control.device_processor import DeviceProcessor
from app.control.performance_monitor import get_performance_monitor
from app.control.pid_controller_manager import PIDControllerManager
from app.control.relay_manager import RelayManager
from app.control.scheduler import Scheduler, is_time_in_range
from app.control.sensor_data_manager import SensorDataManager
from app.control.setpoint_manager import SetpointManager
from app.control.vpd_cascade_controller import (
    VPDCascadeController,
)
from app.database import DatabaseManager
from app.state import StateManager, get_state_manager

# Third-party imports
# (none in this file)
# Local imports
from shared.infra_logging import get_logger

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
        self.device_controller = DeviceController(relay_manager, database, dfr0971_manager)

        # Initialize new extracted components
        self.device_processor = DeviceProcessor(
            self.device_controller,
            database,
            dfr0971_manager,
            scheduler,
            pid_controller_manager=self.pid_controller_manager,
        )
        self.setpoint_manager = SetpointManager(database)

        # VPD Cascade Controller for intelligent actuator selection
        self.vpd_cascade_controller = VPDCascadeController(
            vpd_deadband=0.05,  # 0.05 kPa deadband
            kp=20.0,
            ki=0.5,
            kd=2.0,
        )

        # StateManager for fast in-memory state access (<1ms reads)
        self._state: StateManager = get_state_manager()

        # Ramp restoration will be done asynchronously after Redis is available
        # See _restore_ramps_on_startup() called from run()
        self._ramps_restored = False

        # Track automation context for logging
        self._automation_context: dict[tuple[str, str, str], dict[str, Any]] = {}

        # Track current climate mode per location/cluster
        self._current_climate_mode: dict[tuple[str, str], str] = {}

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

        # Throttle light effective_setpoints DB logging to reduce CPU/IO (log at most every 60s per device)
        self._last_light_effective_log: dict[tuple[str, str, str], datetime] = {}
        self._light_effective_log_interval_sec = 60

        # Performance optimizations
        self._device_hierarchy_cache: dict[str, dict[str, dict[str, dict[str, Any]]]] | None = None
        self._sensor_mapping_cache: dict[str, Any] | None = None
        self._cache_timestamp: datetime | None = None
        self._cache_ttl = timedelta(seconds=30)  # Cache for 30 seconds

        # Object pooling for memory optimization
        self._setpoint_data_pool: list[dict[str, Any]] = []
        self._sensor_values_pool: list[dict[str, float | None]] = []
        self._pool_size = 10  # Keep up to 10 objects in each pool

        # Performance profiling
        self._profiling_enabled = True
        self._performance_stats: dict[str, list[float]] = {
            "total_loop_time": [],
            "sensor_reading_time": [],
            "setpoint_calculation_time": [],
            "device_processing_time": [],
        }
        self._max_stats_history = 100  # Keep last 100 measurements

        logger.info("Control engine initialized")

    def _get_device_hierarchy(self) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        """Get cached device hierarchy or refresh if expired."""
        now = datetime.now()
        if (
            self._device_hierarchy_cache is None
            or self._cache_timestamp is None
            or now - self._cache_timestamp > self._cache_ttl
        ):
            self._device_hierarchy_cache = self.config.get_devices()
            self._cache_timestamp = now
            logger.debug("Refreshed device hierarchy cache")
        return self._device_hierarchy_cache

    def _get_sensor_mapping(self) -> dict[str, Any]:
        """Get cached sensor mapping or refresh if expired."""
        now = datetime.now()
        if (
            self._sensor_mapping_cache is None
            or self._cache_timestamp is None
            or now - self._cache_timestamp > self._cache_ttl
        ):
            self._sensor_mapping_cache = self.config.get_sensor_mapping()
            if self._cache_timestamp is None:  # Only update if not already set
                self._cache_timestamp = now
            logger.debug("Refreshed sensor mapping cache")
        return self._sensor_mapping_cache

    def _get_setpoint_data_from_pool(self) -> dict[str, Any]:
        """Get a setpoint data dict from the object pool or create new one."""
        if self._setpoint_data_pool:
            return self._setpoint_data_pool.pop()
        return {}

    def _return_setpoint_data_to_pool(self, obj: dict[str, Any]) -> None:
        """Return a setpoint data dict to the object pool."""
        if len(self._setpoint_data_pool) < self._pool_size:
            obj.clear()  # Clear before reusing
            self._setpoint_data_pool.append(obj)

    def _get_sensor_values_from_pool(self) -> dict[str, float | None]:
        """Get a sensor values dict from the object pool or create new one."""
        if self._sensor_values_pool:
            return self._sensor_values_pool.pop()
        return {}

    def _return_sensor_values_to_pool(self, obj: dict[str, float | None]) -> None:
        """Return a sensor values dict to the object pool."""
        if len(self._sensor_values_pool) < self._pool_size:
            obj.clear()  # Clear before reusing
            self._sensor_values_pool.append(obj)

    def _record_performance_stat(self, key: str, value: float) -> None:
        """Record a performance measurement."""
        if not self._profiling_enabled:
            return

        stats_list = self._performance_stats[key]
        stats_list.append(value)
        if len(stats_list) > self._max_stats_history:
            stats_list.pop(0)

    def get_performance_stats(self) -> dict[str, dict[str, float]]:
        """Get performance statistics summary."""
        stats = {}
        for key, values in self._performance_stats.items():
            if values:
                stats[key] = {
                    "avg": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                    "count": len(values),
                }
            else:
                stats[key] = {"avg": 0.0, "min": 0.0, "max": 0.0, "count": 0}
        return stats

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

        loop_start_time = datetime.now() if self._profiling_enabled else None

        current_time = datetime.now()

        # Get cached device hierarchy and sensor mapping (performance optimization)
        devices = self._get_device_hierarchy()
        sensor_mapping = self._get_sensor_mapping()

        # Debug logging removed

        # Process each location/cluster
        device_processing_start = datetime.now() if self._profiling_enabled else None

        for location, clusters in devices.items():
            for cluster, cluster_devices in clusters.items():
                if location in ["Veg Room", "Flower Room"]:
                    logger.debug(
                        f"Control loop processing {location}/{cluster} with {len(cluster_devices)} devices"
                    )

                # Get sensor values for this location/cluster (with timing)
                sensor_start = datetime.now() if self._profiling_enabled else None
                # Debug logging removed
                sensor_values = await self.sensor_data_manager.get_sensor_values(
                    location, cluster, sensor_mapping
                )
                # Debug logging removed
                if self._profiling_enabled and sensor_start:
                    sensor_time = (datetime.now() - sensor_start).total_seconds() * 1000
                    self._record_performance_stat("sensor_reading_time", sensor_time)

                # 1. Schedule resolution (DB -> Redis -> NIGHT fallback)
                # light_schedule = sun/moon bounds (light = master); climate_schedule = pre_day/pre_night.
                # Together they feed get_climate_mode for climate mode (setpoints only).
                # Light intensity is determined separately by scheduler from light (sun/moon) schedules.
                light_schedule = None
                climate_schedule = None
                current_mode = "NIGHT"  # Default fallback mode

                try:
                    light_schedule = await self.database.schedule_repo.get_room_light_schedule(
                        location, cluster
                    )
                    climate_schedule = await self.database.schedule_repo.get_climate_schedule(
                        location, cluster
                    )
                except Exception as e:
                    logger.info(
                        f"Database error fetching schedules for {location}/{cluster}: {e}. "
                        + "Falling back to Redis."
                    )
                    cached_schedule = None
                    automation_redis = self.database._automation_redis
                    if automation_redis:
                        cached_schedule = automation_redis.read_schedule_state(location, cluster)

                    if cached_schedule:
                        light_schedule = cached_schedule.get("room", cached_schedule)
                        climate_schedule = cached_schedule.get("climate", cached_schedule)
                    else:
                        logger.warning(
                            f"No cached schedule found for {location}/{cluster}. "
                            + "Forcing NIGHT mode for safety."
                        )

                # 2. Mode resolution (happens early)
                if light_schedule and climate_schedule:
                    mode_result = self.scheduler.get_climate_mode(
                        location,
                        cluster,
                        current_time,
                        light_schedule.get("day_start_time"),
                        light_schedule.get("day_end_time"),
                        climate_schedule.get("pre_day_duration"),
                        climate_schedule.get("pre_night_duration"),
                    )
                    if mode_result:
                        current_mode, _, _ = mode_result

                # Store new mode for transition detection
                climate_mode_key = (location, cluster)
                previous_mode = self._current_climate_mode.get(climate_mode_key)
                self._current_climate_mode[climate_mode_key] = current_mode

                # Also store mode in StateManager for cross-component access
                await self._state.set_mode(location, cluster, current_mode)

                # 3. Setpoint retrieval with StateManager cache
                # First try StateManager cache (fast <1ms read), fallback to database
                setpoint_data: dict[str, Any] | None = None

                # Try to get cached setpoints from StateManager
                cached_setpoints = await self._state.get_setpoint(location, cluster)
                if cached_setpoints:
                    # Use cached setpoints (fast path)
                    setpoint_data = cached_setpoints
                    logger.debug(f"StateManager cache hit for setpoints: {location}/{cluster}")
                else:
                    # Cache miss - fetch from database and populate cache
                    setpoint_data = await self.database.setpoint_repo.get_setpoint(
                        location, cluster, current_mode
                    )
                    if setpoint_data:
                        # Populate StateManager cache for future reads
                        await self._state.set_setpoint(
                            location,
                            cluster,
                            heating_setpoint=setpoint_data.get("heating_setpoint"),
                            cooling_setpoint=setpoint_data.get("cooling_setpoint"),
                            humidity=setpoint_data.get("humidity"),
                            co2=setpoint_data.get("co2"),
                        )
                        logger.debug(
                            f"Populated StateManager cache for setpoints: {location}/{cluster}"
                        )

                if not setpoint_data:
                    # Fallback to legacy mode=None
                    logger.debug(
                        f"No setpoint found for {location}/{cluster} mode={current_mode}, "
                        + "falling back to legacy mode=None"
                    )
                    setpoint_data = await self.database.setpoint_repo.get_setpoint(
                        location, cluster, None
                    )

                # Log setpoint retrieval for verification
                if setpoint_data:
                    mode_str = current_mode or "None (legacy)"
                    logger.debug(
                        f"Retrieved setpoints for {location}/{cluster} mode={mode_str}: "
                        + f"heating={setpoint_data.get('heating_setpoint')}, "
                        + f"cooling={setpoint_data.get('cooling_setpoint')}, "
                        + f"ramp_in_duration={setpoint_data.get('ramp_in_duration', 0)}"
                    )
                    if current_mode in ["PRE_DAY", "PRE_NIGHT"]:
                        ramp_in = setpoint_data.get("ramp_in_duration", 0) or 0
                        logger.debug(
                            f"Retrieved {current_mode} setpoint for {location}/{cluster}: "
                            + f"heating={setpoint_data.get('heating_setpoint')}, "
                            + f"cooling={setpoint_data.get('cooling_setpoint')}, "
                            + f"ramp_in_duration={ramp_in}min"
                        )

                # 4. Effective setpoint calculation and logging (moved outside conditional)
                effective_data = None
                if setpoint_data:
                    # Compute effective setpoints
                    effective_data = await self.setpoint_manager.compute_effective_setpoints(
                        location,
                        cluster,
                        current_time,
                        current_mode,
                        setpoint_data,
                        sensor_values,
                        previous_mode,
                    )

                    # Add current VPD for humidifier/dehumidifier (VPD-only control)
                    sensor_mapping = self._get_sensor_mapping()
                    location_sensors = sensor_mapping.get(location, {}) if sensor_mapping else {}
                    cluster_sensors = location_sensors.get(cluster, {})
                    vpd_sensor_name = cluster_sensors.get("vpd_sensor")
                    if vpd_sensor_name:
                        effective_data["current_vpd"] = sensor_values.get(vpd_sensor_name)
                    else:
                        effective_data["current_vpd"] = None

                    # Store in context
                    self._effective_setpoints[(location, cluster)] = effective_data

                    # Log to database immediately (before device processing)
                    await self.database.setpoint_repo.log_effective_setpoints(
                        location=location,
                        cluster=cluster,
                        device_name="Main",
                        mode=current_mode,
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

                # 5. Moon = 0%: use room sun bounds as single source of truth for lights (all relevant rooms have a schedule)
                is_sun = False
                if light_schedule:
                    sun_start = light_schedule.get("day_start_time")  # sun window start (HH:MM)
                    sun_end = light_schedule.get("day_end_time")  # sun window end (HH:MM)
                    if sun_start and sun_end:
                        try:
                            parts_s = sun_start.split(":")
                            parts_e = sun_end.split(":")
                            sun_start_min = int(parts_s[0]) * 60 + int(parts_s[1])
                            sun_end_min = int(parts_e[0]) * 60 + int(parts_e[1])
                            current_min = current_time.hour * 60 + current_time.minute
                            is_sun = is_time_in_range(current_min, sun_start_min, sun_end_min)
                        except (ValueError, IndexError):
                            pass

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

                # Log effective light intensities for all dimmable lights (throttled: I2C + scheduler + DB at most every 60s per device to reduce CPU)
                if self.dfr0971_manager:
                    for device_name, device_info in cluster_devices.items():
                        if (
                            device_info.get("dimming_enabled")
                            and device_info.get("dimming_type") == "dfr0971"
                        ):
                            board_id = device_info.get("dimming_board_id")
                            channel = device_info.get("dimming_channel")
                            if board_id is None or channel is None:
                                continue
                            log_key = (location, cluster, device_name)
                            last_log = self._last_light_effective_log.get(log_key)
                            should_log = (
                                last_log is None
                                or (current_time - last_log).total_seconds()
                                >= self._light_effective_log_interval_sec
                            )
                            if not should_log:
                                continue
                            # Only do I2C + scheduler + DB when throttle allows
                            current_intensity = (
                                self.dfr0971_manager.get_intensity(board_id, channel) or 0.0
                            )
                            intensity_details = self.scheduler.get_light_intensity_details(
                                location, cluster, device_name, current_time, current_intensity
                            )
                            if intensity_details:
                                await self.database.setpoint_repo.log_effective_setpoints(
                                    location=location,
                                    cluster=cluster,
                                    mode=current_mode,
                                    device_name=device_name,
                                    effective_light_intensity=intensity_details[
                                        "effective_intensity"
                                    ],
                                    nominal_light_intensity=intensity_details["nominal_intensity"],
                                    ramp_progress_light=intensity_details["ramp_progress"],
                                    timestamp=current_time,
                                )
                                self._last_light_effective_log[log_key] = current_time

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

    async def _get_sensor_values(
        self, location: str, cluster: str, sensor_mapping: dict[str, Any]
    ) -> dict[str, float | None]:
        """Get sensor values for a location/cluster.

        Args:
            location: Location name
            cluster: Cluster name
            sensor_mapping: Sensor mapping from config

        Returns:
            Dict mapping sensor names to values
        """
        sensor_values = {}

        location_sensors = sensor_mapping.get(location, {})
        cluster_sensors = location_sensors.get(cluster, {})

        for _sensor_type, sensor_name in cluster_sensors.items():
            if sensor_name:
                value = await self.database.sensor_repo.get_sensor_value(sensor_name)
                sensor_values[sensor_name] = value

        return sensor_values

    def _get_sensor_for_setpoint_type(
        self, location: str, cluster: str, setpoint_type: str
    ) -> str | None:
        """Get sensor name for a setpoint type.

        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Setpoint type (e.g., 'heating_setpoint', 'cooling_setpoint', 'vpd_setpoint', 'co2')

        Returns:
            Sensor name or None if not found
        """
        sensor_mapping = self.config.get_sensor_mapping()
        location_sensors = sensor_mapping.get(location, {})
        cluster_sensors = location_sensors.get(cluster, {})

        # Map setpoint types to sensor names
        if setpoint_type in ["heating_setpoint", "cooling_setpoint"]:
            return cluster_sensors.get("temperature_sensor")
        elif setpoint_type == "vpd" or setpoint_type == "vpd_setpoint":
            return cluster_sensors.get("vpd_sensor")
        elif setpoint_type == "co2":
            return cluster_sensors.get("co2_sensor")
        elif setpoint_type == "humidity" or setpoint_type == "humidity_setpoint":
            return cluster_sensors.get("humidity_sensor")
        else:
            logger.warning(f"Unknown setpoint_type: {setpoint_type}")
            return None

    # _compute_effective_setpoints method moved to SetpointManager

    async def _process_vpd_control(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        sensor_values: dict[str, float | None],
        current_time: datetime,
        context: dict[str, Any],
    ) -> None:
        """Process VPD-based control for dehumidifying devices (fans, dehumidifiers).

        When VPD is below setpoint, turn ON dehumidifying devices to increase VPD.
        When VPD is at or above setpoint, turn OFF dehumidifying devices.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_info: Device configuration
            sensor_values: Available sensor values
            current_time: Current time
            context: Automation context dict
        """
        try:
            # Get current mode to determine which setpoint to use
            current_mode_str = None
            if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                # Try to get current time-based mode from scheduler or Redis
                # For now, use default setpoint (mode=NULL)
                # TODO: Integrate with scheduler to get current DAY/NIGHT/TRANSITION mode
                pass

            # Get setpoint (use default/legacy for now, can be enhanced to use mode-based)
            setpoint_data = await self._state.get_setpoint(location, cluster)
            if not setpoint_data:
                return  # No setpoint configured

            vpd_setpoint = setpoint_data.get("vpd")
            if vpd_setpoint is None:
                return  # No VPD setpoint configured

            # Get VPD sensor name from mapping
            sensor_mapping = self.config.get_sensor_mapping()
            location_sensors = sensor_mapping.get(location, {})
            cluster_sensors = location_sensors.get(cluster, {})
            vpd_sensor_name = cluster_sensors.get("vpd_sensor")

            if not vpd_sensor_name:
                logger.debug(f"No VPD sensor mapping for {location}/{cluster}")
                return

            # Get current VPD value
            current_vpd = sensor_values.get(vpd_sensor_name)

            if current_vpd is None:
                # Try to get from Redis last good value
                if (
                    self.database._automation_redis
                    and self.database._automation_redis.redis_enabled
                ):
                    last_good = self.database._automation_redis.read_last_good_value(
                        cluster, vpd_sensor_name
                    )
                    if last_good:
                        hold_period = self.config.get("control.last_good_hold_period", 30)
                        is_valid, age = self.database._automation_redis.check_last_good_age(
                            cluster, vpd_sensor_name, hold_period
                        )
                        if is_valid:
                            current_vpd = last_good["value"]
                        else:
                            # Last good value expired
                            if self.alarm_manager:
                                self.alarm_manager.raise_alarm(
                                    location,
                                    cluster,
                                    f"{vpd_sensor_name}_offline",
                                    "critical",
                                    f"VPD sensor {vpd_sensor_name} offline for {age:.1f}s",
                                )
                            return
                    else:
                        return  # No VPD sensor value available
                else:
                    return  # No VPD sensor value and no Redis

            # Update last good value if sensor is valid
            if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                self.database._automation_redis.write_last_good_value(
                    cluster, vpd_sensor_name, current_vpd
                )

            # Control logic: If VPD < setpoint, turn ON dehumidifying device
            # If VPD >= setpoint, turn OFF dehumidifying device
            # Add small hysteresis to prevent rapid cycling (0.1 kPa)
            hysteresis = 0.1  # kPa

            current_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0
            target_state = 0

            if current_vpd < (vpd_setpoint - hysteresis):
                # VPD is below setpoint, need to increase VPD → turn ON dehumidifying device
                target_state = 1
                context["control_reason"] = "vpd_control"
            elif current_vpd >= (vpd_setpoint + hysteresis):
                # VPD is at or above setpoint → turn OFF dehumidifying device
                target_state = 0
                context["control_reason"] = "vpd_control"
            else:
                # VPD is within hysteresis band, maintain current state
                target_state = current_state
                context["control_reason"] = "vpd_control_hysteresis"

            # Set device state if changed
            if target_state != current_state:
                await self._set_device_state(
                    location,
                    cluster,
                    device_name,
                    target_state,
                    "auto",
                    f"vpd_control (VPD: {current_vpd:.2f}kPa, setpoint: {vpd_setpoint:.2f}kPa)",
                    sensor_values,
                )
                logger.info(
                    f"VPD control: {location}/{cluster}/{device_name} "
                    f"{'ON' if target_state == 1 else 'OFF'} "
                    f"(VPD: {current_vpd:.2f}kPa, setpoint: {vpd_setpoint:.2f}kPa)"
                )
        except Exception as e:
            logger.error(f"Error in VPD control for {location}/{cluster}/{device_name}: {e}")
            import traceback

            traceback.print_exc()

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

        # Log to database
        await self.database.device_repo.set_device_state(
            location, cluster, device_name, channel, bool(state), mode
        )
        await self.database.control_action_repo.log_control_action(
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

    async def _log_automation_state(self) -> None:
        """Log automation state for all devices."""
        devices = self.config.get_devices()

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

                    await self.database.control_action_repo.log_automation_state(
                        location,
                        cluster,
                        device_name,
                        current_state,
                        current_mode,
                        context.get("pid_output"),
                        context.get("duty_cycle_percent"),
                        context.get("active_rule_ids", []),
                        context.get("active_schedule_ids", []),
                        context.get("control_reason", "unknown"),
                        context.get("schedule_ramp_up_duration"),
                        context.get("schedule_ramp_down_duration"),
                        context.get("schedule_photoperiod_hours"),
                        context.get("pid_kp"),
                        context.get("pid_ki"),
                        context.get("pid_kd"),
                    )

    async def restore_ramp_state_from_database(self) -> None:
        """Handle ramp state on service startup.

        Light ramp state is time-based and resumes automatically by recalculating
        intensity from elapsed time since schedule start. Climate ramps (setpoints)
        are restored from Redis if available to ensure continuity of environmental
        transitions.
        """
        await self._restore_ramps_on_startup()
