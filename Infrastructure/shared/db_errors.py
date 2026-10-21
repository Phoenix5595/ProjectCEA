"""Classify database errors as transient (retry later) or permanent (drop).

Outage buffers retain rows only for transient errors: the server is down,
restarting, refusing connections, out of resources, or the connection broke.
A permanent error (bad data, constraint violation, missing relation) would fail
again on every retry and block all newer rows, so callers drop that batch.
Works for asyncpg and psycopg2 without importing either eagerly.
"""

from __future__ import annotations

# SQLSTATE classes: 08 connection exception, 53 insufficient resources,
# 57P operator intervention (admin/crash shutdown, cannot connect now),
# 40 transaction rollback (serialization failure, deadlock).
_TRANSIENT_SQLSTATE_PREFIXES = ("08", "53", "57P", "40")


def _driver_transient_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = []
    try:
        import asyncpg

        types += [asyncpg.InterfaceError, asyncpg.PostgresConnectionError]
    except ImportError:
        pass
    try:
        import psycopg2

        types += [psycopg2.OperationalError, psycopg2.InterfaceError]
    except ImportError:
        pass
    return tuple(types)


_DRIVER_TRANSIENT_TYPES = _driver_transient_types()


def is_transient_db_error(error: BaseException) -> bool:
    """True when retrying the same rows later can succeed."""
    if isinstance(error, (OSError, TimeoutError)):  # includes ConnectionError
        return True
    sqlstate = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    if isinstance(sqlstate, str) and sqlstate:
        return sqlstate.startswith(_TRANSIENT_SQLSTATE_PREFIXES)
    return isinstance(error, _DRIVER_TRANSIENT_TYPES)
