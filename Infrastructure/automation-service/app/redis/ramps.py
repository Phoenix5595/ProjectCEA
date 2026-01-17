"""Ramp state management mixin for Redis client."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

from shared.logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class RampsMixin:
    """Mixin providing ramp state management functionality."""
    
    redis_enabled: bool
    redis_client: Optional["redis.Redis"]
    
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
        """Write ramp state to Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Type of setpoint being ramped
            current_effective_setpoint: Current effective setpoint value
            ramp_start_timestamp: When the ramp started
            ramp_duration: Ramp duration in minutes
            target_setpoint: Target setpoint value
        
        Returns:
            True if successful, False otherwise
        """
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
    ) -> Optional[Dict[str, Any]]:
        """Read ramp state from Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Type of setpoint
        
        Returns:
            Dict with ramp state, or None if not found
        """
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
        """Clear ramp state from Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Type of setpoint
        
        Returns:
            True if successful, False otherwise
        """
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
