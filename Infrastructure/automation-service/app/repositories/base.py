from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class BaseRepository:
    """Base class for all repositories with shared connection and caching logic."""

    def __init__(self, pool: Pool | None = None) -> None:
        self._pool = pool
        self._query_cache: dict[str, tuple[Any, float]] = {}
        self._cache_ttl = 30.0

    def set_pool(self, pool: Pool) -> None:
        self._pool = pool

    @property
    def pool(self) -> Pool:
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        return self._pool

    def _get_cache_key(self, operation: str, *args: Any) -> str:
        return f"{operation}:{':'.join(str(arg) for arg in args)}"

    def _get_cached_result(self, cache_key: str) -> Any | None:
        if cache_key in self._query_cache:
            result, expiry_time = self._query_cache[cache_key]
            if time.time() < expiry_time:
                return result
            del self._query_cache[cache_key]
        return None

    def _set_cached_result(self, cache_key: str, result: Any) -> None:
        expiry_time = time.time() + self._cache_ttl
        self._query_cache[cache_key] = (result, expiry_time)

    def clear_cache(self) -> None:
        self._query_cache.clear()
        logger.debug("Repository cache cleared")
