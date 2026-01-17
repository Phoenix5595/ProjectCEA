"""Mode management mixin for Redis client."""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from shared.logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class ModesMixin:
    """Mixin providing mode management functionality."""
    
    redis_enabled: bool
    redis_client: Optional["redis.Redis"]
    
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
