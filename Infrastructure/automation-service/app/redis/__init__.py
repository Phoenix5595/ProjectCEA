"""Redis client module for automation service.

This module provides the AutomationRedisClient class which uses composition
to delegate all Redis operations to RedisOperations.

Usage:
    from app.redis import AutomationRedisClient
    # or for backward compatibility:
    from app.redis_client import AutomationRedisClient

    client = AutomationRedisClient()
    client.connect()

    # Direct method calls (delegated to ops)
    client.write_alarm(location, cluster, alarm_name, severity, message)
    client.read_mode(location, cluster)
    client.write_setpoint(location, cluster, heating_setpoint=22.0)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import redis

from app.redis.redis_operations import RedisOperations
from shared.infra_logging import get_logger
from shared.redis_client import close_sync, create_sync_client, redis_url_from_env

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class AutomationRedisClient:
    """Combined Redis client for automation service using composition.

    This class manages Redis connections and delegates all operations to
    RedisOperations. This replaces the previous mixin-based architecture
    with a cleaner composition pattern.

    Provides all Redis functionality for the automation control loop:
    - Connection management (connect, close)
    - Stream writes (sensor data, control data)
    - Setpoint management (read/write setpoints, effective setpoints)
    - Mode management (auto, manual, failsafe)
    - Failsafe state
    - Alarms
    - Heartbeats
    - Sensor last-good values
    - PID parameter cache
    - Light intensity
    - Ramp state
    - Schedule state

    Usage:
        client = AutomationRedisClient()
        client.connect()

        # Direct method calls (delegated to self.ops)
        client.write_mode("Flower Room", "main", "auto")
        client.read_setpoint("Flower Room", "main")
    """

    def __init__(self, redis_url: str | None = None, redis_ttl: int = 10) -> None:
        """Initialize Redis client.

        Args:
            redis_url: Redis connection URL. If None, uses environment variable or default.
            redis_ttl: TTL for Redis state keys in seconds (default: 10)
        """
        self.redis_url = redis_url or redis_url_from_env()
        self.redis_ttl = redis_ttl
        self.redis_client: redis.Redis | None = None
        self.stream_client: redis.Redis | None = None
        self._state_pool: redis.ConnectionPool | None = None
        self._stream_pool: redis.ConnectionPool | None = None
        self.redis_enabled = False

        # Create operations handler (will be initialized with actual clients after connect)
        self.ops = RedisOperations(None, None, False, redis_ttl)

    def connect(self) -> bool:
        """Connect to Redis with connection pooling for better performance.

        Creates two connection pools:
        - State pool (decode_responses=True) for state key operations
        - Stream pool (decode_responses=False) for binary stream writes

        Connection pooling improves performance by reusing connections
        instead of creating new ones for each operation.

        Returns:
            True if successful, False otherwise
        """
        try:
            self.redis_client, self._state_pool = create_sync_client(
                self.redis_url,
                decode_responses=True,
                max_connections=20,
                name="automation-redis-state",
            )
            self.stream_client, self._stream_pool = create_sync_client(
                self.redis_url,
                decode_responses=False,
                max_connections=10,
                name="automation-redis-stream",
            )
            self.redis_enabled = True
            self.ops = RedisOperations(self.redis_client, self.stream_client, True, self.redis_ttl)
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Will continue without Redis.")
            self.redis_enabled = False
            self.ops = RedisOperations(None, None, False, self.redis_ttl)
            return False

    def close(self) -> None:
        """Close Redis connections + pools (best-effort, SIGTERM-safe)."""
        close_sync(self.redis_client, self._state_pool, name="automation-redis-state")
        close_sync(self.stream_client, self._stream_pool, name="automation-redis-stream")
        self.redis_client = None
        self.stream_client = None
        self._state_pool = None
        self._stream_pool = None
        self.redis_enabled = False
        self.ops = RedisOperations(None, None, False, self.redis_ttl)

    # ========================================================================
    # Raw key access (for cache-aside patterns)
    # ========================================================================

    def get(self, key: str) -> Any:
        """Get a raw key from Redis. Returns None if key doesn't exist or Redis is disabled.

        Args:
            key: The Redis key to fetch.

        Returns:
            Value as str (decode_responses=True), or None.
        """
        if not self.redis_enabled or self.redis_client is None:
            return None
        return self.redis_client.get(key)

    # ========================================================================
    # Alarms - delegate to ops.alarms
    # ========================================================================

    def write_alarm(
        self, location: str, cluster: str, alarm_name: str, severity: str, message: str
    ) -> bool:
        """Write an alarm to Redis."""
        return self.ops.write_alarm(location, cluster, alarm_name, severity, message)

    def acknowledge_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        """Acknowledge an alarm."""
        return self.ops.acknowledge_alarm(location, cluster, alarm_name)

    def read_alarms(self, location: str, cluster: str) -> dict[str, dict[str, Any]]:
        """Read all active alarms for a location/cluster."""
        return self.ops.read_alarms(location, cluster)

    def clear_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        """Clear an alarm."""
        return self.ops.clear_alarm(location, cluster, alarm_name)

    # ========================================================================
    # Modes - delegate to ops.modes
    # ========================================================================

    def read_mode(self, location: str, cluster: str) -> str | None:
        """Read mode from Redis."""
        return self.ops.read_mode(location, cluster)

    def write_mode(self, location: str, cluster: str, mode: str, source: str = "api") -> bool:
        """Write mode to Redis."""
        return self.ops.write_mode(location, cluster, mode, source)

    # ========================================================================
    # Failsafe - delegate to ops.failsafe
    # ========================================================================

    def read_failsafe(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Read failsafe state from Redis."""
        return self.ops.read_failsafe(location, cluster)

    def write_failsafe(
        self,
        location: str,
        cluster: str,
        reason: str,
        triggered_by: str,
        timestamp: int | None = None,
    ) -> bool:
        """Write failsafe state to Redis."""
        return self.ops.write_failsafe(location, cluster, reason, triggered_by, timestamp)

    def clear_failsafe(self, location: str, cluster: str) -> bool:
        """Clear failsafe state from Redis."""
        return self.ops.clear_failsafe(location, cluster)

    # ========================================================================
    # Heartbeat - delegate to ops.heartbeat
    # ========================================================================

    def write_heartbeat(self, service_name: str) -> bool:
        """Write heartbeat for a service."""
        return self.ops.write_heartbeat(service_name)

    def check_heartbeat(
        self, service_name: str, max_age_seconds: int = 5
    ) -> tuple[bool, float | None]:
        """Check if service heartbeat is fresh."""
        return self.ops.check_heartbeat(service_name, max_age_seconds)

    # ========================================================================
    # Sensors - delegate to ops.sensors
    # ========================================================================

    def write_last_good_value(
        self, cluster: str, sensor_name: str, value: float, timestamp: int | None = None
    ) -> bool:
        """Write last good sensor value to Redis."""
        return self.ops.write_last_good_value(cluster, sensor_name, value, timestamp)

    def read_last_good_value(self, cluster: str, sensor_name: str) -> dict[str, Any] | None:
        """Read last good sensor value from Redis."""
        return self.ops.read_last_good_value(cluster, sensor_name)

    def check_last_good_age(
        self, cluster: str, sensor_name: str, max_age_seconds: int = 30
    ) -> tuple[bool, float | None]:
        """Check if last good value is fresh."""
        return self.ops.check_last_good_age(cluster, sensor_name, max_age_seconds)

    # ========================================================================
    # Setpoints - delegate to ops.setpoints
    # ========================================================================

    def read_setpoint(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Read setpoint from Redis."""
        return self.ops.read_setpoint(location, cluster)

    def read_effective_setpoints(self, location: str, cluster: str) -> dict[str, float] | None:
        """Read effective setpoints from Redis."""
        return self.ops.read_effective_setpoints(location, cluster)

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
        return self.ops.write_setpoint(
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
        return self.ops.write_effective_setpoints(
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
        return self.ops.read_setpoint_source(location, cluster)

    def check_rate_limit(
        self, location: str, cluster: str, setpoint_type: str, max_per_second: int = 1
    ) -> bool:
        """Check if setpoint write is rate limited."""
        return self.ops.check_rate_limit(location, cluster, setpoint_type, max_per_second)

    # ========================================================================
    # Lighting - delegate to ops.lighting
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
        return self.ops.write_light_intensity(
            location, cluster, device_name, intensity, voltage, board_id, channel
        )

    def read_light_intensity(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Read light intensity from Redis."""
        return self.ops.read_light_intensity(location, cluster, device_name)

    # ========================================================================
    # PID - delegate to ops.pid
    # ========================================================================

    def read_pid_parameters(self, device_type: str) -> dict[str, Any] | None:
        """Read PID parameters from Redis cache."""
        return self.ops.read_pid_parameters(device_type)

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
        return self.ops.write_pid_parameters(device_type, kp, ki, kd, source, updated_at)

    # ========================================================================
    # Ramps - delegate to ops.ramps
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
        return self.ops.write_ramp_state(
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
        return self.ops.read_ramp_state(location, cluster, setpoint_type)

    def clear_ramp_state(self, location: str, cluster: str, setpoint_type: str) -> bool:
        """Clear ramp state from Redis."""
        return self.ops.clear_ramp_state(location, cluster, setpoint_type)

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
        return self.ops.persist_ramp(
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
        return self.ops.get_persisted_ramps()

    def clear_persisted_ramp(self, location: str, cluster: str, setpoint_type: str) -> bool:
        """Clear a persisted ramp after completion."""
        return self.ops.clear_persisted_ramp(location, cluster, setpoint_type)

    # ========================================================================
    # Schedules - delegate to ops.schedules
    # ========================================================================

    def write_schedule_state(
        self, location: str, cluster: str, schedule_data: dict[str, Any]
    ) -> bool:
        """Write schedule state to Redis."""
        return self.ops.write_schedule_state(location, cluster, schedule_data)

    def read_schedule_state(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Read schedule state from Redis."""
        return self.ops.read_schedule_state(location, cluster)

    # ========================================================================
    # Streams - delegate to ops.streams
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
        return self.ops.write_to_stream(
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
        return self.ops.write_to_state(
            location,
            cluster,
            device_name,
            device_state,
            device_mode,
            pid_output=pid_output,
            duty_cycle_percent=duty_cycle_percent,
        )


# Backward compatibility: expose RedisOperations at module level for direct import
__all__ = ["AutomationRedisClient", "RedisOperations"]
