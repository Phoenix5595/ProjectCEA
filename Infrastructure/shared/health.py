"""Shared readiness/liveness probe primitives.

Each CEA service exposes a ``/ready`` endpoint that pings every hard
dependency (asyncpg pool, redis client) within a tight per-check budget
(default 500 ms) and returns a JSON dict + 503 if anything fails. Pre-Phase
6 every service had its own near-identical 18-line copy of the postgres
pool check and a 15-line copy of the redis ping check, varying only in:

  - the ``"service"`` label string
  - whether the redis client was ``redis.Redis`` (sync) or
    ``redis.asyncio.Redis`` (async) — automation-service is the only sync
    holdout pending a Phase 6 follow-up; the rest are async.

These helpers absorb the boilerplate while preserving the exact JSON
shape every dashboard/orchestrator expects:

    {
      "ok": true | false,
      "latency_ms": 1.7,        # only on success
      "detail": "..."           # only on failure
    }

A successful check always carries ``latency_ms``; a failure always
carries ``detail`` with a human-readable reason. The ``ok`` key is
present in every result so the caller can compose readiness with
``all(c["ok"] for c in checks.values())``.

Design notes
------------
- **No exceptions escape.** Every check returns a dict; the readiness
  endpoint should never propagate to FastAPI's exception handler. A
  failed dependency is observability data, not an error.
- **Per-check timeout, not whole-endpoint timeout.** Each dependency
  gets its own ``asyncio.wait_for`` so a slow Postgres can't poison the
  Redis check's reading. The endpoint itself stays unbounded.
- **Tight default.** 500 ms matches what every service was already using
  and what the systemd ``ExecStartPost=`` health probes assume. Increase
  per-call only if a specific check is known to need more headroom.
- **Sync-client bridge.** ``check_redis_sync_client`` exists for
  automation-service which still uses sync ``redis.Redis``; the call is
  bridged via ``asyncio.to_thread`` so the readiness endpoint itself
  remains async and the event loop stays free.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg
    import redis
    import redis.asyncio


DEFAULT_READY_TIMEOUT_SEC: float = 0.5


async def check_postgres_pool(
    pool: asyncpg.Pool | None,
    *,
    timeout: float = DEFAULT_READY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Run ``SELECT 1`` against an asyncpg pool with a per-call timeout.

    Returns the standard check-result dict (see module docstring). Never
    raises. A ``None`` pool reports ``{"ok": False, "detail": "pool not
    initialized"}`` — the same shape the legacy inline checks produced,
    so existing dashboards/log greps keep working.
    """
    if pool is None:
        return {"ok": False, "detail": "pool not initialized"}
    t0 = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            val = await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=timeout)
        result: dict[str, Any] = {
            "ok": val == 1,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        if val != 1:
            result["detail"] = f"unexpected SELECT 1 value: {val!r}"
        return result
    except TimeoutError:
        return {"ok": False, "detail": f"timeout after {timeout * 1000:.0f}ms"}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


async def check_redis_async_client(
    client: redis.asyncio.Redis | None,
    *,
    timeout: float = DEFAULT_READY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """PING an async ``redis.asyncio.Redis`` client with a per-call timeout.

    Returns the standard check-result dict. Never raises.
    """
    if client is None:
        return {"ok": False, "detail": "client not initialized"}
    t0 = time.perf_counter()
    try:
        pong = await asyncio.wait_for(client.ping(), timeout=timeout)
        result: dict[str, Any] = {
            "ok": bool(pong),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        if not pong:
            result["detail"] = "PING returned falsy"
        return result
    except TimeoutError:
        return {"ok": False, "detail": f"timeout after {timeout * 1000:.0f}ms"}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


async def check_redis_sync_client(
    client: redis.Redis | None,
    *,
    timeout: float = DEFAULT_READY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """PING a sync ``redis.Redis`` client by bridging to a worker thread.

    Used today only by ``automation-service``, whose Redis client is still
    synchronous. The bridge keeps the readiness endpoint itself async so
    the event loop remains free during the (cheap, sub-ms) ping. When
    automation-service migrates to ``redis.asyncio`` this can be retired.
    """
    if client is None:
        return {"ok": False, "detail": "client not initialized"}
    t0 = time.perf_counter()
    try:
        pong = await asyncio.wait_for(asyncio.to_thread(client.ping), timeout=timeout)
        result: dict[str, Any] = {
            "ok": bool(pong),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        if not pong:
            result["detail"] = "PING returned falsy"
        return result
    except TimeoutError:
        return {"ok": False, "detail": f"timeout after {timeout * 1000:.0f}ms"}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def all_ok(checks: dict[str, dict[str, Any]]) -> bool:
    """Return True iff every check in the dict reports ``ok=True``.

    Convenience helper so the per-service /ready endpoint stays a
    one-liner: ``ok = all_ok(out["checks"])``.
    """
    return all(c.get("ok", False) for c in checks.values())


__all__ = [
    "DEFAULT_READY_TIMEOUT_SEC",
    "check_postgres_pool",
    "check_redis_async_client",
    "check_redis_sync_client",
    "all_ok",
]
