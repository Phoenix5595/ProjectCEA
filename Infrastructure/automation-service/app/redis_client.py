"""Redis client for automation service - writes to stream and state keys."""
from __future__ import annotations

from shared.logging import get_logger
import os
import json
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple, cast
import redis

logger = get_logger(__name__)


class AutomationRedisClient:
    """Redis client for automation service.
    
    Writes automation state to:
    - Redis Stream (sensor:raw) - recent history buffer
    - Redis state keys (automation:*) - live values for frontend
    
    Uses connection pooling for better performance and resource efficiency.
    """
    
    def __init__(self, redis_url: Optional[str] = None, redis_ttl: int = 10):
        """Initialize Redis client.
        
        Args:
            redis_url: Redis connection URL. If None, uses environment variable or default.
            redis_ttl: TTL for Redis state keys in seconds (default: 10)
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_ttl = redis_ttl
        self.redis_client: Optional[redis.Redis] = None
        self.stream_client: Optional[redis.Redis] = None  # Separate client for stream (binary mode)
        self._state_pool: Optional[redis.ConnectionPool] = None  # Connection pool for state client
        self._stream_pool: Optional[redis.ConnectionPool] = None  # Connection pool for stream client
        self.redis_enabled = False
    
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
            # Create connection pool for state keys (decode_responses=True)
            self._state_pool = redis.ConnectionPool.from_url(
                self.redis_url,
                decode_responses=True,
                max_connections=20,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            self.redis_client = redis.Redis(connection_pool=self._state_pool)
            self.redis_client.ping()
            
            # Create connection pool for stream writes (decode_responses=False for binary)
            self._stream_pool = redis.ConnectionPool.from_url(
                self.redis_url,
                decode_responses=False,
                max_connections=10,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            self.stream_client = redis.Redis(connection_pool=self._stream_pool)
            self.stream_client.ping()
            
            self.redis_enabled = True
            logger.info(f"Connected to Redis: {self.redis_url} (with connection pooling: state=20, stream=10)")
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Will continue without Redis.")
            self.redis_enabled = False
            return False
    
    def close(self) -> None:
        """Close Redis connections and disconnect connection pools."""
        if self.redis_client:
            try:
                self.redis_client.close()
            except Exception:
                pass
        if self.stream_client:
            try:
                self.stream_client.close()
            except Exception:
                pass
        if self._state_pool:
            try:
                self._state_pool.disconnect()
            except Exception:
                pass
        if self._stream_pool:
            try:
                self._stream_pool.disconnect()
            except Exception:
                pass
        self.redis_enabled = False
        logger.info("Redis connection closed")
    
    def write_to_stream(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: int,
        device_mode: str,
        pid_output: Optional[float] = None,
        duty_cycle_percent: Optional[float] = None,
        active_rule_ids: Optional[List[int]] = None,
        active_schedule_ids: Optional[List[int]] = None,
        control_reason: Optional[str] = None
    ) -> bool:
        """Write automation state to Redis Stream (sensor:raw).
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_state: Device state (0/1)
            device_mode: Control mode
            pid_output: PID output value
            duty_cycle_percent: Duty cycle percentage
            active_rule_ids: List of active rule IDs
            active_schedule_ids: List of active schedule IDs
            control_reason: Control reason
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.stream_client:
            return False
        
        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            
            # Create stream entry with type="automation" marker
            stream_data = {
                b'id': f"automation_{location}_{cluster}_{device_name}_{timestamp_ms}".encode(),
                b'ts': str(timestamp_ms).encode(),
                b'type': b'automation',  # Mark as automation data
                b'location': location.encode(),
                b'cluster': cluster.encode(),
                b'device_name': device_name.encode(),
                b'device_state': str(device_state).encode(),
                b'device_mode': device_mode.encode(),
            }
            
            # Add optional fields
            if pid_output is not None:
                stream_data[b'pid_output'] = str(pid_output).encode()
            if duty_cycle_percent is not None:
                stream_data[b'duty_cycle_percent'] = str(duty_cycle_percent).encode()
            if active_rule_ids:
                stream_data[b'active_rule_ids'] = json.dumps(active_rule_ids).encode()
            if active_schedule_ids:
                stream_data[b'active_schedule_ids'] = json.dumps(active_schedule_ids).encode()
            if control_reason:
                stream_data[b'control_reason'] = control_reason.encode()
            
            # Write to Redis Stream with automatic trimming (keep last 100,000 messages)
            self.stream_client.xadd('sensor:raw', stream_data, maxlen=100000, approximate=True)  # type: ignore
            return True
        except Exception as e:
            logger.warning(f"Error writing to Redis Stream: {e}")
            return False
    
    def write_to_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: int,
        device_mode: str,
        pid_output: Optional[float] = None,
        duty_cycle_percent: Optional[float] = None
    ) -> bool:
        """Write automation state to Redis state keys.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_state: Device state (0/1)
            device_mode: Control mode
            pid_output: PID output value
            duty_cycle_percent: Duty cycle percentage
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            
            # Create state key
            state_key = f"automation:{location}:{cluster}:{device_name}"
            
            # Create state object
            state_data = {
                'state': device_state,
                'mode': device_mode,
                'pid_output': pid_output,
                'duty_cycle_percent': duty_cycle_percent,
                'timestamp_ms': timestamp_ms
            }
            
            # Use pipeline for batch operations
            pipe = self.redis_client.pipeline()
            pipe.setex(state_key, self.redis_ttl, json.dumps(state_data))
            pipe.setex(f"{state_key}:ts", self.redis_ttl, str(timestamp_ms))
            pipe.execute()
            
            return True
        except Exception as e:
            logger.warning(f"Error writing to Redis state: {e}")
            return False
    
    # ========== Setpoint Management ==========
    
    def read_setpoint(self, location: str, cluster: str) -> Optional[Dict[str, Any]]:
        """Read setpoints from Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            Dict with heating_setpoint, cooling_setpoint, humidity, co2, source, timestamp_ms, or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            # Try reading individual keys first
            heat_key = f"setpoint:{location}:{cluster}:heating_setpoint"
            cool_key = f"setpoint:{location}:{cluster}:cooling_setpoint"
            # Backward compatibility: also check old temperature key
            temp_key = f"setpoint:{location}:{cluster}:temperature"
            hum_key = f"setpoint:{location}:{cluster}:humidity"
            co2_key = f"setpoint:{location}:{cluster}:co2"
            source_key = f"setpoint:{location}:{cluster}:source"
            
            # Use MGET for single round-trip instead of 6 individual GETs
            keys = [heat_key, cool_key, temp_key, hum_key, co2_key, source_key]
            values = self.redis_client.mget(keys)
            heat, cool, temp, hum, co2, source_data = cast(List[Any], values)  # type: ignore
            
            if heat is None and cool is None and temp is None and hum is None and co2 is None:
                return None
            
            result = {}
            if heat is not None:
                result['heating_setpoint'] = float(heat)
            elif temp is not None:
                # Backward compatibility: migrate old temperature key to heating_setpoint
                result['heating_setpoint'] = float(temp)
            if cool is not None:
                result['cooling_setpoint'] = float(cool)
            if hum is not None:
                result['humidity'] = float(hum)
            if co2 is not None:
                result['co2'] = float(co2)
            
            if source_data:
                try:
                    source_info = json.loads(source_data)
                    result['source'] = source_info.get('source', 'unknown')
                    result['timestamp_ms'] = source_info.get('timestamp', 0)
                except (json.JSONDecodeError, TypeError):
                    pass
            
            return result if result else None
        except Exception as e:
            logger.warning(f"Error reading setpoint from Redis: {e}")
            return None
    
    def write_setpoint(
        self,
        location: str,
        cluster: str,
        heating_setpoint: Optional[float] = None,
        cooling_setpoint: Optional[float] = None,
        humidity: Optional[float] = None,
        co2: Optional[float] = None,
        source: str = 'api'
    ) -> bool:
        """Write setpoints to Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            heating_setpoint: Heating setpoint (optional)
            cooling_setpoint: Cooling setpoint (optional)
            humidity: Humidity setpoint (optional)
            co2: CO2 setpoint (optional)
            source: Source of setpoint ('api', 'schedule', 'failsafe')
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            setpoint_ttl = 60  # 60 seconds for setpoints
            
            pipe = self.redis_client.pipeline()
            
            if heating_setpoint is not None:
                pipe.setex(f"setpoint:{location}:{cluster}:heating_setpoint", setpoint_ttl, str(heating_setpoint))
            if cooling_setpoint is not None:
                pipe.setex(f"setpoint:{location}:{cluster}:cooling_setpoint", setpoint_ttl, str(cooling_setpoint))
            if humidity is not None:
                pipe.setex(f"setpoint:{location}:{cluster}:humidity", setpoint_ttl, str(humidity))
            if co2 is not None:
                pipe.setex(f"setpoint:{location}:{cluster}:co2", setpoint_ttl, str(co2))
            
            # Write source information
            source_data = {
                'source': source,
                'timestamp': timestamp_ms
            }
            pipe.setex(f"setpoint:{location}:{cluster}:source", setpoint_ttl, json.dumps(source_data))
            
            pipe.execute()
            return True
        except Exception as e:
            logger.warning(f"Error writing setpoint to Redis: {e}")
            return False
    
    def write_effective_setpoints(
        self,
        location: str,
        cluster: str,
        effective_heating_setpoint: Optional[float] = None,
        effective_cooling_setpoint: Optional[float] = None,
        effective_humidity_setpoint: Optional[float] = None,
        effective_co2_setpoint: Optional[float] = None,
        effective_vpd_setpoint: Optional[float] = None,
        device_name: Optional[str] = None,
        effective_light_intensity: Optional[float] = None,
        nominal_light_intensity: Optional[float] = None,
        ramp_progress_light: Optional[float] = None,
        nominal_heating_setpoint: Optional[float] = None,
        nominal_cooling_setpoint: Optional[float] = None,
        nominal_humidity_setpoint: Optional[float] = None,
        nominal_co2_setpoint: Optional[float] = None,
        nominal_vpd_setpoint: Optional[float] = None,
        ramp_progress_heating: Optional[float] = None,
        ramp_progress_cooling: Optional[float] = None,
        ramp_progress_humidity: Optional[float] = None,
        ramp_progress_co2: Optional[float] = None,
        ramp_progress_vpd: Optional[float] = None,
        mode: Optional[str] = None,
    ) -> bool:
        """Write effective setpoints to Redis.

        Effective setpoints are the actual values being used by the control system,
        accounting for ramp transitions. These are updated every control step.

        Writes all 18 fields to Redis for real-time access:
        - Effective setpoints (actual values)
        - Nominal setpoints (target values)
        - Ramp progress (0.0-1.0 for each setpoint)
        - Device name for filtering in Grafana
        - Light intensity (if available)

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name for per-device logging (e.g., 'Main')
            effective_heating_setpoint: Effective heating setpoint (actual value being used)
            effective_cooling_setpoint: Effective cooling setpoint (actual value being used)
            effective_humidity_setpoint: Effective humidity setpoint (actual value being used)
            effective_co2_setpoint: Effective CO2 setpoint (actual value being used)
            effective_vpd_setpoint: Effective VPD setpoint (actual value being used)
            nominal_heating_setpoint: Nominal heating setpoint from database (reference value)
            nominal_cooling_setpoint: Nominal cooling setpoint from database (reference value)
            nominal_humidity_setpoint: Nominal humidity setpoint from database (reference value)
            nominal_co2_setpoint: Nominal CO2 setpoint from database (reference value)
            nominal_vpd_setpoint: Nominal VPD setpoint from database (reference value)
            ramp_progress_heating: Ramp progress for heating (0.0-1.0 or None if not ramping)
            ramp_progress_cooling: Ramp progress for cooling (0.0-1.0 or None if not ramping)
            ramp_progress_humidity: Ramp progress for humidity (0.0-1.0 or None if not ramping)
            ramp_progress_co2: Ramp progress for CO2 (0.0-1.0 or None if not ramping)
            ramp_progress_vpd: Ramp progress for VPD (0.0-1.0 or None if not ramping)
            effective_light_intensity: Effective light intensity (0-100%) after ramp
            nominal_light_intensity: Nominal/target light intensity from schedule
            ramp_progress_light: Ramp progress for light (0.0-1.0 or None if not ramping)

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            # Effective setpoints have longer TTL since they're updated every second
            setpoint_ttl = 300  # 5 minutes TTL (covers control loop intervals)

            pipe = self.redis_client.pipeline()

            # Write effective setpoints (actual values)
            if effective_heating_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:heating_setpoint", setpoint_ttl, str(effective_heating_setpoint))
            if effective_cooling_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:cooling_setpoint", setpoint_ttl, str(effective_cooling_setpoint))
            if effective_humidity_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:humidity", setpoint_ttl, str(effective_humidity_setpoint))
            if effective_co2_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:co2", setpoint_ttl, str(effective_co2_setpoint))
            if effective_vpd_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:vpd", setpoint_ttl, str(effective_vpd_setpoint))

            # Write nominal setpoints (target values)
            if nominal_heating_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:nominal_heating_setpoint", setpoint_ttl, str(nominal_heating_setpoint))
            if nominal_cooling_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:nominal_cooling_setpoint", setpoint_ttl, str(nominal_cooling_setpoint))
            if nominal_humidity_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:nominal_humidity_setpoint", setpoint_ttl, str(nominal_humidity_setpoint))
            if nominal_co2_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:nominal_co2_setpoint", setpoint_ttl, str(nominal_co2_setpoint))
            if nominal_vpd_setpoint is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:nominal_vpd_setpoint", setpoint_ttl, str(nominal_vpd_setpoint))

            # Write ramp progress values
            if ramp_progress_heating is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:ramp_progress_heating", setpoint_ttl, str(ramp_progress_heating))
            if ramp_progress_cooling is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:ramp_progress_cooling", setpoint_ttl, str(ramp_progress_cooling))
            if ramp_progress_humidity is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:ramp_progress_humidity", setpoint_ttl, str(ramp_progress_humidity))
            if ramp_progress_co2 is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:ramp_progress_co2", setpoint_ttl, str(ramp_progress_co2))
            if ramp_progress_vpd is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:ramp_progress_vpd", setpoint_ttl, str(ramp_progress_vpd))

            # Write device name (for Grafana filtering)
            if device_name is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:device_name", setpoint_ttl, device_name)

            # Write light intensity fields (if available)
            if effective_light_intensity is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:effective_light_intensity", setpoint_ttl, str(effective_light_intensity))
            if nominal_light_intensity is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:nominal_light_intensity", setpoint_ttl, str(nominal_light_intensity))
            if ramp_progress_light is not None:
                pipe.setex(f"effective_setpoint:{location}:{cluster}:ramp_progress_light", setpoint_ttl, str(ramp_progress_light))

            pipe.execute()
            
            # Also write to stream:control for dashboard visualization and DB writes (history/auditing)
            # State keys = fast truth for automation, Streams = history for dashboards/DB
            if self.stream_client:
                try:
                    timestamp_ms = int(datetime.now().timestamp() * 1000)
                    
                    # Write separate stream entries for each variable (following canonical schema)
                    # Each entry contains: room, variable, nominal, effective, ramp, mode
                    
                    if effective_heating_setpoint is not None:
                        stream_data = {
                            b'ts': str(timestamp_ms).encode(),
                            b'type': b'control',
                            b'room': location.encode(),
                            b'cluster': cluster.encode(),
                            b'variable': b'temp',
                            b'nominal': str(nominal_heating_setpoint or effective_heating_setpoint).encode(),
                            b'effective': str(effective_heating_setpoint).encode(),
                        }
                        if mode:
                            stream_data[b'mode'] = mode.encode()
                        if ramp_progress_heating is not None:
                            stream_data[b'ramp'] = str(ramp_progress_heating).encode()
                        self.stream_client.xadd('stream:control', stream_data, maxlen=100000, approximate=True)  # type: ignore
                    
                    if effective_cooling_setpoint is not None:
                        stream_data = {
                            b'ts': str(timestamp_ms).encode(),
                            b'type': b'control',
                            b'room': location.encode(),
                            b'cluster': cluster.encode(),
                            b'variable': b'cooling',
                            b'nominal': str(nominal_cooling_setpoint or effective_cooling_setpoint).encode(),
                            b'effective': str(effective_cooling_setpoint).encode(),
                        }
                        if mode:
                            stream_data[b'mode'] = mode.encode()
                        if ramp_progress_cooling is not None:
                            stream_data[b'ramp'] = str(ramp_progress_cooling).encode()
                        self.stream_client.xadd('stream:control', stream_data, maxlen=100000, approximate=True)  # type: ignore
                    
                    if effective_humidity_setpoint is not None:
                        stream_data = {
                            b'ts': str(timestamp_ms).encode(),
                            b'type': b'control',
                            b'room': location.encode(),
                            b'cluster': cluster.encode(),
                            b'variable': b'humidity',
                            b'nominal': str(nominal_humidity_setpoint or effective_humidity_setpoint).encode(),
                            b'effective': str(effective_humidity_setpoint).encode(),
                        }
                        if mode:
                            stream_data[b'mode'] = mode.encode()
                        if ramp_progress_humidity is not None:
                            stream_data[b'ramp'] = str(ramp_progress_humidity).encode()
                        self.stream_client.xadd('stream:control', stream_data, maxlen=100000, approximate=True)  # type: ignore
                    
                    if effective_co2_setpoint is not None:
                        stream_data = {
                            b'ts': str(timestamp_ms).encode(),
                            b'type': b'control',
                            b'room': location.encode(),
                            b'cluster': cluster.encode(),
                            b'variable': b'co2',
                            b'nominal': str(nominal_co2_setpoint or effective_co2_setpoint).encode(),
                            b'effective': str(effective_co2_setpoint).encode(),
                        }
                        if mode:
                            stream_data[b'mode'] = mode.encode()
                        if ramp_progress_co2 is not None:
                            stream_data[b'ramp'] = str(ramp_progress_co2).encode()
                        self.stream_client.xadd('stream:control', stream_data, maxlen=100000, approximate=True)  # type: ignore
                    
                    if effective_vpd_setpoint is not None:
                        stream_data = {
                            b'ts': str(timestamp_ms).encode(),
                            b'type': b'control',
                            b'room': location.encode(),
                            b'cluster': cluster.encode(),
                            b'variable': b'vpd',
                            b'nominal': str(nominal_vpd_setpoint or effective_vpd_setpoint).encode(),
                            b'effective': str(effective_vpd_setpoint).encode(),
                        }
                        if mode:
                            stream_data[b'mode'] = mode.encode()
                        if ramp_progress_vpd is not None:
                            stream_data[b'ramp'] = str(ramp_progress_vpd).encode()
                        self.stream_client.xadd('stream:control', stream_data, maxlen=100000, approximate=True)  # type: ignore
                    
                    if effective_light_intensity is not None:
                        stream_data = {
                            b'ts': str(timestamp_ms).encode(),
                            b'type': b'control',
                            b'room': location.encode(),
                            b'cluster': cluster.encode(),
                            b'variable': b'light',
                            b'nominal': str(nominal_light_intensity or effective_light_intensity).encode(),
                            b'effective': str(effective_light_intensity).encode(),
                        }
                        if mode:
                            stream_data[b'mode'] = mode.encode()
                        if ramp_progress_light is not None:
                            stream_data[b'ramp'] = str(ramp_progress_light).encode()
                        self.stream_client.xadd('stream:control', stream_data, maxlen=100000, approximate=True)  # type: ignore
                except Exception as e:
                    logger.debug(f"Error writing effective setpoints to stream: {e}")
            
            return True
        except Exception as e:
            logger.warning(f"Error writing setpoint to Redis: {e}")
            return False
    
    def read_setpoint_source(self, location: str, cluster: str) -> Optional[Dict[str, Any]]:
        """Read setpoint source information from Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            Dict with source and timestamp, or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            source_key = f"setpoint:{location}:{cluster}:source"
            source_data = self.redis_client.get(source_key)
            if source_data:
                return json.loads(str(source_data))
        except Exception as e:
            logger.debug(f"Error reading setpoint source: {e}")
        return None
    
    def check_rate_limit(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        max_per_second: int = 1
    ) -> bool:
        """Check if setpoint write is allowed (rate limiting).
        
        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Type of setpoint ('temperature', 'humidity', 'co2')
            max_per_second: Maximum writes per second (default: 1)
        
        Returns:
            True if write is allowed, False if rate limited
        """
        if not self.redis_enabled or not self.redis_client:
            return True  # Allow if Redis unavailable
        
        try:
            rate_limit_key = f"setpoint:{location}:{cluster}:{setpoint_type}:last_write"
            last_write_str = self.redis_client.get(rate_limit_key)
            
            if last_write_str is None:
                # No previous write, allow
                self.redis_client.setex(rate_limit_key, 2, str(int(datetime.now().timestamp() * 1000)))
                return True
            
            last_write_ms = int(str(last_write_str))
            now_ms = int(datetime.now().timestamp() * 1000)
            time_since_last = (now_ms - last_write_ms) / 1000.0
            
            if time_since_last >= (1.0 / max_per_second):
                # Enough time has passed, allow and update timestamp
                self.redis_client.setex(rate_limit_key, 2, str(now_ms))
                return True
            
            # Rate limited
            return False
        except Exception as e:
            logger.warning(f"Error checking rate limit: {e}")
            return True  # Allow on error
    
    # ========== Mode Management ==========
    
    def read_mode(self, location: str, cluster: str) -> Optional[str]:
        """Read mode from Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            Mode string ('auto', 'manual', 'override', 'failsafe') or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            mode_key = f"mode:{location}:{cluster}"
            mode = self.redis_client.get(mode_key)
            return str(mode) if mode else None
        except Exception as e:
            logger.warning(f"Error reading mode from Redis: {e}")
            return None
    
    def write_mode(
        self,
        location: str,
        cluster: str,
        mode: str,
        source: str = 'api'
    ) -> bool:
        """Write mode to Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            mode: Mode ('auto', 'manual', 'override', 'failsafe')
            source: Source of mode change ('api', 'system')
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            mode_key = f"mode:{location}:{cluster}"
            mode_ttl = 300  # 5 minutes for mode
            
            self.redis_client.setex(mode_key, mode_ttl, mode)
            logger.info(f"Mode set to {mode} for {location}/{cluster} (source: {source})")
            return True
        except Exception as e:
            logger.warning(f"Error writing mode to Redis: {e}")
            return False
    
    # ========== Failsafe Management ==========
    
    def read_failsafe(self, location: str, cluster: str) -> Optional[Dict[str, Any]]:
        """Read failsafe reason/details from Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            Dict with reason, triggered_by, since, or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            failsafe_key = f"failsafe:{location}:{cluster}"
            failsafe_data = self.redis_client.get(failsafe_key)
            if failsafe_data:
                return json.loads(str(failsafe_data))
        except Exception as e:
            logger.debug(f"Error reading failsafe: {e}")
        return None
    
    def write_failsafe(
        self,
        location: str,
        cluster: str,
        reason: str,
        triggered_by: str,
        timestamp: Optional[int] = None
    ) -> bool:
        """Write failsafe state to Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            reason: Failsafe reason ('sensor_offline', 'sensor_out_of_range', 'critical_alarm', etc.)
            triggered_by: What triggered the failsafe (e.g., 'co2_sensor', 'alarm_name')
            timestamp: Timestamp in milliseconds (default: current time)
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            failsafe_key = f"failsafe:{location}:{cluster}"
            timestamp_ms = timestamp or int(datetime.now().timestamp() * 1000)
            
            failsafe_data = {
                'reason': reason,
                'triggered_by': triggered_by,
                'since': timestamp_ms
            }
            
            # No TTL - failsafe persists until explicitly cleared
            self.redis_client.set(failsafe_key, json.dumps(failsafe_data))
            logger.warning(f"Failsafe triggered for {location}/{cluster}: {reason} (triggered by: {triggered_by})")
            return True
        except Exception as e:
            logger.warning(f"Error writing failsafe to Redis: {e}")
            return False
    
    def clear_failsafe(self, location: str, cluster: str) -> bool:
        """Clear failsafe state from Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            failsafe_key = f"failsafe:{location}:{cluster}"
            self.redis_client.delete(failsafe_key)
            logger.info(f"Failsafe cleared for {location}/{cluster}")
            return True
        except Exception as e:
            logger.warning(f"Error clearing failsafe: {e}")
            return False
    
    # ========== Alarm Management ==========
    
    def write_alarm(
        self,
        location: str,
        cluster: str,
        alarm_name: str,
        severity: str,
        message: str
    ) -> bool:
        """Write alarm to Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            alarm_name: Alarm identifier
            severity: Alarm severity ('info', 'warning', 'critical')
            message: Alarm message
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            alarm_key = f"alarm:{location}:{cluster}:{alarm_name}"
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            
            # Check if alarm already exists
            existing_data = self.redis_client.get(alarm_key)
            if existing_data:
                existing = json.loads(str(existing_data))
                since = existing.get('since', timestamp_ms)
            else:
                since = timestamp_ms
            
            alarm_data = {
                'active': True,
                'severity': severity,
                'message': message,
                'since': since,
                'acknowledged': False
            }
            
            # No TTL - alarms persist until explicitly cleared
            self.redis_client.set(alarm_key, json.dumps(alarm_data))
            
            if severity == 'critical':
                logger.error(f"CRITICAL ALARM: {location}/{cluster}/{alarm_name}: {message}")
            elif severity == 'warning':
                logger.warning(f"WARNING ALARM: {location}/{cluster}/{alarm_name}: {message}")
            else:
                logger.info(f"INFO ALARM: {location}/{cluster}/{alarm_name}: {message}")
            
            return True
        except Exception as e:
            logger.warning(f"Error writing alarm to Redis: {e}")
            return False
    
    def acknowledge_alarm(
        self,
        location: str,
        cluster: str,
        alarm_name: str
    ) -> bool:
        """Acknowledge an alarm.
        
        Args:
            location: Location name
            cluster: Cluster name
            alarm_name: Alarm identifier
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            alarm_key = f"alarm:{location}:{cluster}:{alarm_name}"
            alarm_data = self.redis_client.get(alarm_key)
            
            if alarm_data:
                alarm = json.loads(str(alarm_data))
                alarm['acknowledged'] = True
                self.redis_client.set(alarm_key, json.dumps(alarm))
                logger.info(f"Alarm acknowledged: {location}/{cluster}/{alarm_name}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Error acknowledging alarm: {e}")
            return False
    
    def read_alarms(self, location: str, cluster: str) -> Dict[str, Dict[str, Any]]:
        """Read all active alarms for a location/cluster.
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            Dict mapping alarm_name to alarm data
        """
        if not self.redis_enabled or not self.redis_client:
            return {}
        
        try:
            pattern = f"alarm:{location}:{cluster}:*"
            alarms = {}
            
            for key in self.redis_client.scan_iter(match=pattern):
                alarm_data = self.redis_client.get(key)
                if alarm_data:
                    try:
                        alarm = json.loads(str(alarm_data))
                        if alarm.get('active', False):
                            alarm_name = key.split(':')[-1]
                            alarms[alarm_name] = alarm
                    except (json.JSONDecodeError, IndexError):
                        pass
            
            return alarms
        except Exception as e:
            logger.warning(f"Error reading alarms: {e}")
            return {}
    
    def clear_alarm(
        self,
        location: str,
        cluster: str,
        alarm_name: str
    ) -> bool:
        """Clear an alarm (set active=False or delete).
        
        Args:
            location: Location name
            cluster: Cluster name
            alarm_name: Alarm identifier
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            alarm_key = f"alarm:{location}:{cluster}:{alarm_name}"
            alarm_data = self.redis_client.get(alarm_key)
            
            if alarm_data:
                alarm = json.loads(str(alarm_data))
                alarm['active'] = False
                self.redis_client.set(alarm_key, json.dumps(alarm))
                logger.info(f"Alarm cleared: {location}/{cluster}/{alarm_name}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Error clearing alarm: {e}")
            return False
    
    # ========== Heartbeat Management ==========
    
    def write_heartbeat(self, service_name: str) -> bool:
        """Write heartbeat for a service.
        
        Args:
            service_name: Service name (e.g., 'automation-service', 'sensor:clusterA')
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            heartbeat_key = f"heartbeat:{service_name}"
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            
            # TTL depends on service type
            if service_name == 'automation-service':
                ttl = 5  # 5 seconds for automation service
            elif service_name.startswith('sensor:'):
                ttl = 10  # 10 seconds for sensor gateways
            else:
                ttl = 5  # Default 5 seconds
            
            self.redis_client.setex(heartbeat_key, ttl, str(timestamp_ms))
            return True
        except Exception as e:
            logger.debug(f"Error writing heartbeat: {e}")
            return False
    
    def check_heartbeat(self, service_name: str, max_age_seconds: int = 5) -> Tuple[bool, Optional[float]]:
        """Check if service heartbeat is fresh.
        
        Args:
            service_name: Service name
            max_age_seconds: Maximum age in seconds to consider service alive
        
        Returns:
            Tuple of (is_alive, age_seconds)
        """
        if not self.redis_enabled or not self.redis_client:
            return False, None
        
        try:
            heartbeat_key = f"heartbeat:{service_name}"
            heartbeat_str = self.redis_client.get(heartbeat_key)
            
            if heartbeat_str is None:
                return False, None
            
            heartbeat_ms = int(str(heartbeat_str))
            now_ms = int(datetime.now().timestamp() * 1000)
            age_seconds = (now_ms - heartbeat_ms) / 1000.0
            
            return age_seconds <= max_age_seconds, age_seconds
        except Exception as e:
            logger.debug(f"Error checking heartbeat: {e}")
            return False, None
    
    # ========== Last Good Value Management ==========
    
    def write_last_good_value(
        self,
        cluster: str,
        sensor_name: str,
        value: float,
        timestamp: Optional[int] = None
    ) -> bool:
        """Write last good sensor value to Redis.
        
        Args:
            cluster: Cluster name
            sensor_name: Sensor name (e.g., 'dry_bulb_f', 'co2_b')
            value: Sensor value
            timestamp: Timestamp in milliseconds (default: current time)
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            last_good_key = f"sensor:{cluster}:{sensor_name}:last_good"
            timestamp_ms = timestamp or int(datetime.now().timestamp() * 1000)
            
            last_good_data = {
                'value': value,
                'timestamp': timestamp_ms
            }
            
            # TTL: hold_period + 10 seconds (default hold_period is 30s, so TTL = 40s)
            # This will be configurable later
            ttl = 40  # Default hold period (30s) + buffer (10s)
            
            self.redis_client.setex(last_good_key, ttl, json.dumps(last_good_data))
            return True
        except Exception as e:
            logger.debug(f"Error writing last good value: {e}")
            return False
    
    def read_last_good_value(self, cluster: str, sensor_name: str) -> Optional[Dict[str, Any]]:
        """Read last good sensor value from Redis.
        
        Args:
            cluster: Cluster name
            sensor_name: Sensor name
        
        Returns:
            Dict with 'value' and 'timestamp', or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            last_good_key = f"sensor:{cluster}:{sensor_name}:last_good"
            last_good_data = self.redis_client.get(last_good_key)
            
            if last_good_data:
                return json.loads(str(last_good_data))
        except Exception as e:
            logger.debug(f"Error reading last good value: {e}")
        return None
    
    def check_last_good_age(
        self,
        cluster: str,
        sensor_name: str,
        max_age_seconds: int = 30
    ) -> Tuple[bool, Optional[float]]:
        """Check if last good value is still valid (within age limit).
        
        Args:
            cluster: Cluster name
            sensor_name: Sensor name
            max_age_seconds: Maximum age in seconds to consider value valid
        
        Returns:
            Tuple of (is_valid, age_seconds)
        """
        if not self.redis_enabled or not self.redis_client:
            return False, None
        
        try:
            last_good = self.read_last_good_value(cluster, sensor_name)
            if last_good is None:
                return False, None
            
            timestamp_ms = last_good.get('timestamp', 0)
            now_ms = int(datetime.now().timestamp() * 1000)
            age_seconds = (now_ms - timestamp_ms) / 1000.0
            
            return age_seconds <= max_age_seconds, age_seconds
        except Exception as e:
            logger.debug(f"Error checking last good age: {e}")
            return False, None
    
    # ========== PID Parameter Cache ==========
    
    def read_pid_parameters(self, device_type: str) -> Optional[Dict[str, Any]]:
        """Read PID parameters from Redis cache.
        
        Args:
            device_type: Device type (e.g., 'heater', 'co2')
        
        Returns:
            Dict with kp, ki, kd, source, updated_at, or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            pid_key = f"pid:parameters:{device_type}"
            pid_data = self.redis_client.get(pid_key)
            
            if pid_data:
                return json.loads(str(pid_data))
        except Exception as e:
            logger.debug(f"Error reading PID parameters from Redis: {e}")
        return None
    
    def write_pid_parameters(
        self,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        source: str = 'api',
        updated_at: Optional[int] = None
    ) -> bool:
        """Write PID parameters to Redis cache.
        
        Args:
            device_type: Device type
            kp: Proportional gain
            ki: Integral gain
            kd: Derivative gain
            source: Source of parameters ('api', 'config')
            updated_at: Timestamp in milliseconds (default: current time)
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            pid_key = f"pid:parameters:{device_type}"
            timestamp_ms = updated_at or int(datetime.now().timestamp() * 1000)
            pid_ttl = 300  # 5 minutes for PID parameters
            
            pid_data = {
                'kp': kp,
                'ki': ki,
                'kd': kd,
                'source': source,
                'updated_at': timestamp_ms
            }
            
            self.redis_client.setex(pid_key, pid_ttl, json.dumps(pid_data))
            return True
        except Exception as e:
            logger.warning(f"Error writing PID parameters to Redis: {e}")
            return False

    # ========== Light Intensity Management ==========
    
    def write_light_intensity(
        self,
        location: str,
        cluster: str,
        device_name: str,
        intensity: float,
        voltage: float,
        board_id: int,
        channel: int
    ) -> bool:
        """Write light intensity to Redis (persistent, no TTL).
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            intensity: Light intensity (0-100%)
            voltage: Output voltage (0-10V)
            board_id: DFR0971 board ID
            channel: DFR0971 channel (0 or 1)
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            light_key = f"light:{location}:{cluster}:{device_name}"
            
            light_data = {
                'intensity': intensity,
                'voltage': voltage,
                'board_id': board_id,
                'channel': channel,
                'timestamp_ms': timestamp_ms
            }
            
            # Store without TTL (persistent) - these values should survive service restarts
            self.redis_client.set(light_key, json.dumps(light_data))
            return True
        except Exception as e:
            logger.warning(f"Error writing light intensity to Redis: {e}")
            return False
    
    def read_light_intensity(
        self,
        location: str,
        cluster: str,
        device_name: str
    ) -> Optional[Dict[str, Any]]:
        """Read light intensity from Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
        
        Returns:
            Dict with intensity, voltage, board_id, channel, timestamp_ms, or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            light_key = f"light:{location}:{cluster}:{device_name}"
            light_data = self.redis_client.get(light_key)
            if light_data:
                return json.loads(str(light_data))
        except Exception as e:
            logger.debug(f"Error reading light intensity from Redis: {e}")
        return None

    def write_ramp_state(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        current_effective_setpoint: float,
        ramp_start_timestamp: datetime,
        ramp_duration: int,
        target_setpoint: float
    ) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False
        try:
            ramp_key = f"ramp:{location}:{cluster}:{setpoint_type}"
            ramp_ttl = 10
            ramp_data = {
                'current_effective_setpoint': current_effective_setpoint,
                'ramp_start_timestamp': ramp_start_timestamp.isoformat(),
                'ramp_duration': ramp_duration,
                'target_setpoint': target_setpoint
            }
            self.redis_client.setex(ramp_key, ramp_ttl, json.dumps(ramp_data))
            logger.debug(
                f"Wrote ramp state for {setpoint_type} ({location}/{cluster}): "
                f"current={current_effective_setpoint:.2f}, target={target_setpoint:.2f}, "
                f"duration={ramp_duration}min"
            )
            return True
        except Exception as e:
            logger.warning(f"Error writing ramp state to Redis: {e}")
            return False

    def read_ramp_state(
        self,
        location: str,
        cluster: str,
        setpoint_type: str
    ):
        if not self.redis_enabled or not self.redis_client:
            return None
        try:
            ramp_key = f"ramp:{location}:{cluster}:{setpoint_type}"
            ramp_data = self.redis_client.get(ramp_key)
            if ramp_data:
                return json.loads(str(ramp_data))
        except Exception as e:
            logger.debug(f"Error reading ramp state from Redis: {e}")
        return None

    def clear_ramp_state(
        self,
        location: str,
        cluster: str,
        setpoint_type: str
    ) -> bool:
        if not self.redis_enabled or not self.redis_client:
            return False
        try:
            ramp_key = f"ramp:{location}:{cluster}:{setpoint_type}"
            self.redis_client.delete(ramp_key)
            logger.debug(f"Cleared ramp state for {setpoint_type} ({location}/{cluster})")
            return True
        except Exception as e:
            logger.warning(f"Error clearing ramp state from Redis: {e}")
            return False
    
    # ========== Schedule State Management ==========
    
    def write_schedule_state(
        self,
        location: str,
        cluster: str,
        schedule_data: Dict[str, Any]
    ) -> bool:
        """Write complete schedule state to Redis following canonical schema.
        
        Schedule state includes:
        - Room schedule (day_start_time, day_end_time, ramp durations)
        - Climate schedule (pre_day_duration, pre_night_duration)
        - Setpoints for all modes (DAY, NIGHT, PRE_DAY, PRE_NIGHT)
        - Light intensities (target_intensity per light)
        
        Args:
            location: Location name (e.g., 'Veg Room', 'Flower Room')
            cluster: Cluster name (e.g., 'main')
            schedule_data: Complete schedule data matching canonical schema:
                {
                    "room": {
                        "day_start_time": "06:00",
                        "day_end_time": "20:00",
                        "night_start_time": "20:00",
                        "night_end_time": "06:00",
                        "ramp_up_duration": 30,
                        "ramp_down_duration": 15
                    },
                    "climate": {
                        "pre_day_duration": 15,
                        "pre_night_duration": 10
                    },
                    "setpoints": {
                        "DAY": { "heating_setpoint": 22.0, ... },
                        "NIGHT": { ... },
                        "PRE_DAY": { ... },
                        "PRE_NIGHT": { ... }
                    },
                    "lights": {
                        "light_1": { "target_intensity": 70.0 },
                        "light_2": { "target_intensity": 80.0 }
                    }
                }
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            # Follow canonical schema: schedule:state:<room>:<cluster>
            state_key = f"schedule:state:{location}:{cluster}"
            
            # No TTL - schedule state persists until explicitly updated
            # Schedule configuration changes infrequently, current mode changes daily
            # but is stored separately in system:mode
            self.redis_client.set(state_key, json.dumps(schedule_data))
            
            logger.debug(f"Wrote schedule state to Redis: {state_key}")
            return True
        except Exception as e:
            logger.warning(f"Error writing schedule state to Redis: {e}")
            return False
    
    def read_schedule_state(
        self,
        location: str,
        cluster: str
    ) -> Optional[Dict[str, Any]]:
        """Read schedule state from Redis following canonical schema.
        
        Args:
            location: Location name (e.g., 'Veg Room', 'Flower Room')
            cluster: Cluster name (e.g., 'main')
        
        Returns:
            Complete schedule data matching canonical schema, or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            # Follow canonical schema: schedule:state:<room>:<cluster>
            state_key = f"schedule:state:{location}:{cluster}"
            state_data = self.redis_client.get(state_key)
            
            if state_data:
                return json.loads(str(state_data))
        except Exception as e:
            logger.debug(f"Error reading schedule state from Redis: {e}")
        return None

