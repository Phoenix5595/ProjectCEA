"""Atomic Redis adapter for automation-owned monitoring publications."""

from __future__ import annotations

import json
from typing import Protocol

import redis

from app.schemas.climate_timeline import RichTrajectoryEnvelope
from shared.monitoring_contracts import CurrentSnapshot, FutureProjection
from shared.redis_keys import (
    monitoring_current_publication_key,
    monitoring_future_publication_key,
    monitoring_rich_trajectory_key,
)


class RedisSetter(Protocol):
    """Narrow synchronous Redis capability used by the background publisher."""

    def set(self, key: str, value: str) -> bool:
        """Atomically replace one Redis string value."""
        ...

    def get(self, key: str) -> str | bytes | None: ...

    def pipeline(self, transaction: bool = True) -> RedisPipeline: ...


class RedisPipeline(Protocol):
    """Minimal transactional Redis pipeline used for paired publications."""

    def set(self, key: str, value: str) -> RedisPipeline: ...

    def execute(self) -> list[bool]: ...


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

    def write_future(self, location: str, projections: tuple[FutureProjection, ...]) -> bool:
        """Atomically replace one room's complete ordered future timeline."""
        try:
            return self._redis_client.set(
                monitoring_future_publication_key(location),
                json.dumps(
                    [projection.model_dump(mode="json") for projection in projections],
                    separators=(",", ":"),
                ),
            )
        except redis.RedisError:
            return False

    def write_rich_trajectory(self, location: str, trajectory: RichTrajectoryEnvelope) -> bool:
        try:
            return self._redis_client.set(
                monitoring_rich_trajectory_key(location), trajectory.model_dump_json()
            )
        except redis.RedisError:
            return False

    def write_complete(
        self,
        location: str,
        projections: tuple[FutureProjection, ...],
        trajectory: RichTrajectoryEnvelope,
    ) -> bool:
        """Replace legacy and rich payloads in one Redis transaction."""
        try:
            pipeline = self._redis_client.pipeline(transaction=True)
            pipeline.set(
                monitoring_future_publication_key(location),
                json.dumps(
                    [projection.model_dump(mode="json") for projection in projections],
                    separators=(",", ":"),
                ),
            )
            pipeline.set(monitoring_rich_trajectory_key(location), trajectory.model_dump_json())
            return all(pipeline.execute())
        except redis.RedisError:
            return False

    def read_rich_trajectory(self, location: str) -> RichTrajectoryEnvelope | None:
        """Read the worker-published rich trajectory without recomputation."""
        try:
            raw = self._redis_client.get(monitoring_rich_trajectory_key(location))
        except redis.RedisError:
            return None
        if raw is None:
            return None
        if not isinstance(raw, (str, bytes, bytearray)):
            return None
        return RichTrajectoryEnvelope.model_validate_json(raw)


class RedisRichTrajectoryReader:
    """Read worker-published rich trajectories from a concrete Redis client."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis_client = redis_client

    def read_rich_trajectory(self, location: str) -> RichTrajectoryEnvelope | None:
        try:
            raw = self._redis_client.get(monitoring_rich_trajectory_key(location))
        except redis.RedisError:
            return None
        if raw is None:
            return None
        if not isinstance(raw, (str, bytes, bytearray)):
            return None
        return RichTrajectoryEnvelope.model_validate_json(raw)
