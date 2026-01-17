"""PID parameter cache mixin for Redis client."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

from shared.logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class PIDMixin:
    """Mixin providing PID parameter cache functionality."""
    
    redis_enabled: bool
    redis_client: Optional["redis.Redis"]
    
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
