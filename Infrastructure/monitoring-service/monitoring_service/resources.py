"""Typed settings for monitoring-service read resources."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatabaseResourceSettings:
    """Bounded connection and execution settings for TimescaleDB reads."""

    postgres_dsn: str
    pool_size: int
    acquire_timeout_seconds: float
    statement_timeout_ms: int


@dataclass(frozen=True, slots=True)
class RedisResourceSettings:
    """Connection and socket timeout settings for Redis reads."""

    redis_url: str
    timeout_seconds: float
