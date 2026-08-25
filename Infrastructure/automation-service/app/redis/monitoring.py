"""Atomic Redis adapter for automation-owned monitoring publications."""

from __future__ import annotations

from typing import Protocol

import redis

from shared.monitoring_contracts import CurrentSnapshot
from shared.redis_keys import monitoring_current_publication_key


class RedisSetter(Protocol):
    """Narrow synchronous Redis capability used by the background publisher."""

    def set(self, key: str, value: str) -> bool:
        """Atomically replace one Redis string value."""
        ...


class RedisCurrentPublicationWriter:
    """Serialize validated snapshots into one atomic Redis SET per location."""

    def __init__(self, redis_client: RedisSetter) -> None:
        self._redis_client: RedisSetter = redis_client

    def write_current(self, location: str, snapshot: CurrentSnapshot) -> bool:
        """Store the contract JSON without rewriting its event timestamps."""
        try:
            return self._redis_client.set(
                monitoring_current_publication_key(location),
                snapshot.model_dump_json(),
            )
        except redis.RedisError:
            return False
