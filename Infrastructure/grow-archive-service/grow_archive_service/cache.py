"""Verified ZIP cache governed by durable archive_load pins.

Database rows are authoritative for pins and artifact identity; filesystem
copies are content-addressed and are never treated as verified by filename.

Persistent cache copies obey the inclusive per-file (5 GiB) and total (25 GiB)
limits and evict LRU unpinned copies that are durable elsewhere; a copy that is
neither remote-verified nor contained in a verified annual parent is never
removed. Artifacts above the per-file limit use one shared content-addressed
staging copy per SHA256 whose lifetime is bounded by the live ``archive_load``
rows that reference it, so a shared annual container is materialized once
across its quarter loads and an oversized file survives drill-down reads until
its last pin is released or expires.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
from shared.nextcloud_dav import DavError

from .models import ArchiveError

GIB = 1024 ** 3
IDLE_SECONDS = 30 * 60
RESERVE_BYTES = GIB
LOAD_IDLE_SQL = "now()+interval '30 minutes'"

Downloader = Callable[[Path], Awaitable[tuple[str, int]]]
Verifier = Callable[[Path, str], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class CacheLimits:
    file_bytes: int = 5 * GIB
    total_bytes: int = 25 * GIB
    idle_seconds: int = IDLE_SECONDS


@dataclass(frozen=True, slots=True)
class CachedArtifact:
    sha256: str
    path: Path
    byte_size: int
    persistent: bool


class ArchiveCache:
    """Small asyncpg-compatible cache API; ``conn`` owns archive_load pins."""

    def __init__(self, cache_root: Path, staging_root: Path, *, limits: CacheLimits = CacheLimits(),
                 reserve: int = RESERVE_BYTES):
        self.cache_root = Path(cache_root)
        self.staging_root = Path(staging_root)
        self.limits = limits
        self.reserve = reserve
        self._lock = asyncio.Lock()
        self._materialization_lock = asyncio.Lock()
        self._readers: dict[str, int] = {}
        self._input_pins: dict[str, int] = {}
        self._deferred_unlink: set[str] = set()

    @staticmethod
    def content_path(root: Path, sha256: str) -> Path:
        value = sha256.lower()
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("archive_integrity_failed")
        return Path(root) / value[:2] / f"{value}.zip"

    def staging_path(self, sha256: str) -> Path:
        """Shared oversized-artifact copy; removed when no load pins remain."""
        return self.content_path(self.staging_root / "loads", sha256)

    def owns_cache_path(self, path: Path) -> bool:
        return not Path(path).is_symlink() and self._is_under(self.cache_root, path)

    def owns_staging_path(self, path: Path) -> bool:
        return not Path(path).is_symlink() and self._is_under(self.staging_root, path)

    async def has_active_reader(self, path: Path) -> bool:
        value = str(Path(path).resolve())
        async with self._lock:
            return bool(self._readers.get(value, 0) or self._input_pins.get(value, 0))

    def _is_under(self, root: Path, path: Path) -> bool:
        try:
            Path(path).resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    async def preflight(self, root: Path, *, raw_bytes: int, zip_bytes: int) -> None:
        """Refuse to start when raw plus ZIP plus the reserve would not fit."""
        if raw_bytes < 0 or zip_bytes < 0:
            raise DavError("archive_local_space_insufficient")
        probe = root if root.exists() else root.parent
        free = await asyncio.to_thread(lambda: shutil.disk_usage(probe).free)
        if free <= raw_bytes + zip_bytes + self.reserve:
            raise DavError("archive_local_space_insufficient")

    def require_space(self, root: Path, *, needed: int = 0) -> None:
        """Streaming guard: stop safely before exhausting the reserve."""
        if shutil.disk_usage(root).free <= needed + self.reserve:
            raise DavError("archive_local_space_insufficient")

    async def materialize(self, pool: asyncpg.Pool, *, artifact_id: uuid.UUID, sha256: str,
                          byte_size: int, downloader: Downloader,
                          verifier: Verifier, pin_input: bool = False) -> tuple[CachedArtifact, Any]:
        """Ensure one locally verified copy exists; download only when absent.

        The optional input pin protects either cache or staging between materializing
        it and installing its durable archive_load pin or using it as the prior archive
        for a time-window rebuild.
        Each such temporary pin is paired with release_input.
        """
        async with self._materialization_lock:
            if byte_size <= 0 or len(sha256) != 64:
                raise DavError("archive_integrity_failed")
            async with pool.acquire() as conn:
                persistent = await self._budget_allows(conn, sha256=sha256, byte_size=byte_size)
            target = (self.content_path(self.cache_root, sha256) if persistent else self.staging_path(sha256)).resolve()
            input_pin = False
            if pin_input:
                value = str(target)
                async with self._lock:
                    self._input_pins[value] = self._input_pins.get(value, 0) + 1
                    input_pin = True
            scratch: Path | None = None
            try:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                manifest: Any = None
                if await asyncio.to_thread(file_matches, target, sha256, byte_size):
                    try:
                        manifest = await verifier(target, sha256)
                    except (DavError, ArchiveError, ValueError):
                        # A corrupt local copy is re-downloaded over the same path.
                        manifest = None
                if manifest is None:
                    scratch = self.staging_root / "parts" / f"{uuid.uuid4().hex}.part"
                    scratch.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    digest, size = await downloader(scratch)
                    if size != byte_size or digest != sha256:
                        raise DavError("archive_integrity_failed")
                    manifest = await verifier(scratch, sha256)
                    await asyncio.to_thread(os.replace, scratch, target)
                    os.chmod(target, 0o600)
                if persistent:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE public.archive_artifact SET local_path=$2,local_verified_at=now(),last_accessed_at=now() "
                            "WHERE artifact_id=$1 AND sha256=$3 AND byte_size=$4 AND state IN ('verified','superseded')",
                            artifact_id, str(target), sha256, byte_size)
                        await self.evict_unpinned(conn)
                return CachedArtifact(sha256, target, byte_size, persistent), manifest
            except BaseException:
                if scratch is not None:
                    await asyncio.to_thread(scratch.unlink, missing_ok=True)
                if input_pin:
                    async with pool.acquire() as conn:
                        await self.release_input(conn, target, release_pin=True)
                raise
    async def adopt_local_copy(self, pool: asyncpg.Pool, *, artifact_id: uuid.UUID, source: Path,
                               sha256: str, byte_size: int) -> Path:
        """Move an already-verified local ZIP into the persistent cache when eligible."""
        async with self._materialization_lock:
            source = Path(source)
            if not await asyncio.to_thread(file_matches, source, sha256, byte_size):
                raise DavError("archive_integrity_failed")
            async with pool.acquire() as conn:
                persistent = await self._budget_allows(conn, sha256=sha256, byte_size=byte_size)
            if not persistent:
                return source
            target = self.content_path(self.cache_root, sha256).resolve()
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not await asyncio.to_thread(file_matches, target, sha256, byte_size):
                await asyncio.to_thread(os.replace, source, target)
                os.chmod(target, 0o600)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE public.archive_artifact SET local_path=$2,local_verified_at=now(),last_accessed_at=now() "
                    "WHERE artifact_id=$1 AND sha256=$3 AND byte_size=$4 AND state IN ('verified','superseded')",
                    artifact_id, str(target), sha256, byte_size)
                await self.evict_unpinned(conn)
            return target

    async def _budget_allows(self, conn: asyncpg.Connection, *, sha256: str, byte_size: int) -> bool:
        if byte_size <= 0 or byte_size > self.limits.file_bytes:
            return False
        target = str(self.content_path(self.cache_root, sha256).resolve())
        if Path(target).is_file() and await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.archive_artifact WHERE local_path=$1 "
                "AND state IN ('verified','superseded'))", target):
            return True
        await self.evict_unpinned(conn, needed_bytes=byte_size)
        rows = await conn.fetch(
            "SELECT DISTINCT local_path FROM public.archive_artifact "
            "WHERE local_path IS NOT NULL AND state IN ('verified','superseded')")
        total = 0
        for row in rows:
            path = Path(str(row["local_path"]))
            if path.is_file():
                total += path.stat().st_size
        return total + byte_size <= self.limits.total_bytes

    async def evict_unpinned(self, conn: asyncpg.Connection, *, needed_bytes: int = 0) -> int:
        """Evict LRU unpinned copies that have a verified remote counterpart.

        Superseded copies whose bytes live in a verified annual container are
        durable too; a copy that is neither remote-verified nor contained in a
        verified parent is never removed.
        """
        removed = 0
        async with self._lock:
            rows = await conn.fetch(
                "SELECT a.artifact_id,a.dataset_id,a.revision,a.local_path,a.state,a.sha256,"
                "(a.remote_instance_id IS NOT NULL AND a.remote_path IS NOT NULL "
                " AND a.sha256 IS NOT NULL) AS remote_verified,"
                "EXISTS(SELECT 1 FROM public.archive_component c "
                " JOIN public.archive_artifact p ON p.artifact_id=c.parent_artifact_id "
                " WHERE c.child_dataset_id=a.dataset_id AND c.child_revision=a.revision "
                " AND p.state='verified') AS in_verified_parent,"
                "EXISTS(SELECT 1 FROM public.archive_load l LEFT JOIN public.archive_artifact pin_a "
                "ON pin_a.artifact_id=l.artifact_id WHERE l.expires_at>now() "
                "AND (l.staging_path=a.local_path OR pin_a.local_path=a.local_path)) AS path_pinned "
                "FROM public.archive_artifact a WHERE a.local_path IS NOT NULL "
                "AND a.state IN ('verified','superseded') "
                "ORDER BY a.last_accessed_at NULLS FIRST,a.verified_at,a.artifact_id")
            unique: dict[str, Mapping[str, Any]] = {}
            pinned_paths: set[str] = set()
            for row in rows:
                value = str(row["local_path"])
                unique.setdefault(value, row)
                if row["path_pinned"]:
                    pinned_paths.add(value)
            entries = list(unique.values())
            total = 0
            for row in entries:
                path = Path(str(row["local_path"]))
                if path.is_file():
                    total += path.stat().st_size
            for row in entries:
                if total + needed_bytes <= self.limits.total_bytes:
                    break
                value = str(row["local_path"])
                path = Path(value)
                if not self.owns_cache_path(path):
                    continue
                if value in pinned_paths or self._readers.get(value, 0) or self._input_pins.get(value, 0):
                    continue
                if not bool(row["remote_verified"]) and not bool(row["in_verified_parent"]):
                    continue
                if not path.is_file():
                    await conn.execute(
                        "UPDATE public.archive_artifact SET local_path=NULL,local_verified_at=NULL "
                        "WHERE local_path=$1", value)
                    continue
                size = path.stat().st_size
                await asyncio.to_thread(path.unlink)
                await conn.execute(
                    "UPDATE public.archive_artifact SET local_path=NULL,local_verified_at=NULL "
                    "WHERE local_path=$1", value)
                total -= size
                removed += 1
        return removed

    async def drop_local_copy(self, conn: asyncpg.Connection, local_path: str) -> None:
        """Remove a superseded local copy only after selections/readers finish."""
        path = Path(local_path)
        if not (self.owns_cache_path(path) or self.owns_staging_path(path)):
            raise DavError("archive_integrity_failed")
        value = str(path.resolve())
        async with self._lock:
            shared = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.archive_artifact WHERE local_path=$1 AND state='verified')",
                value)
            if shared:
                return
            pinned = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.archive_load l LEFT JOIN public.archive_artifact a "
                "ON a.artifact_id=l.artifact_id WHERE l.expires_at>now() "
                "AND (l.staging_path=$1 OR a.local_path=$1))", value)
            if pinned or self._readers.get(value, 0) or self._input_pins.get(value, 0):
                self._deferred_unlink.add(value)
                return
            await asyncio.to_thread(path.unlink, missing_ok=True)
            await conn.execute(
                "UPDATE public.archive_artifact SET local_path=NULL,local_verified_at=NULL WHERE local_path=$1",
                value)

    async def load_path(self, conn: asyncpg.Connection, row: Mapping[str, Any]) -> Path | None:
        """Resolve one pinned load's local file; None for compressed-db loads."""
        if row["staging_path"]:
            path = self._owned_staging_path(str(row["staging_path"]))
        elif row["artifact_id"] is not None:
            value = await conn.fetchval(
                "SELECT local_path FROM public.archive_artifact WHERE artifact_id=$1", row["artifact_id"])
            if not value:
                raise DavError("archive_load_required")
            raw_path = Path(str(value))
            if raw_path.is_symlink() or not self.owns_cache_path(raw_path):
                raise DavError("archive_integrity_failed")
            path = raw_path.resolve()
        else:
            return None
        if not path.is_file():
            raise DavError("archive_load_required")
        return path

    async def touch_load(self, conn: asyncpg.Connection,
                         load_id: uuid.UUID) -> tuple[Mapping[str, Any], Path | None]:
        row = await conn.fetchrow(
            "UPDATE public.archive_load SET last_accessed_at=now(),expires_at=" + LOAD_IDLE_SQL +
            " WHERE load_id=$1 AND expires_at>now() RETURNING *", load_id)
        if row is None:
            raise DavError("archive_load_required")
        path = await self.load_path(conn, row)
        if row["artifact_id"] is not None:
            await conn.execute(
                "UPDATE public.archive_artifact SET last_accessed_at=now() WHERE artifact_id=$1",
                row["artifact_id"])
        return row, path

    async def acquire_load(self, conn: asyncpg.Connection,
                          load_id: uuid.UUID) -> tuple[Mapping[str, Any], Path | None]:
        """Refresh and acquire an in-process reader pin before exposing the path."""
        async with self._lock:
            row = await conn.fetchrow(
                "UPDATE public.archive_load SET last_accessed_at=now(),expires_at=" + LOAD_IDLE_SQL +
                " WHERE load_id=$1 AND expires_at>now() RETURNING *", load_id)
            if row is None:
                raise DavError("archive_load_required")
            path = await self.load_path(conn, row)
            if row["artifact_id"] is not None:
                await conn.execute(
                    "UPDATE public.archive_artifact SET last_accessed_at=now() WHERE artifact_id=$1",
                    row["artifact_id"])
            if path is not None:
                value = str(path)
                self._readers[value] = self._readers.get(value, 0) + 1
            return row, path

    async def finish_load_read(self, conn: asyncpg.Connection, path: Path | None) -> None:
        """Release an in-flight reader; deferred unlink waits for the final reader."""
        if path is None:
            return
        value = str(path)
        async with self._lock:
            count = self._readers.get(value, 0)
            if count <= 1:
                self._readers.pop(value, None)
            else:
                self._readers[value] = count - 1
        await self._unlink_if_released(conn, value, staging=False)

    async def _unlink_if_released(self, conn: asyncpg.Connection, value: str, *, staging: bool) -> None:
        async with self._lock:
            if self._readers.get(value, 0) or self._input_pins.get(value, 0):
                if staging or value in self._deferred_unlink:
                    self._deferred_unlink.add(value)
                return
            pinned = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.archive_load l LEFT JOIN public.archive_artifact a "
                "ON a.artifact_id=l.artifact_id WHERE l.expires_at>now() "
                "AND (l.staging_path=$1 OR a.local_path=$1))", value)
            if pinned:
                if staging or value in self._deferred_unlink:
                    self._deferred_unlink.add(value)
                return
            if not staging and value not in self._deferred_unlink:
                return
            shared = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.archive_artifact WHERE local_path=$1 AND state='verified')",
                value)
            self._deferred_unlink.discard(value)
            if shared:
                return
            path = Path(value)
            if self.owns_staging_path(path) or self.owns_cache_path(path):
                await asyncio.to_thread(path.unlink, missing_ok=True)
                await conn.execute(
                    "UPDATE public.archive_artifact SET local_path=NULL,local_verified_at=NULL WHERE local_path=$1",
                    value)

    async def release_load(self, conn: asyncpg.Connection, load_id: uuid.UUID) -> None:
        row = await conn.fetchrow(
            "DELETE FROM public.archive_load WHERE load_id=$1 RETURNING staging_path,artifact_id", load_id)
        if not row:
            return
        if row["staging_path"]:
            value = str(self._owned_staging_path(str(row["staging_path"])))
            await self._unlink_if_released(conn, value, staging=True)
        if row["artifact_id"] is not None:
            artifact = await conn.fetchrow(
                "SELECT local_path,state FROM public.archive_artifact WHERE artifact_id=$1",
                row["artifact_id"])
            if artifact is not None and artifact["state"] == "superseded" and artifact["local_path"]:
                value = str(artifact["local_path"])
                async with self._lock:
                    self._deferred_unlink.add(value)
                await self._unlink_if_released(conn, value, staging=False)

    async def expire_loads(self, conn: asyncpg.Connection) -> int:
        rows = await conn.fetch(
            "DELETE FROM public.archive_load WHERE expires_at<=now() RETURNING staging_path,artifact_id")
        staging_paths = {str(row["staging_path"]) for row in rows if row["staging_path"]}
        artifact_ids = {row["artifact_id"] for row in rows if row["artifact_id"] is not None}
        for raw in staging_paths:
            value = str(self._owned_staging_path(raw))
            await self._unlink_if_released(conn, value, staging=True)
        for artifact_id in artifact_ids:
            artifact = await conn.fetchrow(
                "SELECT local_path,state FROM public.archive_artifact WHERE artifact_id=$1", artifact_id)
            if artifact is not None and artifact["state"] == "superseded" and artifact["local_path"]:
                value = str(artifact["local_path"])
                async with self._lock:
                    self._deferred_unlink.add(value)
                await self._unlink_if_released(conn, value, staging=False)
        return len(rows)

    async def release_input(self, conn: asyncpg.Connection, path: Path, *,
                                   release_pin: bool = False) -> None:
        """Release a provider/materialize reference; discard only unpinned staging."""
        if not (self.owns_staging_path(path) or self.owns_cache_path(path)):
            raise DavError("archive_integrity_failed")
        value = str(Path(path).resolve())
        if release_pin:
            async with self._lock:
                count = self._input_pins.get(value, 0)
                if count <= 1:
                    self._input_pins.pop(value, None)
                else:
                    self._input_pins[value] = count - 1
        await self._unlink_if_released(conn, value, staging=self.owns_staging_path(path))
    def _owned_staging_path(self, value: str) -> Path:
        path = Path(value).resolve()
        if Path(value).is_symlink():
            raise DavError("archive_integrity_failed")
        try:
            path.relative_to(self.staging_root.resolve())
        except ValueError as exc:
            raise DavError("archive_integrity_failed") from exc
        return path


def file_matches(path: Path, expected_hash: str, expected_size: int) -> bool:
    """Cheap identity check; full ZIP verification happens through the verifier."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected_size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == expected_hash
