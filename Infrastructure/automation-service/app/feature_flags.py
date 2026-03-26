"""Feature flag system for runtime toggling of optimizations."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from pydantic import BaseModel
from redis import Redis

logger = logging.getLogger(__name__)


class FeatureFlag(BaseModel):
    """Feature flag model with name, enabled state, and description."""

    name: str
    enabled: bool
    description: str


class FeatureFlagManager:
    """Manages feature flags with Redis storage and local caching."""

    # Define all available flags with their descriptions
    FLAG_DEFINITIONS: dict[str, str] = {
        "FAST_DFR0971_RETRIES": "Enable fast retry logic for DFR0971 dimming boards",
        "REDIS_BATCH_SENSORS": "Enable batch processing of sensor data in Redis",
    }

    def __init__(self, redis_client: Redis, cache_ttl_seconds: int = 5) -> None:
        """Initialize feature flag manager.

        Args:
            redis_client: Redis client for flag storage
            cache_ttl_seconds: TTL for local cache in seconds (default: 5)
        """
        self.redis_client = redis_client
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[bool, float]] = {}  # {flag_name: (value, timestamp)}

    def get_flag(self, flag_name: str) -> bool:
        """Get flag value with local caching.

        Args:
            flag_name: Name of the flag

        Returns:
            Flag value (True/False), defaults to False if not found
        """
        # Check if flag is defined
        if flag_name not in self.FLAG_DEFINITIONS:
            logger.warning(f"Unknown feature flag requested: {flag_name}")
            return False

        # Check cache first
        if flag_name in self._cache:
            value, timestamp = self._cache[flag_name]
            if time.time() - timestamp < self.cache_ttl_seconds:
                return value

        # Fetch from Redis
        try:
            # Support both raw Redis client and AutomationRedisClient wrapper
            client = getattr(self.redis_client, "redis_client", self.redis_client)
            if client is None:
                return False
            redis_value = client.get(f"flag:{flag_name}")
            if redis_value is not None:
                value = str(redis_value).lower() == "true"
            else:
                # Default to False for new flags
                value = False
                # Set default in Redis for consistency
                client.setex(f"flag:{flag_name}", 300, "false")  # 5 min TTL

            # Update cache
            self._cache[flag_name] = (value, time.time())
            return value

        except Exception as e:
            logger.error(f"Failed to get feature flag {flag_name} from Redis: {e}")
            # Return cached value if available, otherwise False
            if flag_name in self._cache:
                return self._cache[flag_name][0]
            return False

    def set_flag(self, flag_name: str, enabled: bool) -> None:
        """Set flag value in Redis and invalidate cache.

        Args:
            flag_name: Name of the flag
            enabled: Flag value to set
        """
        # Check if flag is defined
        if flag_name not in self.FLAG_DEFINITIONS:
            logger.warning(f"Attempted to set unknown feature flag: {flag_name}")
            return

        # Get previous value for logging
        previous_value = self.get_flag(flag_name)

        try:
            # Set in Redis with 5 minute TTL
            # Support both raw Redis client and AutomationRedisClient wrapper
            client = getattr(self.redis_client, "redis_client", self.redis_client)
            redis_value = "true" if enabled else "false"
            client.setex(f"flag:{flag_name}", 300, redis_value)

            # Invalidate cache
            if flag_name in self._cache:
                del self._cache[flag_name]

            # Log the change
            logger.info(f"Feature flag updated: {flag_name} = {enabled} (was: {previous_value})")

        except Exception as e:
            logger.error(f"Failed to set feature flag {flag_name} in Redis: {e}")

    def get_all_flags(self) -> list[FeatureFlag]:
        """Get all defined flags with their current values.

        Returns:
            List of FeatureFlag objects
        """
        flags = []
        for flag_name in self.FLAG_DEFINITIONS:
            enabled = self.get_flag(flag_name)
            flag = FeatureFlag(
                name=flag_name, enabled=enabled, description=self.FLAG_DEFINITIONS[flag_name]
            )
            flags.append(flag)
        return flags

    def get_flag_definition(self, flag_name: str) -> FeatureFlag | None:
        """Get flag definition with current value.

        Args:
            flag_name: Name of the flag

        Returns:
            FeatureFlag object or None if not found
        """
        if flag_name not in self.FLAG_DEFINITIONS:
            return None

        enabled = self.get_flag(flag_name)
        return FeatureFlag(
            name=flag_name, enabled=enabled, description=self.FLAG_DEFINITIONS[flag_name]
        )

    def clear_cache(self) -> None:
        """Clear local cache (useful for testing)."""
        self._cache.clear()

    def _get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics for monitoring.

        Returns:
            Dictionary with cache stats
        """
        current_time = time.time()
        valid_entries = sum(
            1
            for _, timestamp in self._cache.values()
            if current_time - timestamp < self.cache_ttl_seconds
        )

        return {
            "total_entries": len(self._cache),
            "valid_entries": valid_entries,
            "cache_ttl_seconds": self.cache_ttl_seconds,
        }


# Module-level convenience function for use without dependency injection
_default_manager: FeatureFlagManager | None = None


def get_flag(flag_name: str, default: bool = False) -> bool:
    global _default_manager
    if _default_manager is None:
        try:
            import redis

            # Create a raw Redis client for feature flags
            redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
            _default_manager = FeatureFlagManager(redis_client)
        except Exception:
            return default
    return _default_manager.get_flag(flag_name)
