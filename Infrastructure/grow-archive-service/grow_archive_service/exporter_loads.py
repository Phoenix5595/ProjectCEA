"""Explicit Historical ZIP load pins for ExportService: target resolution, pinning, touch, release and expiry."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from shared.infra_logging import get_logger
from shared.nextcloud_dav import DavError

from .annual import manifest_sha256
from .cache import IDLE_SECONDS
from .exporter_support import _annual_component, _require_manifest_identity, _translates
from .manifest import ArchiveManifest
from .models import ArchiveError, JobClaim

logger = get_logger("grow_archive_service.exporter")


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class LoadTarget:
    """One resolvable Historical selection from a verified ZIP."""

    dataset_id: UUID
    revision: int
    source_kind: str
    artifact_id: UUID | None
    member_prefix: str | None
    sha256: str | None
    byte_size: int | None


@dataclass(frozen=True, slots=True)
class LoadPin:
    """One durable explicit ZIP selection."""

    load_id: UUID
    dataset_id: UUID
    revision: int
    source_kind: str
    artifact_id: UUID | None
    member_prefix: str | None
    local_path: Path | None
    expires_at: datetime
    manifest: ArchiveManifest | None = None


@dataclass(frozen=True, slots=True)
class LoadRequest:
    revision: int
    load: LoadPin | None
    job_id: UUID | None


class LoadMixin:
    # ------------------------------------------------------------------
    # Loads (explicit Historical selection pins)
    # ------------------------------------------------------------------

    @_translates
    async def resolve_load_target(self, dataset_id: UUID,
                                  requested_revision: int | None = None) -> LoadTarget:
        async with self.pool.acquire() as conn:
            return await self._resolve_load_target(conn, dataset_id, requested_revision)

    async def _resolve_load_target(self, conn: asyncpg.Connection, dataset_id: UUID,
                                   requested_revision: int | None) -> LoadTarget:
        dataset = await conn.fetchrow("SELECT * FROM public.archive_dataset WHERE dataset_id=$1", dataset_id)
        if dataset is None:
            raise ArchiveError("archive_not_found", status_code=404)
        current = int(dataset["data_revision"])
        published = int(dataset["published_revision"]) if dataset["published_revision"] is not None else None
        requested = int(requested_revision) if requested_revision is not None else (published or current)
        if requested == current and published is None:
            raise ArchiveError("archive_export_required")
        if requested == current and published != current:
            raise ArchiveError("archive_revision_pending")
        if requested != current and requested != published:
            raise ArchiveError("archive_revision_conflict")
        row = await conn.fetchrow(
            "SELECT artifact_id,sha256,byte_size FROM public.archive_artifact "
            "WHERE dataset_id=$1 AND revision=$2 AND state='verified'", dataset_id, requested)
        if row is not None:
            return LoadTarget(dataset_id, requested, "zip", row["artifact_id"], None,
                              str(row["sha256"]), int(row["byte_size"]))
        row = await conn.fetchrow(
            "SELECT a.artifact_id,a.sha256,a.byte_size,c.member_prefix FROM public.archive_component c "
            "JOIN public.archive_artifact a ON a.artifact_id=c.parent_artifact_id "
            "WHERE c.child_dataset_id=$1 AND c.child_revision=$2 AND a.state='verified'",
            dataset_id, requested)
        if row is not None:
            return LoadTarget(dataset_id, requested, "zip", row["artifact_id"], str(row["member_prefix"]),
                              str(row["sha256"]), int(row["byte_size"]))
        raise ArchiveError("archive_export_required")

    @_translates
    async def enqueue_load(self, dataset_id: UUID, requested_revision: int | None = None) -> LoadRequest:
        """200-style existing ready load or a durable load job (202)."""
        async with self.pool.acquire() as conn:
            dataset = await conn.fetchrow(
                "SELECT data_revision FROM public.archive_dataset WHERE dataset_id=$1", dataset_id)
            if dataset is None:
                raise ArchiveError("archive_not_found", status_code=404)
            target = await self._resolve_load_target(conn, dataset_id, requested_revision)
            existing = await conn.fetchrow(
                "SELECT load_id FROM public.archive_load WHERE dataset_id=$1 AND revision=$2 "
                "AND artifact_id IS NOT DISTINCT FROM $3 AND expires_at>now() "
                "ORDER BY created_at DESC LIMIT 1",
                dataset_id, target.revision, target.artifact_id)
        if existing is not None:
            return LoadRequest(target.revision, await self.load_pin(existing["load_id"]), None)
        job = await self.queue.enqueue(dataset_id, "load", int(dataset["data_revision"]),
                                       target_revision=target.revision)
        return LoadRequest(target.revision, None, job.job_id)

    async def _validate_annual_component_binding(self, artifact_id: UUID, dataset_id: UUID,
                                                 revision: int, member_prefix: str,
                                                 container: ArchiveManifest) -> None:
        entry = _annual_component(container.data, dataset_id)
        if (entry is None or int(entry.get("revision", 0)) != revision
                or entry.get("member_prefix") != member_prefix):
            raise ArchiveError("archive_integrity_failed")
        child = entry.get("manifest")
        if not isinstance(child, Mapping):
            raise ArchiveError("archive_integrity_failed")
        row = await self.pool.fetchrow(
            "SELECT child_revision,member_prefix,manifest_sha256,observation_counts "
            "FROM public.archive_component WHERE parent_artifact_id=$1 AND child_dataset_id=$2",
            artifact_id, dataset_id)
        if row is None:
            raise ArchiveError("archive_integrity_failed")
        counts = _json_mapping(row["observation_counts"])
        if (int(row["child_revision"]) != revision or row["member_prefix"] != member_prefix
                or row["manifest_sha256"] != manifest_sha256(child)
                or entry.get("manifest_sha256") != row["manifest_sha256"]
                or counts != dict(child.get("observation_counts") or {})):
            raise ArchiveError("archive_integrity_failed")

    @_translates
    async def run_load(self, claim: JobClaim) -> LoadPin:
        """Load job executor: materialize and pin one explicit selection."""
        dataset_id = claim.fence.dataset_id
        raw_revision = claim.job.checkpoint.get("target_revision")
        target_revision = int(raw_revision) if raw_revision is not None else None
        async with self.pool.acquire() as conn:
            await self.queue.check_fence(conn, claim)
            target = await self._resolve_load_target(conn, dataset_id, target_revision)
        load_id = uuid4()
        async with self.pool.acquire() as conn, conn.transaction():
            await self.queue.check_fence(conn, claim, lock=True)
            await conn.execute(
                "INSERT INTO public.archive_load(load_id,dataset_id,revision,source_kind,artifact_id,"
                "member_prefix,staging_path,expires_at) "
                "VALUES($1,$2,$3,$4,$5,$6,NULL,now()+interval '30 minutes')",
                load_id, dataset_id, target.revision, target.source_kind, target.artifact_id,
                target.member_prefix)
        path: Path | None = None
        persistent = False
        loaded_manifest: ArchiveManifest | None = None
        input_pin = False
        try:
            if target.source_kind == "zip" and target.artifact_id is not None:
                artifact, loaded_manifest = await self._materialize_artifact(
                    target.artifact_id, pin_input=True)
                path, persistent = artifact.path, artifact.persistent
                input_pin = True
                if target.member_prefix is None:
                    _require_manifest_identity(loaded_manifest, target.dataset_id, target.revision)
                else:
                    await self._validate_annual_component_binding(
                        target.artifact_id, target.dataset_id, target.revision,
                        target.member_prefix, loaded_manifest)
            async with self.pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "UPDATE public.archive_load SET staging_path=$2,last_accessed_at=now(),"
                    "expires_at=now()+interval '30 minutes' WHERE load_id=$1",
                    load_id, None if (path is None or persistent) else str(path))
            if input_pin and path is not None:
                async with self.pool.acquire() as conn:
                    await self.cache.release_input(conn, path, release_pin=True)
                input_pin = False
            await self.queue.checkpoint(claim, {"phase": "load", "load_id": str(load_id),
                                                "revision": target.revision,
                                                "source_kind": target.source_kind})
            if loaded_manifest is not None:
                self._load_manifests[load_id] = loaded_manifest
        except BaseException:
            try:
                async with self.pool.acquire() as conn:
                    await self.cache.release_load(conn, load_id)
                    if input_pin and path is not None:
                        await self.cache.release_input(conn, path, release_pin=True)
                        input_pin = False
            except Exception:
                logger.warning("load %s rollback failed", load_id)
            raise
        return LoadPin(load_id, dataset_id, target.revision, target.source_kind, target.artifact_id,
                       target.member_prefix, path, self.clock() + timedelta(seconds=IDLE_SECONDS),
                       loaded_manifest)

    @_translates
    async def load_pin(self, load_id: UUID) -> LoadPin:
        """Read a ready pin; acquire a brief reader lease while verifying its file."""
        async with self.pool.acquire() as conn:
            row, path = await self.cache.acquire_load(conn, load_id)
        try:
            manifest = await self._verified_manifest_for_load(row, path)
            return self._pin(row, path, manifest)
        finally:
            if path is not None:
                async with self.pool.acquire() as conn:
                    await self.cache.finish_load_read(conn, path)

    @_translates
    async def touch_load(self, load_id: UUID) -> LoadPin:
        """Refresh one load's idle expiry for metadata-only access."""
        async with self.pool.acquire() as conn:
            row, path = await self.cache.touch_load(conn, load_id)
        return self._pin(row, path, self._load_manifests.get(load_id))

    @asynccontextmanager
    async def acquire_load(self, load_id: UUID) -> AsyncIterator[LoadPin]:
        """Refresh a load and hold a reader pin until graph/file IO finishes."""
        path: Path | None = None
        try:
            async with self.pool.acquire() as conn:
                row, path = await self.cache.acquire_load(conn, load_id)
            manifest = await self._verified_manifest_for_load(row, path)
            pin = self._pin(row, path, manifest)
            yield pin
        except DavError as error:
            raise ArchiveError(error.code) from error
        finally:
            if path is not None:
                try:
                    async with self.pool.acquire() as conn:
                        await self.cache.finish_load_read(conn, path)
                except DavError as error:
                    raise ArchiveError(error.code) from error

    @_translates
    async def release_load(self, load_id: UUID) -> None:
        async with self.pool.acquire() as conn:
            await self.cache.release_load(conn, load_id)
        self._load_manifests.pop(load_id, None)

    @_translates
    async def expire_loads(self) -> int:
        candidates = tuple(self._load_manifests)
        async with self.pool.acquire() as conn:
            expired = await self.cache.expire_loads(conn)
            live = {row["load_id"] for row in await conn.fetch(
                "SELECT load_id FROM public.archive_load WHERE load_id=ANY($1::uuid[]) AND expires_at>now()",
                list(candidates),
            )} if candidates else set()
        for load_id in candidates:
            if load_id not in live:
                self._load_manifests.pop(load_id, None)
        return expired

    async def _verified_manifest_for_load(self, row: Mapping[str, Any], path: Path | None) -> ArchiveManifest | None:
        if row["source_kind"] != "zip":
            return None
        if path is None or row["artifact_id"] is None:
            raise ArchiveError("archive_load_required")
        cached = self._load_manifests.get(row["load_id"])
        if cached is not None:
            return cached
        artifact = await self.pool.fetchrow(
            "SELECT dataset_id,revision,sha256 FROM public.archive_artifact WHERE artifact_id=$1",
            row["artifact_id"])
        if artifact is None or not artifact["sha256"]:
            raise ArchiveError("archive_load_required")
        manifest = await self._verify_file(path, str(artifact["sha256"]))
        _require_manifest_identity(manifest, artifact["dataset_id"], int(artifact["revision"]))
        if row["member_prefix"] is None:
            _require_manifest_identity(manifest, row["dataset_id"], int(row["revision"]))
        else:
            await self._validate_annual_component_binding(
                row["artifact_id"], row["dataset_id"], int(row["revision"]),
                str(row["member_prefix"]), manifest)
        self._load_manifests[row["load_id"]] = manifest
        return manifest

    @staticmethod
    def _pin(row: Mapping[str, Any], path: Path | None,
             manifest: ArchiveManifest | None = None) -> LoadPin:
        return LoadPin(load_id=row["load_id"], dataset_id=row["dataset_id"],
                       revision=int(row["revision"]), source_kind=str(row["source_kind"]),
                       artifact_id=row["artifact_id"], member_prefix=row["member_prefix"],
                       local_path=path, expires_at=row["expires_at"], manifest=manifest)
