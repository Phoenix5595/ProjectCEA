"""Shared Redis pool + client factories (async and sync).

Lifts the 5-way duplicated Redis connection boilerplate that was scattered
across services. Before this module, every service with Redis state/stream
I/O had its own copy of::

    pool = redis.(asyncio.)ConnectionPool.from_url(url, decode_responses=..., max_connections=N, ...)
    client = redis.(asyncio.)Redis(connection_pool=pool)
    await/sync client.ping()
    ...
    await/sync client.close()
    await/sync pool.disconnect()

Pattern survey (pre-lift):

===========================  =======  =====  ================  =====  ===================
service                       style    pools  decode_responses  max    extra kwargs
===========================  =======  =====  ================  =====  ===================
backend                       async    1      True               10    -
onewire-worker                async    1      True                5    -
soil-sensor-service           async    2      True/False       10/5   retry_on_timeout
can-processor-service         sync     2      False/True      10/10   socket_*, retry
automation-service            sync     2      True/False      20/10   socket_*, retry, hc
===========================  =======  =====  ================  =====  ===================

Scope:
  * **Connection lifecycle only** — create, ping, close. Key-naming, TTL
    policy, and xadd maxlen belong to ``shared.redis_keys`` (next Phase 6
    sub-step). Repository-layer helpers belong to each service.
  * **No silent fallback.** Each service decides what happens when Redis
    is unreachable (warn-and-continue vs. fail-startup). The factory
    raises; the caller catches if it wants the old "log a warning and
    keep going" behaviour.
  * **Both sync and async.** ``can-processor-service`` and
    ``automation-service`` run sync Redis today (mostly because they live
    in synchronous worker loops or threads). Migrating them to async is a
    bigger lift (Phase 6 "sync->async redis migration for
    automation-service" follow-up); this module keeps both flavours so
    the *factory* call site is shared even before those migrations land.

Naming note: the module is called ``redis_client`` (not ``redis``) so
``from shared.redis_client import ...`` cannot be confused with the
third-party ``redis`` package. Same flat-module convention as
``shared/db.py`` and ``shared/db_credentials.py``.
"""

from __future__ import annotations

import os
from typing import Any

import redis as _sync_redis
import redis.asyncio as _async_redis

from shared.infra_logging import get_logger

logger = get_logger(__name__)


REDIS_URL_DEFAULT: str = "redis://localhost:6379"

DEFAULT_MAX_CONNECTIONS: int = 10
DEFAULT_SOCKET_TIMEOUT_SEC: float = 5.0
DEFAULT_SOCKET_CONNECT_TIMEOUT_SEC: float = 5.0
DEFAULT_HEALTH_CHECK_INTERVAL_SEC: int = 30
DEFAULT_RETRY_ON_TIMEOUT: bool = True


def redis_url_from_env(default: str = REDIS_URL_DEFAULT) -> str:
    """Read ``REDIS_URL`` from the environment with a localhost fallback.

    Every CEA service uses this exact lookup. The default intentionally
    points at ``localhost:6379`` so a dev checkout "just works" without
    requiring ``REDIS_URL`` in the environment; prod services get the
    real URL injected via their systemd ``EnvironmentFile=``.
    """
    return os.getenv("REDIS_URL", default)


def _filter_pool_kwargs(
    socket_timeout: float | None,
    socket_connect_timeout: float | None,
    retry_on_timeout: bool,
    health_check_interval: int | None,
) -> dict[str, Any]:
    """Build the optional-kwargs dict for ``ConnectionPool.from_url``.

    ``None`` values are omitted rather than passed through — the redis-py
    defaults for ``socket_timeout`` / ``socket_connect_timeout`` /
    ``health_check_interval`` are "no timeout / disabled", and passing
    ``None`` explicitly is different from not passing the kwarg at all in
    some redis-py versions. Keep the behaviour equivalent to the
    pre-lift call sites.
    """
    kwargs: dict[str, Any] = {"retry_on_timeout": retry_on_timeout}
    if socket_timeout is not None:
        kwargs["socket_timeout"] = socket_timeout
    if socket_connect_timeout is not None:
        kwargs["socket_connect_timeout"] = socket_connect_timeout
    if health_check_interval is not None:
        kwargs["health_check_interval"] = health_check_interval
    return kwargs


# ---------------------------------------------------------------------------
# Async factory (redis.asyncio)
# ---------------------------------------------------------------------------


def create_async_pool(
    url: str | None = None,
    *,
    decode_responses: bool = True,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    socket_timeout: float | None = DEFAULT_SOCKET_TIMEOUT_SEC,
    socket_connect_timeout: float | None = DEFAULT_SOCKET_CONNECT_TIMEOUT_SEC,
    retry_on_timeout: bool = DEFAULT_RETRY_ON_TIMEOUT,
    health_check_interval: int | None = DEFAULT_HEALTH_CHECK_INTERVAL_SEC,
) -> _async_redis.ConnectionPool:
    """Build an async Redis ``ConnectionPool``.

    Args:
        url: Redis URL (e.g. ``redis://localhost:6379``). Falls back to
            ``redis_url_from_env()`` when ``None``.
        decode_responses: ``True`` for text-mode state keys, ``False``
            for binary streams. Keep separate pools per mode — one client
            cannot serve both reliably.
        max_connections: Pool ceiling. Services historically pick 5-20
            based on their concurrency shape.
        socket_timeout, socket_connect_timeout: Per-call and per-connect
            deadlines. Defaults to 5s each (matches automation-service +
            can-processor-service; the simpler backend/onewire pools
            didn't set these before — tightening is safe because a 5s
            Redis call on localhost already indicates a broken system).
        retry_on_timeout: Whether redis-py retries a single in-flight op
            once after a socket timeout. Defaults ``True`` to preserve the
            behaviour of the services that already had it.
        health_check_interval: Periodic PING-on-idle interval. Keeps
            long-lived pool connections from dying to server-side idle
            timeouts. ``None`` disables it (match pre-lift behaviour for
            the three services that didn't set it).

    Returns:
        An un-connected async ``ConnectionPool``. No network I/O happens
        here — pass the pool to ``create_async_client()`` (or directly to
        ``redis.asyncio.Redis(connection_pool=pool)``) and ping after.
    """
    resolved_url = url or redis_url_from_env()
    return _async_redis.ConnectionPool.from_url(
        resolved_url,
        decode_responses=decode_responses,
        max_connections=max_connections,
        **_filter_pool_kwargs(
            socket_timeout,
            socket_connect_timeout,
            retry_on_timeout,
            health_check_interval,
        ),
    )


async def create_async_client(
    url: str | None = None,
    *,
    decode_responses: bool = True,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    socket_timeout: float | None = DEFAULT_SOCKET_TIMEOUT_SEC,
    socket_connect_timeout: float | None = DEFAULT_SOCKET_CONNECT_TIMEOUT_SEC,
    retry_on_timeout: bool = DEFAULT_RETRY_ON_TIMEOUT,
    health_check_interval: int | None = DEFAULT_HEALTH_CHECK_INTERVAL_SEC,
    ping: bool = True,
    name: str = "redis",
) -> tuple[_async_redis.Redis, _async_redis.ConnectionPool]:
    """Build + ping an async Redis client backed by a fresh pool.

    Returns ``(client, pool)`` so callers can dispose of them
    symmetrically via ``close_async()`` on shutdown.

    Args:
        url, decode_responses, max_connections, socket_timeout,
            socket_connect_timeout, retry_on_timeout, health_check_interval:
            Forwarded to ``create_async_pool``.
        ping: If ``True`` (default), issues a single ``PING`` so the
            caller finds out about connect failures immediately instead
            of on first real op. Set ``False`` only for tests that use
            fakeredis / mock transport.
        name: Label used in the info log (``"redis"``,
            ``"redis-stream"``, etc.). Helps disambiguate services that
            create two clients (state + stream).

    Raises:
        redis.exceptions.RedisError: If ``ping`` is True and the server
        is unreachable or authentication fails. Callers that need the
        old "warn-and-continue" behaviour should wrap in try/except and
        flip their own ``redis_enabled`` flag.
    """
    pool = create_async_pool(
        url,
        decode_responses=decode_responses,
        max_connections=max_connections,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
        retry_on_timeout=retry_on_timeout,
        health_check_interval=health_check_interval,
    )
    client: _async_redis.Redis = _async_redis.Redis(connection_pool=pool)
    if ping:
        await client.ping()
    resolved_url = url or redis_url_from_env()
    logger.info(
        f"Connected to Redis ({name}) at {resolved_url} "
        f"(max={max_connections}, decode={decode_responses})"
    )
    return client, pool


async def close_async(
    client: _async_redis.Redis | None,
    pool: _async_redis.ConnectionPool | None,
    *,
    name: str = "redis",
) -> None:
    """Best-effort async teardown of a ``(client, pool)`` pair.

    Both steps are wrapped in a try/except and logged at DEBUG: a
    SIGTERM during an in-flight request frequently races the close,
    which redis-py surfaces as ``ConnectionError`` / ``RuntimeError``.
    There's nothing to recover on the shutdown path, so swallow and
    move on — but keep the breadcrumb (Phase 6 silent-except exit
    criterion: no bare ``except: pass``).
    """
    if client is not None:
        try:
            await client.close()
        except Exception as e:
            logger.debug("%s client.close() failed during shutdown: %s", name, e)
    if pool is not None:
        try:
            await pool.disconnect()
        except Exception as e:
            logger.debug("%s pool.disconnect() failed during shutdown: %s", name, e)


# ---------------------------------------------------------------------------
# Sync factory (redis)
# ---------------------------------------------------------------------------


def create_sync_pool(
    url: str | None = None,
    *,
    decode_responses: bool = True,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    socket_timeout: float | None = DEFAULT_SOCKET_TIMEOUT_SEC,
    socket_connect_timeout: float | None = DEFAULT_SOCKET_CONNECT_TIMEOUT_SEC,
    retry_on_timeout: bool = DEFAULT_RETRY_ON_TIMEOUT,
    health_check_interval: int | None = DEFAULT_HEALTH_CHECK_INTERVAL_SEC,
) -> _sync_redis.ConnectionPool:
    """Build a sync Redis ``ConnectionPool``. Kwargs mirror
    ``create_async_pool``.

    Used by ``can-processor-service`` (thread-based writer) and
    ``automation-service`` (sync control loop + mixin). New code should
    reach for ``create_async_pool`` instead — keeping this symmetric for
    the duration of the sync->async migration window.
    """
    resolved_url = url or redis_url_from_env()
    return _sync_redis.ConnectionPool.from_url(
        resolved_url,
        decode_responses=decode_responses,
        max_connections=max_connections,
        **_filter_pool_kwargs(
            socket_timeout,
            socket_connect_timeout,
            retry_on_timeout,
            health_check_interval,
        ),
    )


def create_sync_client(
    url: str | None = None,
    *,
    decode_responses: bool = True,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    socket_timeout: float | None = DEFAULT_SOCKET_TIMEOUT_SEC,
    socket_connect_timeout: float | None = DEFAULT_SOCKET_CONNECT_TIMEOUT_SEC,
    retry_on_timeout: bool = DEFAULT_RETRY_ON_TIMEOUT,
    health_check_interval: int | None = DEFAULT_HEALTH_CHECK_INTERVAL_SEC,
    ping: bool = True,
    name: str = "redis",
) -> tuple[_sync_redis.Redis, _sync_redis.ConnectionPool]:
    """Build + ping a sync Redis client backed by a fresh pool.

    See ``create_async_client`` for the argument contract — identical
    shape, minus the ``await`` on ping.
    """
    pool = create_sync_pool(
        url,
        decode_responses=decode_responses,
        max_connections=max_connections,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
        retry_on_timeout=retry_on_timeout,
        health_check_interval=health_check_interval,
    )
    client: _sync_redis.Redis = _sync_redis.Redis(connection_pool=pool)
    if ping:
        client.ping()
    resolved_url = url or redis_url_from_env()
    logger.info(
        f"Connected to Redis ({name}) at {resolved_url} "
        f"(max={max_connections}, decode={decode_responses})"
    )
    return client, pool


def close_sync(
    client: _sync_redis.Redis | None,
    pool: _sync_redis.ConnectionPool | None,
    *,
    name: str = "redis",
) -> None:
    """Best-effort sync teardown of a ``(client, pool)`` pair. Mirrors
    ``close_async``."""
    if client is not None:
        try:
            client.close()
        except Exception as e:
            logger.debug("%s client.close() failed during shutdown: %s", name, e)
    if pool is not None:
        try:
            pool.disconnect()
        except Exception as e:
            logger.debug("%s pool.disconnect() failed during shutdown: %s", name, e)


__all__ = [
    # Defaults (exported so services can see what's being applied).
    "REDIS_URL_DEFAULT",
    "DEFAULT_MAX_CONNECTIONS",
    "DEFAULT_SOCKET_TIMEOUT_SEC",
    "DEFAULT_SOCKET_CONNECT_TIMEOUT_SEC",
    "DEFAULT_HEALTH_CHECK_INTERVAL_SEC",
    "DEFAULT_RETRY_ON_TIMEOUT",
    # URL helper.
    "redis_url_from_env",
    # Async factory.
    "create_async_pool",
    "create_async_client",
    "close_async",
    # Sync factory.
    "create_sync_pool",
    "create_sync_client",
    "close_sync",
]
