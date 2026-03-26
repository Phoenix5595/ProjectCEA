"""Composition facade for all Redis operations.

This module provides RedisOperations class that holds references to all mixins
and delegates method calls to them. This replaces the previous monolithic
RedisOperations class with a cleaner composition pattern.

Usage:
    from app.redis.redis_operations import RedisOperations

    ops = RedisOperations(redis_client, stream_client, redis_enabled)
    ops.write_alarm(location, cluster, alarm_name, severity, message)
    ops.read_mode(location, cluster)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

# Import all mixins (relative imports for same package)
from app.redis.alarms import AlarmsMixin  # noqa: E402, PLC0415
from app.redis.failsafe import FailsafeMixin  # noqa: E402, PLC0415
from app.redis.heartbeat import HeartbeatMixin  # noqa: E402, PLC0415
from app.redis.lighting import LightingMixin  # noqa: E402, PLC0415
from app.redis.modes import ModesMixin  # noqa: E402, PLC0415
from app.redis.pid import PIDMixin  # noqa: E402, PLC0415
from app.redis.ramps import RampsMixin  # noqa: E402, PLC0415
from app.redis.schedules import SchedulesMixin  # noqa: E402, PLC0415
from app.redis.sensors import SensorsMixin  # noqa: E402, PLC0415
from app.redis.setpoints import SetpointsMixin  # noqa: E402, PLC0415
from app.redis.streams import StreamsMixin  # noqa: E402, PLC0415
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class RedisOperations:
    """Composition facade for all Redis operations.

    This class holds references to all mixin instances and delegates
    method calls to them. It provides a clean composition pattern
    instead of the previous monolithic approach.

    Mixins are initialized with redis client references when created.
    """

    def __init__(
        self,
        redis_client: redis.Redis | None,
        stream_client: redis.Redis | None,
        redis_enabled: bool,
        redis_ttl: int = 10,
    ) -> None:
        """Initialize RedisOperations with all mixins.

        Args:
            redis_client: Redis client for state operations
            stream_client: Redis client for stream operations (binary mode)
            redis_enabled: Whether Redis is enabled and connected
            redis_ttl: Default TTL for state keys in seconds
        """
        # Create mixin instances
        self.alarms = AlarmsMixin()
        self.failsafe = FailsafeMixin()
        self.heartbeat = HeartbeatMixin()
        self.lighting = LightingMixin()
        self.modes = ModesMixin()
        self.pid = PIDMixin()
        self.ramps = RampsMixin()
        self.schedules = SchedulesMixin()
        self.sensors = SensorsMixin()
        self.setpoints = SetpointsMixin()
        self.streams = StreamsMixin()

        # Set redis attributes on all mixins
        for mixin in [
            self.alarms,
            self.failsafe,
            self.heartbeat,
            self.lighting,
            self.modes,
            self.pid,
            self.ramps,
            self.schedules,
            self.sensors,
            self.setpoints,
            self.streams,
        ]:
            mixin.redis_client = redis_client
            mixin.redis_enabled = redis_enabled

        # Set stream client and TTL on streams mixin
        self.streams.stream_client = stream_client
        self.streams.redis_ttl = redis_ttl

        # Set stream client on setpoints mixin (for _write_effective_setpoints_to_stream)
        self.setpoints.stream_client = stream_client

    # ========================================================================
    # Alarms - delegate to AlarmsMixin
    # ========================================================================

    def write_alarm(
        self, location: str, cluster: str, alarm_name: str, severity: str, message: str
    ) -> bool:
        """Write an alarm to Redis."""
        return self.alarms.write_alarm(location, cluster, alarm_name, severity, message)

    def acknowledge_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        """Acknowledge an alarm."""
        return self.alarms.acknowledge_alarm(location, cluster, alarm_name)

    def read_alarms(self, location: str, cluster: str) -> dict[str, dict[str, Any]]:
        """Read all active alarms for a location/cluster."""
        return self.alarms.read_alarms(location, cluster)

    def clear_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        """Clear an alarm."""
        return self.alarms.clear_alarm(location, cluster, alarm_name)

    # ========================================================================
    # Modes - delegate to ModesMixin
    # ========================================================================

    def read_mode(self, location: str, cluster: str) -> str | None:
        """Read mode from Redis."""
        return self.modes.read_mode(location, cluster)

    def write_mode(self, location: str, cluster: str, mode: str, source: str = "api") -> bool:
        """Write mode to Redis."""
        return self.modes.write_mode(location, cluster, mode, source)

    # ========================================================================
    # Failsafe - delegate to FailsafeMixin
    # ========================================================================

    def read_failsafe(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Read failsafe state from Redis."""
        return self.failsafe.read_failsafe(location, cluster)

    def write_failsafe(
        self,
        location: str,
        cluster: str,
        reason: str,
        triggered_by: str,
        timestamp: int | None = None,
    ) -> bool:
        """Write failsafe state to Redis."""
        return self.failsafe.write_failsafe(location, cluster, reason, triggered_by, timestamp)

    def clear_failsafe(self, location: str, cluster: str) -> bool:
        """Clear failsafe state from Redis."""
        return self.failsafe.clear_failsafe(location, cluster)

    # ========================================================================
    # Heartbeat - delegate to HeartbeatMixin
    # ========================================================================

    def write_heartbeat(self, service_name: str) -> bool:
        """Write heartbeat for a service."""
        return self.heartbeat.write_heartbeat(service_name)

    def check_heartbeat(
        self, service_name: str, max_age_seconds: int = 5
    ) -> tuple[bool, float | None]:
        """Check if service heartbeat is fresh."""
        return self.heartbeat.check_heartbeat(service_name, max_age_seconds)

    # ========================================================================
    # Sensors - delegate to SensorsMixin
    # ========================================================================

    def write_last_good_value(
        self, cluster: str, sensor_name: str, value: float, timestamp: int | None = None
    ) -> bool:
        """Write last good sensor value to Redis."""
        return self.sensors.write_last_good_value(cluster, sensor_name, value, timestamp)

    def read_last_good_value(self, cluster: str, sensor_name: str) -> dict[str, Any] | None:
        """Read last good sensor value from Redis."""
        return self.sensors.read_last_good_value(cluster, sensor_name)

    def check_last_good_age(
        self, cluster: str, sensor_name: str, max_age_seconds: int = 30
    ) -> tuple[bool, float | None]:
        """Check if last good value is fresh."""
        return self.sensors.check_last_good_age(cluster, sensor_name, max_age_seconds)

    # ========================================================================
    # Setpoints - delegate to SetpointsMixin
    # ========================================================================

    def read_setpoint(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Read setpoint from Redis."""
        return self.setpoints.read_setpoint(location, cluster)

    def read_effective_setpoints(self, location: str, cluster: str) -> dict[str, float] | None:
        """Read effective setpoints from Redis."""
        return self.setpoints.read_effective_setpoints(location, cluster)

    def write_setpoint(
        self,
        location: str,
        cluster: str,
        heating_setpoint: float | None = None,
        cooling_setpoint: float | None = None,
        humidity: float | None = None,
        co2: float | None = None,
        source: str = "api",
    ) -> bool:
        """Write setpoint to Redis."""
        return self.setpoints.write_setpoint(
            location,
            cluster,
            heating_setpoint=heating_setpoint,
            cooling_setpoint=cooling_setpoint,
            humidity=humidity,
            co2=co2,
            source=source,
        )

    def write_effective_setpoints(
        self,
        location: str,
        cluster: str,
        effective_heating_setpoint: float | None = None,
        effective_cooling_setpoint: float | None = None,
        effective_humidity_setpoint: float | None = None,
        effective_co2_setpoint: float | None = None,
        effective_vpd_setpoint: float | None = None,
        device_name: str | None = None,
        effective_light_intensity: float | None = None,
        nominal_light_intensity: float | None = None,
        ramp_progress_light: float | None = None,
        nominal_heating_setpoint: float | None = None,
        nominal_cooling_setpoint: float | None = None,
        nominal_humidity_setpoint: float | None = None,
        nominal_co2_setpoint: float | None = None,
        nominal_vpd_setpoint: float | None = None,
        ramp_progress_heating: float | None = None,
        ramp_progress_cooling: float | None = None,
        ramp_progress_humidity: float | None = None,
        ramp_progress_co2: float | None = None,
        ramp_progress_vpd: float | None = None,
        mode: str | None = None,
    ) -> bool:
        """Write effective setpoints to Redis."""
        return self.setpoints.write_effective_setpoints(
            location,
            cluster,
            effective_heating_setpoint=effective_heating_setpoint,
            effective_cooling_setpoint=effective_cooling_setpoint,
            effective_humidity_setpoint=effective_humidity_setpoint,
            effective_co2_setpoint=effective_co2_setpoint,
            effective_vpd_setpoint=effective_vpd_setpoint,
            device_name=device_name,
            effective_light_intensity=effective_light_intensity,
            nominal_light_intensity=nominal_light_intensity,
            ramp_progress_light=ramp_progress_light,
            nominal_heating_setpoint=nominal_heating_setpoint,
            nominal_cooling_setpoint=nominal_cooling_setpoint,
            nominal_humidity_setpoint=nominal_humidity_setpoint,
            nominal_co2_setpoint=nominal_co2_setpoint,
            nominal_vpd_setpoint=nominal_vpd_setpoint,
            ramp_progress_heating=ramp_progress_heating,
            ramp_progress_cooling=ramp_progress_cooling,
            ramp_progress_humidity=ramp_progress_humidity,
            ramp_progress_co2=ramp_progress_co2,
            ramp_progress_vpd=ramp_progress_vpd,
            mode=mode,
        )

    def read_setpoint_source(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Read setpoint source information from Redis."""
        return self.setpoints.read_setpoint_source(location, cluster)

    def check_rate_limit(
        self, location: str, cluster: str, setpoint_type: str, max_per_second: int = 1
    ) -> bool:
        """Check if setpoint write is rate limited."""
        return self.setpoints.check_rate_limit(location, cluster, setpoint_type, max_per_second)

    # ========================================================================
    # Lighting - delegate to LightingMixin
    # ========================================================================

    def write_light_intensity(
        self,
        location: str,
        cluster: str,
        device_name: str,
        intensity: float,
        voltage: float,
        board_id: int,
        channel: int,
    ) -> bool:
        """Write light intensity to Redis."""
        return self.lighting.write_light_intensity(
            location, cluster, device_name, intensity, voltage, board_id, channel
        )

    def read_light_intensity(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Read light intensity from Redis."""
        return self.lighting.read_light_intensity(location, cluster, device_name)

    # ========================================================================
    # PID - delegate to PIDMixin
    # ========================================================================

    def read_pid_parameters(self, device_type: str) -> dict[str, Any] | None:
        """Read PID parameters from Redis cache."""
        return self.pid.read_pid_parameters(device_type)

    def write_pid_parameters(
        self,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        source: str = "api",
        updated_at: int | None = None,
    ) -> bool:
        """Write PID parameters to Redis cache."""
        return self.pid.write_pid_parameters(device_type, kp, ki, kd, source, updated_at)

    # ========================================================================
    # Ramps - delegate to RampsMixin
    # ========================================================================

    def write_ramp_state(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        current_effective_setpoint: float,
        ramp_start_timestamp: datetime,
        ramp_duration: int,
        target_setpoint: float,
    ) -> bool:
        """Write ramp state to Redis."""
        return self.ramps.write_ramp_state(
            location,
            cluster,
            setpoint_type,
            current_effective_setpoint,
            ramp_start_timestamp,
            ramp_duration,
            target_setpoint,
        )

    def read_ramp_state(
        self, location: str, cluster: str, setpoint_type: str
    ) -> dict[str, Any] | None:
        """Read ramp state from Redis."""
        return self.ramps.read_ramp_state(location, cluster, setpoint_type)

    def clear_ramp_state(self, location: str, cluster: str, setpoint_type: str) -> bool:
        """Clear ramp state from Redis."""
        return self.ramps.clear_ramp_state(location, cluster, setpoint_type)

    def persist_ramp(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        start_value: float,
        target_value: float,
        duration_minutes: int,
        start_time: datetime,
    ) -> bool:
        """Persist ramp state for recovery after restart."""
        return self.ramps.persist_ramp(
            location,
            cluster,
            setpoint_type,
            start_value,
            target_value,
            duration_minutes,
            start_time,
        )

    def get_persisted_ramps(self) -> list[dict[str, Any]]:
        """Get all persisted ramps for restoration."""
        return self.ramps.get_persisted_ramps()

    def clear_persisted_ramp(self, location: str, cluster: str, setpoint_type: str) -> bool:
        """Clear a persisted ramp after completion."""
        return self.ramps.clear_persisted_ramp(location, cluster, setpoint_type)

    # ========================================================================
    # Schedules - delegate to SchedulesMixin
    # ========================================================================

    def write_schedule_state(
        self, location: str, cluster: str, schedule_data: dict[str, Any]
    ) -> bool:
        """Write schedule state to Redis."""
        return self.schedules.write_schedule_state(location, cluster, schedule_data)

    def read_schedule_state(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Read schedule state from Redis."""
        return self.schedules.read_schedule_state(location, cluster)

    # ========================================================================
    # Streams - delegate to StreamsMixin
    # ========================================================================

    def write_to_stream(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: int,
        device_mode: str,
        pid_output: float | None = None,
        duty_cycle_percent: float | None = None,
        active_rule_ids: list[int] | None = None,
        active_schedule_ids: list[int] | None = None,
        control_reason: str | None = None,
    ) -> bool:
        """Write device state to Redis stream."""
        return self.streams.write_to_stream(
            location,
            cluster,
            device_name,
            device_state,
            device_mode,
            pid_output=pid_output,
            duty_cycle_percent=duty_cycle_percent,
            active_rule_ids=active_rule_ids,
            active_schedule_ids=active_schedule_ids,
            control_reason=control_reason,
        )

    def write_to_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: int,
        device_mode: str,
        pid_output: float | None = None,
        duty_cycle_percent: float | None = None,
    ) -> bool:
        """Write device state to Redis state key."""
        return self.streams.write_to_state(
            location,
            cluster,
            device_name,
            device_state,
            device_mode,
            pid_output=pid_output,
            duty_cycle_percent=duty_cycle_percent,
        )
