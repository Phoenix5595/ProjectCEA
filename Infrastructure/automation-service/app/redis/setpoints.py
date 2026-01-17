"""Setpoint management mixin for Redis client."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, cast, TYPE_CHECKING

from shared.logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class SetpointsMixin:
    """Mixin providing setpoint management functionality."""
    
    redis_enabled: bool
    redis_client: Optional["redis.Redis"]
    stream_client: Optional["redis.Redis"]
    
    def read_setpoint(self, location: str, cluster: str) -> Optional[Dict[str, Any]]:
        if not self.redis_enabled or not self.redis_client:
            return None
        
        try:
            heat_key = f"setpoint:{location}:{cluster}:heating_setpoint"
            cool_key = f"setpoint:{location}:{cluster}:cooling_setpoint"
            temp_key = f"setpoint:{location}:{cluster}:temperature"
            hum_key = f"setpoint:{location}:{cluster}:humidity"
            co2_key = f"setpoint:{location}:{cluster}:co2"
            source_key = f"setpoint:{location}:{cluster}:source"
            
            keys = [heat_key, cool_key, temp_key, hum_key, co2_key, source_key]
            values = self.redis_client.mget(keys)
            heat, cool, temp, hum, co2, source_data = cast(List[Any], values)  # type: ignore
            
            if heat is None and cool is None and temp is None and hum is None and co2 is None:
                return None
            
            result: Dict[str, Any] = {}
            if heat is not None:
                result['heating_setpoint'] = float(heat)
            elif temp is not None:
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
        if not self.redis_enabled or not self.redis_client:
            return False
        
        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            setpoint_ttl = 60
            
            pipe = self.redis_client.pipeline()
            
            if heating_setpoint is not None:
                pipe.setex(f"setpoint:{location}:{cluster}:heating_setpoint", setpoint_ttl, str(heating_setpoint))
            if cooling_setpoint is not None:
                pipe.setex(f"setpoint:{location}:{cluster}:cooling_setpoint", setpoint_ttl, str(cooling_setpoint))
            if humidity is not None:
                pipe.setex(f"setpoint:{location}:{cluster}:humidity", setpoint_ttl, str(humidity))
            if co2 is not None:
                pipe.setex(f"setpoint:{location}:{cluster}:co2", setpoint_ttl, str(co2))
            
            source_data = {'source': source, 'timestamp': timestamp_ms}
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
        if not self.redis_enabled or not self.redis_client:
            return False

        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            setpoint_ttl = 300

            pipe = self.redis_client.pipeline()
            prefix = f"effective_setpoint:{location}:{cluster}"

            if effective_heating_setpoint is not None:
                pipe.setex(f"{prefix}:heating_setpoint", setpoint_ttl, str(effective_heating_setpoint))
            if effective_cooling_setpoint is not None:
                pipe.setex(f"{prefix}:cooling_setpoint", setpoint_ttl, str(effective_cooling_setpoint))
            if effective_humidity_setpoint is not None:
                pipe.setex(f"{prefix}:humidity", setpoint_ttl, str(effective_humidity_setpoint))
            if effective_co2_setpoint is not None:
                pipe.setex(f"{prefix}:co2", setpoint_ttl, str(effective_co2_setpoint))
            if effective_vpd_setpoint is not None:
                pipe.setex(f"{prefix}:vpd", setpoint_ttl, str(effective_vpd_setpoint))

            if nominal_heating_setpoint is not None:
                pipe.setex(f"{prefix}:nominal_heating_setpoint", setpoint_ttl, str(nominal_heating_setpoint))
            if nominal_cooling_setpoint is not None:
                pipe.setex(f"{prefix}:nominal_cooling_setpoint", setpoint_ttl, str(nominal_cooling_setpoint))
            if nominal_humidity_setpoint is not None:
                pipe.setex(f"{prefix}:nominal_humidity_setpoint", setpoint_ttl, str(nominal_humidity_setpoint))
            if nominal_co2_setpoint is not None:
                pipe.setex(f"{prefix}:nominal_co2_setpoint", setpoint_ttl, str(nominal_co2_setpoint))
            if nominal_vpd_setpoint is not None:
                pipe.setex(f"{prefix}:nominal_vpd_setpoint", setpoint_ttl, str(nominal_vpd_setpoint))

            if ramp_progress_heating is not None:
                pipe.setex(f"{prefix}:ramp_progress_heating", setpoint_ttl, str(ramp_progress_heating))
            if ramp_progress_cooling is not None:
                pipe.setex(f"{prefix}:ramp_progress_cooling", setpoint_ttl, str(ramp_progress_cooling))
            if ramp_progress_humidity is not None:
                pipe.setex(f"{prefix}:ramp_progress_humidity", setpoint_ttl, str(ramp_progress_humidity))
            if ramp_progress_co2 is not None:
                pipe.setex(f"{prefix}:ramp_progress_co2", setpoint_ttl, str(ramp_progress_co2))
            if ramp_progress_vpd is not None:
                pipe.setex(f"{prefix}:ramp_progress_vpd", setpoint_ttl, str(ramp_progress_vpd))

            if device_name is not None:
                pipe.setex(f"{prefix}:device_name", setpoint_ttl, device_name)

            if effective_light_intensity is not None:
                pipe.setex(f"{prefix}:effective_light_intensity", setpoint_ttl, str(effective_light_intensity))
            if nominal_light_intensity is not None:
                pipe.setex(f"{prefix}:nominal_light_intensity", setpoint_ttl, str(nominal_light_intensity))
            if ramp_progress_light is not None:
                pipe.setex(f"{prefix}:ramp_progress_light", setpoint_ttl, str(ramp_progress_light))

            pipe.execute()
            
            self._write_effective_setpoints_to_stream(
                location, cluster, timestamp_ms, mode,
                effective_heating_setpoint, nominal_heating_setpoint, ramp_progress_heating,
                effective_cooling_setpoint, nominal_cooling_setpoint, ramp_progress_cooling,
                effective_humidity_setpoint, nominal_humidity_setpoint, ramp_progress_humidity,
                effective_co2_setpoint, nominal_co2_setpoint, ramp_progress_co2,
                effective_vpd_setpoint, nominal_vpd_setpoint, ramp_progress_vpd,
                effective_light_intensity, nominal_light_intensity, ramp_progress_light
            )
            
            return True
        except Exception as e:
            logger.warning(f"Error writing setpoint to Redis: {e}")
            return False
    
    def _write_effective_setpoints_to_stream(
        self,
        location: str,
        cluster: str,
        timestamp_ms: int,
        mode: Optional[str],
        eff_heat: Optional[float], nom_heat: Optional[float], ramp_heat: Optional[float],
        eff_cool: Optional[float], nom_cool: Optional[float], ramp_cool: Optional[float],
        eff_hum: Optional[float], nom_hum: Optional[float], ramp_hum: Optional[float],
        eff_co2: Optional[float], nom_co2: Optional[float], ramp_co2: Optional[float],
        eff_vpd: Optional[float], nom_vpd: Optional[float], ramp_vpd: Optional[float],
        eff_light: Optional[float], nom_light: Optional[float], ramp_light: Optional[float]
    ) -> None:
        if not self.stream_client:
            return
        
        try:
            variables = [
                ('temp', eff_heat, nom_heat, ramp_heat),
                ('cooling', eff_cool, nom_cool, ramp_cool),
                ('humidity', eff_hum, nom_hum, ramp_hum),
                ('co2', eff_co2, nom_co2, ramp_co2),
                ('vpd', eff_vpd, nom_vpd, ramp_vpd),
                ('light', eff_light, nom_light, ramp_light),
            ]
            
            for var_name, effective, nominal, ramp in variables:
                if effective is not None:
                    stream_data: Dict[bytes, bytes] = {
                        b'ts': str(timestamp_ms).encode(),
                        b'type': b'control',
                        b'room': location.encode(),
                        b'cluster': cluster.encode(),
                        b'variable': var_name.encode(),
                        b'nominal': str(nominal or effective).encode(),
                        b'effective': str(effective).encode(),
                    }
                    if mode:
                        stream_data[b'mode'] = mode.encode()
                    if ramp is not None:
                        stream_data[b'ramp'] = str(ramp).encode()
                    self.stream_client.xadd('stream:control', stream_data, maxlen=100000, approximate=True)  # type: ignore
        except Exception as e:
            logger.debug(f"Error writing effective setpoints to stream: {e}")
    
    def read_setpoint_source(self, location: str, cluster: str) -> Optional[Dict[str, Any]]:
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
        if not self.redis_enabled or not self.redis_client:
            return True
        
        try:
            rate_limit_key = f"setpoint:{location}:{cluster}:{setpoint_type}:last_write"
            last_write_str = self.redis_client.get(rate_limit_key)
            
            if last_write_str is None:
                self.redis_client.setex(rate_limit_key, 2, str(int(datetime.now().timestamp() * 1000)))
                return True
            
            last_write_ms = int(str(last_write_str))
            now_ms = int(datetime.now().timestamp() * 1000)
            time_since_last = (now_ms - last_write_ms) / 1000.0
            
            if time_since_last >= (1.0 / max_per_second):
                self.redis_client.setex(rate_limit_key, 2, str(now_ms))
                return True
            
            return False
        except Exception as e:
            logger.warning(f"Error checking rate limit: {e}")
            return True
