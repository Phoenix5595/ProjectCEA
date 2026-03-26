"""Mode management mixin for Redis client."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.redis.schema import get_with_backward_compat, mode_key, set_with_backward_compat
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    import redis

logger = get_logger(__name__)


class ModesMixin:
    """Mixin providing mode management functionality."""

    redis_enabled: bool
    redis_client: redis.Redis | None

    def read_mode(self, location: str, cluster: str) -> str | None:
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
            mode = get_with_backward_compat(
                self.redis_client,
                "mode:{location}:{cluster}",
                mode_key,
                location=location,
                cluster=cluster,
            )
            return str(mode) if mode else None
        except Exception as e:
            logger.warning(f"Error reading mode from Redis: {e}")
            return None

    def write_mode(self, location: str, cluster: str, mode: str, source: str = "api") -> bool:
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
            mode_ttl = 300  # 5 minutes for mode

            set_with_backward_compat(
                self.redis_client,
                "mode:{location}:{cluster}",
                mode_key,
                mode,
                mode_ttl,
                location=location,
                cluster=cluster,
            )
            logger.info(f"Mode set to {mode} for {location}/{cluster} (source: {source})")
            return True
        except Exception as e:
            logger.warning(f"Error writing mode to Redis: {e}")
            return False
