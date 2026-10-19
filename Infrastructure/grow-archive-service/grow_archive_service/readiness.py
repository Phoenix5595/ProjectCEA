"""Readiness checks for the grow archive service.

``/ready`` reports three independently observable dependencies:

- ``database`` — the asyncpg pool answers ``SELECT 1``.
- ``schema`` — the archive catalog and expiry-cursor relations are present;
  a missing relation means archive selection cannot safely serve requests.
- ``archive_root`` — the owned archive root exists, is a directory, is owned
  by the service user and is writable. Nextcloud reachability is deliberately
  not part of readiness.

All checks are non-mutating: they never create files or schema.
"""

from __future__ import annotations

import asyncio
import os
import stat as stat_module
import time
from typing import TYPE_CHECKING, Any, Literal, Protocol, final

from pydantic import BaseModel, ConfigDict

from grow_archive_service.config import (
    SERVICE_NAME,
    ArchiveConfigurationError,
    ArchiveSettings,
)
from shared.health import DEFAULT_READY_TIMEOUT_SEC, check_postgres_pool

if TYPE_CHECKING:
    import asyncpg

ARCHIVE_SCHEMA_RELATIONS: tuple[str, ...] = (
    "public.grow_lifecycle",
    "public.archive_dataset",
    "public.archive_window",
    "public.archive_artifact",
    "public.archive_component",
    "public.archive_load",
    "public.archive_job",
    "public.archive_availability",
    "public.archive_scope_override",
    "public.archive_expiry_cursor",
)


class DependencyCheck(BaseModel):
    """One dependency result in the shared readiness shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    latency_ms: float | None = None
    detail: str | None = None


class ReadinessChecks(BaseModel):
    """Independently observable archive-service dependencies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    database: DependencyCheck
    schema: DependencyCheck
    archive_root: DependencyCheck


class ReadinessResponse(BaseModel):
    """The port-8006 readiness response contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready", "not_ready"]
    service: str
    checks: ReadinessChecks


class ReadinessResources(Protocol):
    """The runtime state needed to answer readiness probes."""

    pool: asyncpg.Pool | None
    settings: ArchiveSettings | None


def check_archive_root(settings: ArchiveSettings) -> dict[str, Any]:
    """Check the owned archive root without creating or writing anything."""
    root = settings.archive_root
    try:
        root_stat = root.stat()
    except FileNotFoundError:
        return {"ok": False, "detail": f"archive root missing: {root}"}
    except OSError as exc:
        return {"ok": False, "detail": f"archive root unreadable: {exc}"}
    if not stat_module.S_ISDIR(root_stat.st_mode):
        return {"ok": False, "detail": f"archive root is not a directory: {root}"}
    if root_stat.st_uid != os.geteuid():
        return {"ok": False, "detail": f"archive root not owned by service user: {root}"}
    if not os.access(root, os.W_OK | os.X_OK):
        return {"ok": False, "detail": f"archive root not writable: {root}"}
    return {"ok": True}


async def check_archive_schema(
    pool: asyncpg.Pool | None,
    *,
    relations: tuple[str, ...] = ARCHIVE_SCHEMA_RELATIONS,
    timeout: float = DEFAULT_READY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Check that every required archive schema relation exists."""
    if pool is None:
        return {"ok": False, "detail": "pool not initialized"}
    t0 = time.perf_counter()
    try:
        rows = await asyncio.wait_for(
            pool.fetch(
                "SELECT rel AS relation, to_regclass(rel) IS NOT NULL AS present"
                " FROM unnest($1::text[]) AS rel",
                list(relations),
            ),
            timeout=timeout,
        )
    except TimeoutError:
        return {"ok": False, "detail": f"timeout after {timeout * 1000:.0f}ms"}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    missing = tuple(str(row["relation"]) for row in rows if not row["present"])
    if missing:
        return {"ok": False, "detail": "archive schema missing: " + ", ".join(missing)}
    return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}


async def require_non_superuser_role(pool: asyncpg.Pool) -> None:
    """Fail startup when the service's database role has superuser rights.

    The archive service reuses the existing non-superuser ``cea_user`` role;
    a superuser connection would bypass the least-privilege boundary the
    lifecycle depends on.

    Raises:
        ArchiveConfigurationError: when the role is a superuser or cannot be
            resolved from ``pg_roles``.
    """
    role = await pool.fetchrow(
        "SELECT current_user AS role, rolsuper FROM pg_roles WHERE rolname = current_user"
    )
    if role is None:
        raise ArchiveConfigurationError(
            "cannot resolve the current database role from pg_roles; "
            "the grow archive service requires the existing non-superuser cea_user role"
        )
    if role["rolsuper"]:
        raise ArchiveConfigurationError(
            f"database role {role['role']!r} is a superuser; "
            "the grow archive service requires the existing non-superuser cea_user role"
        )


@final
class ArchiveReadinessProbe:
    """Compose the archive service's non-mutating readiness checks."""

    def __init__(self, resources: ReadinessResources) -> None:
        self._resources = resources

    async def check(self) -> ReadinessResponse:
        settings = self._resources.settings
        pool = self._resources.pool
        database = await check_postgres_pool(pool)
        schema = await check_archive_schema(pool)
        if settings is None:
            archive_root: dict[str, Any] = {
                "ok": False,
                "detail": "settings not initialized",
            }
        else:
            archive_root = check_archive_root(settings)
        checks = ReadinessChecks(
            database=DependencyCheck.model_validate(database),
            schema=DependencyCheck.model_validate(schema),
            archive_root=DependencyCheck.model_validate(archive_root),
        )
        status: Literal["ready", "not_ready"] = (
            "ready"
            if checks.database.ok and checks.schema.ok and checks.archive_root.ok
            else "not_ready"
        )
        return ReadinessResponse(status=status, service=SERVICE_NAME, checks=checks)
