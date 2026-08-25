"""Dedicated Redis read resource for monitoring chart metadata."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, final

import redis.asyncio
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from monitoring_service.resources import RedisResourceSettings
from monitoring_service.sensor_models import MonitoringUnavailableError


@dataclass(frozen=True, slots=True)
class LightEffectiveMetadata:
    """Published effective intensity for one charted light device."""

    effective_intensity: float


class RedisReadTransport(Protocol):
    """The read-only subset required from the Redis async client."""

    def mget(self, keys: list[str]) -> Awaitable[list[str | None]]: ...

    def ping(self) -> Awaitable[bool] | bool: ...

    def aclose(self) -> Awaitable[None]: ...

    def scan_iter(self, *, match: str, count: int) -> AsyncIterator[str]: ...


@final
class RedisReadClient:
    """Read chart metadata without exposing Redis mutation operations."""

    def __init__(self, client: redis.asyncio.Redis | RedisReadTransport) -> None:
        self._client: RedisReadTransport = client

    @classmethod
    def connect(cls, settings: RedisResourceSettings) -> RedisReadClient:
        """Create a Redis client bounded for connection and read operations."""
        client = redis.asyncio.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.timeout_seconds,
            socket_timeout=settings.timeout_seconds,
        )
        return cls(client)

    async def read_light_effective_metadata(
        self, location: str, cluster: str, device_names: Sequence[str]
    ) -> Mapping[str, LightEffectiveMetadata]:
        """Read published effective light intensities for the requested devices."""
        keys = [
            f"cea:effective_setpoint:{location}:{cluster}:light:{device_name}:effective_intensity"
            for device_name in device_names
        ]
        values = await self.mget(keys)
        return {
            device_name: LightEffectiveMetadata(effective_intensity=float(value))
            for device_name, value in zip(device_names, values, strict=True)
            if value is not None
        }

    async def mget(self, keys: list[str]) -> list[str | None]:
        """Read complete shared publication payloads without write capability."""
        try:
            return await self._client.mget(keys)
        except (RedisConnectionError, RedisTimeoutError) as exc:
            raise MonitoringUnavailableError("monitoring Redis read is unavailable") from exc

    async def ping(self) -> bool:
        """Support the shared non-mutating readiness probe."""
        result = self._client.ping()
        if isinstance(result, bool):
            return result
        return await result

    async def sensor_values(self, pattern: str) -> tuple[tuple[str, str | None, str | None], ...]:
        """Read current sensor values and publication timestamps through SCAN and MGET only."""
        try:
            keys: list[str] = []
            async for key in self._client.scan_iter(match=pattern, count=500):
                if not key.endswith("_ts"):
                    keys.append(key)
        except (RedisConnectionError, RedisTimeoutError) as exc:
            raise MonitoringUnavailableError("monitoring Redis read is unavailable") from exc
        if not keys:
            return ()
        values = await self.mget(keys)
        timestamps = await self.mget([f"{key}_ts" for key in keys])
        return tuple(zip(keys, values, timestamps, strict=True))

    def _readiness_client(self) -> redis.asyncio.Redis:
        """Return the underlying client only for the shared non-mutating readiness probe."""
        if not isinstance(self._client, redis.asyncio.Redis):
            raise RuntimeError("monitoring Redis test double cannot run readiness")
        return self._client

    async def close(self) -> None:
        """Release the independently owned Redis connection pool."""
        await self._client.aclose()
