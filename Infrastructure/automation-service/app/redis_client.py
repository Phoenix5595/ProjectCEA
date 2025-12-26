"""Redis client for automation service - writes to stream and state keys."""
import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import redis

logger = logging.getLogger(__name__)


class AutomationRedisClient:
    """Redis client for automation service.
    
    Writes automation state to:
    - Redis Stream (sensor:raw) - recent history buffer
    - Redis state keys (automation:*) - live values for frontend
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
        self.redis_enabled = False
    
    def connect(self) -> bool:
        """Connect to Redis.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Connect for state keys (decode_responses=True)
            self.redis_client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            self.redis_client.ping()
            
            # Connect for stream writes (decode_responses=False for binary)
            self.stream_client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=False,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            self.stream_client.ping()
            
            self.redis_enabled = True
            logger.info(f"Connected to Redis: {self.redis_url}")
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Will continue without Redis.")
            self.redis_enabled = False
            return False
    
    def close(self) -> None:
        """Close Redis connection."""
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
            self.stream_client.xadd('sensor:raw', stream_data, maxlen=100000, approximate=True)
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
            Dict with temperature, humidity, co2, source, timestamp_ms, or None if not found
        """
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            # Try reading individual keys first
            temp_key = f"setpoint:{location}:{cluster}:temperature"
            hum_key = f"setpoint:{location}:{cluster}:humidity"
            co2_key = f"setpoint:{location}:{cluster}:co2"
            source_key = f"setpoint:{location}:{cluster}:source"
            
            temp = self.redis_client.get(temp_key)
            hum = self.redis_client.get(hum_key)
            co2 = self.redis_client.get(co2_key)
            source_data = self.redis_client.get(source_key)
            
            if temp is None and hum is None and co2 is None:
                return None
            
            result = {}
            if temp is not None:
                result['temperature'] = float(temp)
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
        temperature: Optional[float] = None,
        humidity: Optional[float] = None,
        co2: Optional[float] = None,
        source: str = 'api'
    ) -> bool:
        """Write setpoints to Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            temperature: Temperature setpoint (optional)
            humidity: Humidity setpoint (optional)
            co2: CO2 setpoint (optional)
            source: Source of setpoint ('api', 'node-red', 'schedule', 'failsafe')
        
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            setpoint_ttl = 60  # 60 seconds for setpoints
            
            pipe = self.redis_client.pipeline()
            
            if temperature is not None:
                pipe.setex(f"setpoint:{location}:{cluster}:temperature", setpoint_ttl, str(temperature))
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
                return json.loads(source_data)
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
            
            last_write_ms = int(last_write_str)
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
            return mode if mode else None
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
            source: Source of mode change ('api', 'node-red', 'system')
        
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
                return json.loads(failsafe_data)
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
                existing = json.loads(existing_data)
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
                alarm = json.loads(alarm_data)
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
                        alarm = json.loads(alarm_data)
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
                alarm = json.loads(alarm_data)
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
    
    def check_heartbeat(self, service_name: str, max_age_seconds: int = 5) -> Tuple[bool, Optional[int]]:
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
            
            heartbeat_ms = int(heartbeat_str)
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
                return json.loads(last_good_data)
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
                return json.loads(pid_data)
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
            source: Source of parameters ('api', 'node-red', 'config')
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
                return json.loads(light_data)
        except Exception as e:
            logger.debug(f"Error reading light intensity from Redis: {e}")
        return None

