"""Shared asyncpg pool factory + env-driven config helper.

Lifts two patterns that were duplicated across all four services with a
``DatabaseManager`` (automation, backend, soil, weather):

  1. **Env-driven DB config** — read ``POSTGRES_HOST/DB/USER/PORT`` from the
     environment with sensible defaults, plus the password via
     ``shared.db_credentials.load_postgres_password()`` (which already prefers
     ``$CREDENTIALS_DIRECTORY/postgres_password`` over ``POSTGRES_PASSWORD``
     per Phase 3.8). Four services were doing this with byte-identical
     7-line blocks.

  2. **asyncpg pool creation with connect-time retry** — three of the four
     services already had a 5-attempt exponential-backoff retry around
     ``asyncpg.create_pool``; the *backend* did not, so a transient Postgres
     unavailability at boot would crash it instead of riding through. The
     lift fixes that asymmetry as a side effect.

The shape is deliberately small: one config helper + one pool factory.
Anything more elaborate (transaction context managers, repository base
classes, query-pattern helpers) belongs to the calling service or to a
later Phase 6 step — this module owns *connection lifecycle*, nothing
more.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import asyncpg

from shared.db_credentials import load_postgres_password
from shared.infra_logging import get_logger

logger = get_logger(__name__)


# Sentinel default — services that want different pool sizes pass their own.
DEFAULT_MIN_POOL_SIZE: int = 2
DEFAULT_MAX_POOL_SIZE: int = 10
DEFAULT_COMMAND_TIMEOUT_SECONDS: int = 30
DEFAULT_MAX_CONNECT_RETRIES: int = 5
DEFAULT_BASE_RETRY_DELAY_SECONDS: float = 1.0
DEFAULT_MAX_RETRY_DELAY_SECONDS: float = 60.0


def db_config_from_env() -> dict[str, Any]:
    """Build a DB config dict from the standard CEA environment variables.

    Reads:
      - ``POSTGRES_HOST``  (default ``"localhost"``)
      - ``POSTGRES_DB``    (default ``"cea_sensors"``)
      - ``POSTGRES_USER``  (default ``"cea_user"``)
      - ``POSTGRES_PORT``  (default ``5432``)

    Password comes from ``load_postgres_password()`` so the Phase 3.8
    ``LoadCredential=`` path takes precedence over ``POSTGRES_PASSWORD``
    when present.

    Returns the dict shape that ``create_pool()`` expects.
    """
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "database": os.getenv("POSTGRES_DB", "cea_sensors"),
        "user": os.getenv("POSTGRES_USER", "cea_user"),
        "password": load_postgres_password(),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
    }


async def create_pool(
    db_config: dict[str, Any],
    *,
    application_name: str,
    min_size: int = DEFAULT_MIN_POOL_SIZE,
    max_size: int = DEFAULT_MAX_POOL_SIZE,
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_CONNECT_RETRIES,
    base_retry_delay: float = DEFAULT_BASE_RETRY_DELAY_SECONDS,
    max_retry_delay: float = DEFAULT_MAX_RETRY_DELAY_SECONDS,
) -> asyncpg.Pool:
    """Create an asyncpg pool, retrying transient connect failures.

    Args:
        db_config: dict with ``host`` / ``database`` / ``user`` / ``password`` /
            ``port`` keys (typically built by ``db_config_from_env()``).
        application_name: Set as ``server_settings.application_name`` so the
            connection shows up identifiably in ``pg_stat_activity``. One per
            service (e.g. ``"weather_service"``, ``"cea_backend"``).
        min_size, max_size: Pool size bounds. Defaults match what every
            service was using before the lift.
        command_timeout: Per-query timeout in seconds.
        max_retries: Total connect attempts including the first. Set to 1 to
            disable the retry loop.
        base_retry_delay, max_retry_delay: Exponential backoff bounds. The
            wait before retry N is ``min(base * 2**(N-1), max)``. No jitter
            here because the connection target is a single local Postgres,
            not a fleet of upstreams — thundering-herd is not a concern.

    Raises:
        ConnectionError: After ``max_retries`` consecutive failures, wraps
        the final exception with attempt count for log clarity.
    """
    if max_retries < 1:
        raise ValueError(f"max_retries must be >= 1, got {max_retries}")

    last_exc: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            pool = await asyncpg.create_pool(
                host=db_config["host"],
                database=db_config["database"],
                user=db_config["user"],
                password=db_config["password"],
                port=db_config["port"],
                min_size=min_size,
                max_size=max_size,
                command_timeout=command_timeout,
                server_settings={"application_name": application_name},
            )
            logger.info(
                f"Connected to TimescaleDB ({application_name}) on attempt {attempt}/{max_retries}"
            )
            return pool
        except Exception as e:
            last_exc = e
            if attempt >= max_retries:
                # Wrap in ConnectionError so callers can catch a stable type
                # regardless of which underlying asyncpg error fired.
                raise ConnectionError(
                    f"Failed to connect to TimescaleDB ({application_name}) "
                    f"after {max_retries} attempts: {e}"
                ) from e
            wait_time = min(
                base_retry_delay * (2 ** (attempt - 1)),
                max_retry_delay,
            )
            logger.warning(
                f"DB connect attempt {attempt}/{max_retries} failed for "
                f"{application_name}: {e}. Retrying in {wait_time}s..."
            )
            await asyncio.sleep(wait_time)

    # Defensive: the loop must have either returned or raised.
    assert last_exc is not None
    raise last_exc


__all__ = [
    "db_config_from_env",
    "create_pool",
    "DEFAULT_MIN_POOL_SIZE",
    "DEFAULT_MAX_POOL_SIZE",
    "DEFAULT_COMMAND_TIMEOUT_SECONDS",
    "DEFAULT_MAX_CONNECT_RETRIES",
    "DEFAULT_BASE_RETRY_DELAY_SECONDS",
    "DEFAULT_MAX_RETRY_DELAY_SECONDS",
]
