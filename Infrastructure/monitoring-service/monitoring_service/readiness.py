"""Typed readiness composition for monitoring read dependencies."""

from __future__ import annotations

from typing import ClassVar, Literal, Protocol, final

from pydantic import BaseModel, ConfigDict

from shared.health import check_postgres_pool, check_redis_async_client

from monitoring_service.config import SERVICE_NAME
from monitoring_service.database import ReadOnlyDatabase
from monitoring_service.redis_resources import RedisReadClient


class DependencyCheck(BaseModel):
    """One dependency result returned by the shared readiness primitives."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    latency_ms: float | None = None
    detail: str | None = None


class ReadinessChecks(BaseModel):
    """Independently observable monitoring read dependencies."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    database: DependencyCheck
    redis: DependencyCheck


class ReadinessResponse(BaseModel):
    """The port-8005 readiness response contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    service: Literal["monitoring-service"] = SERVICE_NAME
    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks


class ReadinessResources(Protocol):
    """The read-only connections needed for monitoring readiness."""

    database: ReadOnlyDatabase | None
    redis_client: RedisReadClient | None


class ReadinessProbe(Protocol):
    """Provide the current readiness result without changing dependency state."""

    async def check(self) -> ReadinessResponse:
        """Return the current database and Redis readiness state."""
        ...


@final
class SharedReadinessProbe:
    """Compose the existing non-mutating shared Postgres and Redis probes."""

    def __init__(self, resources: ReadinessResources) -> None:
        self._resources: ReadinessResources = resources

    async def check(self) -> ReadinessResponse:
        """Probe both monitoring read dependencies independently."""
        checks = ReadinessChecks(
            database=DependencyCheck.model_validate(
                await check_postgres_pool(
                    None if self._resources.database is None else self._resources.database.pool
                )
            ),
            redis=DependencyCheck.model_validate(
                await check_redis_async_client(
                    None
                    if self._resources.redis_client is None
                    else self._resources.redis_client._readiness_client()
                )
            ),
        )
        status: Literal["ready", "not_ready"] = (
            "ready" if checks.database.ok and checks.redis.ok else "not_ready"
        )
        return ReadinessResponse(status=status, checks=checks)


@final
class StaticReadinessProbe:
    """Stable readiness probe for focused endpoint tests."""

    def __init__(self, database: DependencyCheck, redis: DependencyCheck) -> None:
        self._checks: ReadinessChecks = ReadinessChecks(database=database, redis=redis)

    async def check(self) -> ReadinessResponse:
        """Return the fixed dependency state."""
        dependencies_are_ready = self._checks.database.ok and self._checks.redis.ok
        status: Literal["ready", "not_ready"] = "ready" if dependencies_are_ready else "not_ready"
        return ReadinessResponse(status=status, checks=self._checks)
