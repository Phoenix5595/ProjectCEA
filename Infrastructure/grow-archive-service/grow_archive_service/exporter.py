"""Verified ZIP export, remote verification, load pinning and annual containers.

This module owns the Files half of the archive lifecycle: it captures a fenced
source revision into a verified immutable Nextcloud artifact, re-verifies an
existing artifact by a full remote GET, materializes explicit Historical load
pins, merges four verified quarterly artifacts into one annual container and
removes only known superseded SJCEA-owned artifact paths after their verified
replacement exists. It publishes the verified manifest and dataset catalog
state after the artifact is durable.

Import is side-effect free: no environment, credential, network or Arrow access
happens until a method runs against a live pool and the configured encrypted
connection.
"""
from __future__ import annotations

import asyncio
import functools
import json
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import asyncpg
import httpx

from shared.infra_logging import get_logger
from shared.nextcloud_credentials import decrypt_secret
from shared.nextcloud_dav import DavError, discover_nextcloud

from .annual import (
    AnnualComponentInput, AnnualIntegrityError, build_annual_zip,
    manifest_sha256,
)
from .cache import ArchiveCache, CachedArtifact, IDLE_SECONDS
from .capture import CaptureResult, PriorArchive, SourceCapture
from .jobs import ArchiveJobQueue
from .manifest import ArchiveManifest
from .models import ArchiveError, JobClaim
from .parquet_codec import ArchiveIntegrityError, iter_verify_archive, iter_write_archive
from .webdav import ArtifactName, WebDAVArtifactClient

logger = get_logger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 15.0
REMOTE_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
UPLOAD_NAMESPACE = "sj-cea-upload"


def _translates(function: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Map DAV fixed codes onto archive errors at the service boundary."""

    @functools.wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await function(*args, **kwargs)
        except DavError as error:
            code = error.code if error.code.startswith("archive_") else "archive_remote_unavailable"
            unavailable = code in {
                "archive_remote_unavailable", "archive_remote_quota", "archive_local_space_insufficient",
            }
            raise ArchiveError(code, status_code=503 if unavailable else 409) from error

    return wrapper


def _tree_bytes(root: Path) -> int:
    total = 0
    for path in Path(root).rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def _annual_component(manifest: Mapping[str, Any], dataset_id: UUID) -> Mapping[str, Any] | None:
    components = manifest.get("components")
    if not isinstance(components, list):
        return None
    for entry in components:
        if isinstance(entry, Mapping) and str(entry.get("dataset_id")) == str(dataset_id):
            return entry
    return None


def _require_manifest_identity(manifest: ArchiveManifest, dataset_id: UUID, revision: int) -> None:
    if manifest.dataset_id != str(dataset_id) or manifest.revision != revision:
        raise ArchiveError("archive_integrity_failed")


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
class ExportRequest:
    revision: int
    artifact_id: UUID | None
    job_id: UUID | None


@dataclass(frozen=True, slots=True)
class LoadRequest:
    revision: int
    load: LoadPin | None
    job_id: UUID | None


class ExportService:
    """Files exporter, verifier, load pinner and annual-container builder."""

    def __init__(self, pool: asyncpg.Pool, archive_root: Path, heavy_work: Any,
                 capture: SourceCapture, *, clock: Callable[[], datetime] | None = None,
                 dav_factory: Callable[[UUID | None], Any] | None = None,
                 cache: ArchiveCache | None = None) -> None:
        self.pool = pool
        self.root = Path(archive_root)
        self.heavy = heavy_work
        self.capture = capture
        self.clock = clock or (lambda: datetime.now(UTC))
        self.queue = ArchiveJobQueue(pool)
        self.cache = cache or ArchiveCache(self.root / "cache", self.root / "staging")
        self._dav_factory = dav_factory
        self._load_manifests: dict[UUID, ArchiveManifest] = {}

    # ------------------------------------------------------------------
    # DAV binding
    # ------------------------------------------------------------------

    def dav(self, instance_id: UUID | None = None) -> Any:
        """Async context manager yielding a client bound to one instance."""
        if self._dav_factory is not None:
            return self._dav_factory(instance_id)
        return self._connect(instance_id)

    @asynccontextmanager
    async def _connect(self, instance_id: UUID | None = None) -> AsyncIterator[WebDAVArtifactClient]:
        row = await self.pool.fetchrow(
            "SELECT id,instance_id,files_root_url,caldav_base_url,account_email,credentials_encrypted "
            "FROM public.calendar_sync_connection WHERE enabled AND files_root_url IS NOT NULL "
            "ORDER BY id DESC LIMIT 1")
        if row is None:
            raise ArchiveError("archive_remote_unavailable", status_code=503)
        if instance_id is not None and row["instance_id"] != instance_id:
            # Credentials for a previous Nextcloud instance are unavailable:
            # never re-upload to or purge from its replacement blindly.
            raise ArchiveError("archive_remote_unavailable", status_code=503)
        try:
            password = decrypt_secret(bytes(row["credentials_encrypted"]))
        except (ValueError, RuntimeError) as error:
            raise ArchiveError("archive_credentials_unavailable", status_code=503) from error
        base = str(row["caldav_base_url"] or row["files_root_url"])
        async with httpx.AsyncClient(auth=httpx.BasicAuth(str(row["account_email"]), password),
                                     trust_env=False, follow_redirects=False,
                                     timeout=REMOTE_TIMEOUT) as client:
            async with asyncio.timeout(DISCOVERY_TIMEOUT_SECONDS):
                discovery = await discover_nextcloud(client, base)
            if discovery.instance_id != row["instance_id"]:
                raise ArchiveError("archive_remote_unavailable", status_code=503)
            if row["files_root_url"] and discovery.files_root_url.rstrip("/") != str(row["files_root_url"]).rstrip("/"):
                raise ArchiveError("archive_remote_unavailable", status_code=503)
            yield WebDAVArtifactClient(discovery, client, instance_id=row["instance_id"],
                                       scratch_root=self.root / "staging" / "downloads")

    # ------------------------------------------------------------------
    # Heavy-work stepping
    # ------------------------------------------------------------------

    async def _drain(self, factory: Callable[[Callable[[], None]], Any], *, claim: JobClaim | None = None,
                     phase: str | None = None, guard: Callable[[], None] | None = None,
                     checkpoint_every: int = 64) -> Any:
        """Drive one bounded-step codec iterator through the heavy-work lane."""
        holder: dict[str, Any] = {}

        def check() -> None:
            control = holder.get("control")
            if control is not None:
                control.checkpoint()

        iterator = factory(check)
        result: Any = None
        steps = 0
        while True:
            def step(control: Any) -> Any:
                holder["control"] = control
                return next(iterator, None)

            value = await self.heavy.run(step)
            if value is None:
                return result
            result = value
            steps += 1
            if guard is not None:
                guard()
            if claim is not None and phase is not None and steps % checkpoint_every == 0:
                await self.queue.checkpoint(claim, {"phase": phase, "steps": steps})

    async def _verify_file(self, path: Path, expected_sha256: str | None) -> ArchiveManifest:
        try:
            result = await self._drain(
                lambda check: iter_verify_archive(path, expected_sha256, checkpoint=check))
        except ArchiveIntegrityError as error:
            raise ArchiveError("archive_integrity_failed") from error
        if not result or not result.get("complete"):
            raise ArchiveError("archive_integrity_failed")
        return ArchiveManifest(result["manifest"], result["sha256"], result["byte_size"])

    async def _write_archive(self, claim: JobClaim, result: CaptureResult, destination: Path) -> None:
        try:
            await self._drain(
                lambda check: iter_write_archive(result.dataset_id, result.revision, destination,
                                                 checkpoint=check),
                claim=claim, phase="encode",
                guard=lambda: self._require_space(destination.parent))
        except ArchiveIntegrityError as error:
            raise ArchiveError("archive_integrity_failed") from error

    def _require_space(self, root: Path) -> None:
        try:
            self.cache.require_space(root)
        except DavError as error:
            raise ArchiveError(error.code) from error

    # ------------------------------------------------------------------
    # Export job executor
    # ------------------------------------------------------------------

    @_translates
    async def export_dataset(self, claim: JobClaim) -> UUID:
        """Capture a fenced source revision, upload it and publish its manifest."""
        dataset_id = claim.fence.dataset_id
        revision = claim.fence.data_revision
        async with self.pool.acquire() as conn:
            dataset = await conn.fetchrow(
                "SELECT d.*,g.state AS lifecycle_state FROM archive_dataset d "
                "LEFT JOIN grow_lifecycle g USING(grow_plan_id) WHERE dataset_id=$1", dataset_id)
            if dataset is None:
                raise ArchiveError("archive_not_found", status_code=404)
            await self.queue.check_fence(conn, claim)
            if int(dataset["data_revision"]) != revision:
                raise ArchiveError("archive_job_superseded")
            existing = await conn.fetchrow(
                "SELECT artifact_id FROM public.archive_artifact "
                "WHERE dataset_id=$1 AND revision=$2 AND state='verified'", dataset_id, revision)
            existing_artifact_id = existing["artifact_id"] if existing is not None else None
            removed = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.archive_availability "
                "WHERE dataset_id=$1 AND source_rows_removed)", dataset_id)
        directory = self.root / "staging" / str(claim.job.job_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if existing_artifact_id is not None:
            manifest = await self.reverify_artifact(existing_artifact_id)
            await self._publish_catalog(claim, dataset_id, revision, manifest)
            await self._drop_staging(claim)
            return existing_artifact_id

        prior: PriorArchive | None = None
        try:
            if removed:
                prior = await self.prior_archive_path(dataset)
            result = await self.capture.capture(claim, directory, prior=prior)
            destination = directory / "archive.zip"
            raw_bytes = await asyncio.to_thread(_tree_bytes, result.capture_dir)
            await self.cache.preflight(directory, raw_bytes=0, zip_bytes=raw_bytes)
            await self._write_archive(claim, result, destination)
            manifest = await self._verify_file(destination, None)
            _require_manifest_identity(manifest, dataset_id, revision)
            sha256 = manifest.sha256
            byte_size = manifest.byte_size
            name = self._artifact_name(dataset, revision, sha256)
            upload_id, resume_from = self._resume_state(claim, sha256=sha256, byte_size=byte_size)

            def checkpoint(payload: dict[str, Any]) -> Awaitable[None]:
                return self.queue.checkpoint(claim, {**payload, "sha256": sha256})

            async with self.dav() as dav:
                instance_id = dav.instance_id
                remote_path, remote_manifest = await dav.upload(
                    destination, name, upload_id=upload_id, verifier=self._verify_file,
                    checkpoint=checkpoint, resume_from=resume_from)
            artifact_id = await self._publish_artifact(
                claim, dataset_id, revision, instance_id=instance_id, remote_path=remote_path,
                sha256=sha256, byte_size=byte_size, manifest=remote_manifest)
            await self.cache.adopt_local_copy(self.pool, artifact_id=artifact_id, source=destination,
                                              sha256=sha256, byte_size=byte_size)
            await self._publish_catalog(claim, dataset_id, revision, remote_manifest)
            await self._drop_staging(claim)
            return artifact_id
        finally:
            if prior is not None:
                await self.release_prior_archive(prior.path)

    async def _publish_catalog(
        self, claim: JobClaim, dataset_id: UUID, revision: int, manifest: ArchiveManifest,
    ) -> None:
        observation_members = [
            entry for entry in manifest.data.get("members", ())
            if isinstance(entry, Mapping) and entry.get("record_role") == "observation"
        ]
        state = "ready"
        if observation_members:
            async with self.pool.acquire() as conn:
                for entry in observation_members:
                    source_table = entry.get("source_table")
                    start_raw, end_raw = entry.get("window_start"), entry.get("window_end")
                    if not isinstance(source_table, str) or start_raw is None or end_raw is None:
                        break
                    try:
                        start = (start_raw if isinstance(start_raw, datetime) else
                                 datetime.fromisoformat(str(start_raw).replace("Z", "+00:00")))
                        end = (end_raw if isinstance(end_raw, datetime) else
                               datetime.fromisoformat(str(end_raw).replace("Z", "+00:00")))
                    except (TypeError, ValueError):
                        break
                    if (start.tzinfo is None or start.utcoffset() is None
                            or end.tzinfo is None or end.utcoffset() is None or start >= end):
                        break
                    removed = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM public.archive_availability "
                        "WHERE dataset_id=$1 AND source_table=$2 AND source_rows_removed "
                        "AND start_at<=$3 AND end_at>=$4)",
                        dataset_id, source_table, start, end)
                    if not removed:
                        break
                else:
                    state = "zip_only"
        async with self.pool.acquire() as conn, conn.transaction():
            await self.queue.check_fence(conn, claim, lock=True)
            updated = await conn.execute(
                "UPDATE public.archive_dataset SET published_revision=$2,manifest=$3::jsonb,"
                "state=$4,last_error_code=NULL,updated_at=clock_timestamp() "
                "WHERE dataset_id=$1 AND data_revision=$2",
                dataset_id, revision, json.dumps(dict(manifest.data)), state)
            if updated != "UPDATE 1":
                raise ArchiveError("archive_job_superseded")
        await self.queue.checkpoint(claim, {"verified_manifest": manifest.data})

    def _artifact_name(self, dataset: Mapping[str, Any], revision: int, sha256: str) -> ArtifactName:
        try:
            return ArtifactName(dataset_id=dataset["dataset_id"], revision=revision, sha256=sha256,
                                kind=str(dataset["kind"]), year=dataset["calendar_year"],
                                quarter=dataset["quarter"])
        except ValueError as error:
            raise ArchiveError("archive_integrity_failed") from error

    def _resume_state(self, claim: JobClaim, *, sha256: str, byte_size: int) -> tuple[UUID, int]:
        stable = uuid5(NAMESPACE_URL, f"{UPLOAD_NAMESPACE}:{claim.job.job_id}")
        progress = claim.job.checkpoint.get("progress")
        progress = dict(progress) if isinstance(progress, Mapping) else {}
        raw = progress.get("upload_id")
        try:
            upload_id = UUID(str(raw)) if raw else stable
        except (TypeError, ValueError):
            upload_id = stable
        if str(progress.get("sha256") or "") == sha256 and int(progress.get("size") or -1) == byte_size:
            try:
                return upload_id, max(0, int(progress.get("next_chunk") or 0))
            except (TypeError, ValueError):
                return stable, 0
        return stable, 0

    async def _publish_artifact(self, claim: JobClaim, dataset_id: UUID, revision: int, *,
                                instance_id: UUID, remote_path: str, sha256: str, byte_size: int,
                                manifest: ArchiveManifest) -> UUID:
        _require_manifest_identity(manifest, dataset_id, revision)
        _require_manifest_identity(manifest, dataset_id, revision)
        manifest_hash = manifest_sha256(manifest.data)
        async with self.pool.acquire() as conn, conn.transaction():
            await self.queue.check_fence(conn, claim, lock=True)
            row = await conn.fetchrow(
                "SELECT artifact_id,state,sha256,byte_size FROM public.archive_artifact "
                "WHERE dataset_id=$1 AND revision=$2 FOR UPDATE", dataset_id, revision)
            if row is None:
                created = await conn.fetchrow(
                    "INSERT INTO public.archive_artifact(artifact_id,dataset_id,revision,state,"
                    "remote_instance_id,remote_path,sha256,byte_size,verified_at,manifest_sha256) "
                    "VALUES($1,$2,$3,'verified',$4,$5,$6,$7,now(),$8) RETURNING artifact_id",
                    uuid4(), dataset_id, revision, instance_id, remote_path, sha256, byte_size, manifest_hash)
                return created["artifact_id"]
            if row["state"] == "verified" and (row["sha256"] != sha256 or int(row["byte_size"]) != byte_size):
                # The same content revision produced different bytes.
                raise ArchiveError("archive_integrity_failed")
            if row["state"] == "superseded":
                raise ArchiveError("archive_job_superseded")
            await conn.execute(
                "UPDATE public.archive_artifact SET state='verified',remote_instance_id=$3,remote_path=$4,"
                "sha256=$5,byte_size=$6,verified_at=now(),manifest_sha256=$7,updated_at=now() "
                "WHERE artifact_id=$1 AND dataset_id=$2 AND revision=$8",
                row["artifact_id"], dataset_id, instance_id, remote_path, sha256, byte_size,
                manifest_hash, revision)
            return row["artifact_id"]


    async def _drop_staging(self, claim: JobClaim) -> None:
        await self.heavy.wait_idle()
        directory = self.root / "staging" / str(claim.job.job_id)
        try:
            await asyncio.to_thread(shutil.rmtree, directory)
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------
    # Remote verification and local materialization
    # ------------------------------------------------------------------

    @_translates
    async def reverify_artifact(self, artifact_id: UUID) -> ArchiveManifest:
        """Full remote GET verification; never trusts the catalog flag alone."""
        row = await self.pool.fetchrow(
            "SELECT * FROM public.archive_artifact WHERE artifact_id=$1", artifact_id)
        if row is None:
            raise ArchiveError("archive_not_found", status_code=404)
        if (row["state"] not in ("verified", "superseded") or not row["remote_path"]
                or row["remote_instance_id"] is None or not row["sha256"] or row["byte_size"] is None):
            raise ArchiveError("archive_remote_unavailable", status_code=503)
        async def verify_bound(path: Path, sha256: str) -> ArchiveManifest:
            manifest = await self._verify_file(path, sha256)
            _require_manifest_identity(manifest, row["dataset_id"], int(row["revision"]))
            return manifest

        async with self.dav(instance_id=row["remote_instance_id"]) as dav:
            path, manifest = await dav.verify_remote(
                dav.remote_url(str(row["remote_path"])), str(row["sha256"]),
                verifier=verify_bound, expected_size=int(row["byte_size"]))
        adopted = await self.cache.adopt_local_copy(
            self.pool, artifact_id=artifact_id, source=path, sha256=str(row["sha256"]),
            byte_size=int(row["byte_size"]))
        if adopted != path or not self.cache.owns_cache_path(adopted):
            await asyncio.to_thread(path.unlink, missing_ok=True)
        if row["state"] == "verified":
            await self.pool.execute(
                "UPDATE public.archive_artifact SET verified_at=now(),last_accessed_at=now() "
                "WHERE artifact_id=$1", artifact_id)
        return manifest


    async def _materialize_artifact(self, artifact_id: UUID, *,
                                    pin_input: bool = False) -> tuple[CachedArtifact, ArchiveManifest]:
        row = await self.pool.fetchrow(
            "SELECT * FROM public.archive_artifact WHERE artifact_id=$1", artifact_id)
        if (row is None or row["state"] not in ("verified", "superseded")
                or not row["sha256"] or row["byte_size"] is None):
            raise ArchiveError("archive_export_required")
        async def verify_bound(path: Path, sha256: str) -> ArchiveManifest:
            manifest = await self._verify_file(path, sha256)
            _require_manifest_identity(manifest, row["dataset_id"], int(row["revision"]))
            return manifest
        async def downloader(path: Path) -> tuple[str, int]:
            if not row["remote_path"] or row["remote_instance_id"] is None:
                raise ArchiveError("archive_remote_unavailable", status_code=503)
            async with self.dav(instance_id=row["remote_instance_id"]) as dav:
                _path, digest, size = await dav.download(
                    dav.remote_url(str(row["remote_path"])), path, expected_size=int(row["byte_size"]),
                )
                return digest, size

        return await self.cache.materialize(
            self.pool, artifact_id=artifact_id, sha256=str(row["sha256"]),
            byte_size=int(row["byte_size"]), downloader=downloader, verifier=verify_bound,
            pin_input=pin_input,
        )


    @_translates
    async def prior_archive_path(self, dataset: Mapping[str, Any]) -> PriorArchive:
        """Return a verified previous ZIP input for a time-window rebuild."""
        revision = dataset["published_revision"]
        if revision is None:
            raise ArchiveError("archive_export_required")
        target = await self.resolve_load_target(dataset["dataset_id"], int(revision))
        if target.source_kind != "zip" or target.artifact_id is None:
            raise ArchiveError("archive_export_required")
        artifact, manifest = await self._materialize_artifact(target.artifact_id, pin_input=True)
        component_dataset_id = dataset["dataset_id"] if target.member_prefix is not None else None
        return PriorArchive(artifact.path, manifest, component_dataset_id)

    @_translates
    async def release_prior_archive(self, path: Path) -> None:
        """Release the verified prior ZIP input; persistent cache paths remain cached."""
        if not (self.cache.owns_staging_path(path) or self.cache.owns_cache_path(path)):
            raise ArchiveError("archive_integrity_failed")
        async with self.pool.acquire() as conn:
            await self.cache.release_input(conn, path, release_pin=True)

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

    # ------------------------------------------------------------------
    # Export requests (routes) and annual merge
    # ------------------------------------------------------------------

    @_translates
    async def request_export(self, dataset_id: UUID, expected_revision: int) -> ExportRequest:
        """Existing verified artifact (200) or a durable export job (202)."""
        async with self.pool.acquire() as conn:
            dataset = await conn.fetchrow(
                "SELECT d.*,g.state AS lifecycle_state FROM archive_dataset d "
                "LEFT JOIN grow_lifecycle g USING(grow_plan_id) WHERE dataset_id=$1", dataset_id)
            if dataset is None:
                raise ArchiveError("archive_not_found", status_code=404)
            if int(dataset["data_revision"]) != expected_revision:
                raise ArchiveError("archive_revision_conflict")
            if dataset["kind"] == "grow" and dataset["lifecycle_state"] != "closed":
                raise ArchiveError("grow_not_closed")
            existing = await conn.fetchrow(
                "SELECT artifact_id FROM public.archive_artifact "
                "WHERE dataset_id=$1 AND revision=$2 AND state='verified'", dataset_id, expected_revision)
            existing_artifact_id = existing["artifact_id"] if existing is not None else None
        if existing_artifact_id is not None:
            # Reuse only after revalidating the actual current remote bytes.
            await self.reverify_artifact(existing_artifact_id)
            return ExportRequest(expected_revision, existing_artifact_id, None)
        job = await self.queue.enqueue(dataset_id, "export", expected_revision)
        return ExportRequest(expected_revision, None, job.job_id)

    @_translates
    async def merge_annual(self, claim: JobClaim) -> UUID:
        """Combine four verified quarters (prefixes reused) into one annual ZIP."""
        dataset_id = claim.fence.dataset_id
        revision = claim.fence.data_revision
        async with self.pool.acquire() as conn:
            annual = await conn.fetchrow("SELECT * FROM public.archive_dataset WHERE dataset_id=$1", dataset_id)
            if annual is None or annual["kind"] != "veg_year" or annual["calendar_year"] is None:
                raise ArchiveError("archive_not_found", status_code=404)
            await self.queue.check_fence(conn, claim)
            if int(annual["data_revision"]) != revision:
                raise ArchiveError("archive_job_superseded")
            year = int(annual["calendar_year"])
            children = await conn.fetch(
                "SELECT * FROM public.archive_dataset WHERE kind='veg_quarter' AND calendar_year=$1 "
                "ORDER BY quarter", year)
            if len(children) != 4:
                raise ArchiveError("archive_revision_pending")
            previous = await conn.fetchrow(
                "SELECT artifact_id FROM public.archive_artifact WHERE dataset_id=$1 AND state='verified' "
                "AND revision<$2 ORDER BY revision DESC LIMIT 1", dataset_id, revision)
            plan: list[tuple[Mapping[str, Any], str, UUID, str | None]] = []
            for child in children:
                published = child["published_revision"]
                if published is None or int(published) != int(child["data_revision"]):
                    raise ArchiveError("archive_revision_pending")
                own = await conn.fetchrow(
                    "SELECT artifact_id FROM public.archive_artifact WHERE dataset_id=$1 AND revision=$2 "
                    "AND state='verified'", child["dataset_id"], int(published))
                if own is not None:
                    plan.append((child, "zip", own["artifact_id"], None))
                elif previous is not None:
                    component = await conn.fetchrow(
                        "SELECT child_revision,member_prefix FROM public.archive_component "
                        "WHERE parent_artifact_id=$1 AND child_dataset_id=$2",
                        previous["artifact_id"], child["dataset_id"])
                    if component is None or int(component["child_revision"]) != int(published):
                        raise ArchiveError("archive_revision_pending")
                    plan.append((child, "annual", previous["artifact_id"], str(component["member_prefix"])))
                else:
                    raise ArchiveError("archive_revision_pending")
            previous_id = previous["artifact_id"] if previous is not None else None
        input_paths: list[Path] = []
        try:
            local: dict[UUID, tuple[Path, ArchiveManifest]] = {}
            for _child, _kind, artifact_id, _prefix in plan:
                if artifact_id not in local:
                    artifact, component_manifest = await self._materialize_artifact(
                        artifact_id, pin_input=True)
                    local[artifact_id] = (artifact.path, component_manifest)
                    input_paths.append(artifact.path)
            components: list[AnnualComponentInput] = []
            for child, kind, artifact_id, prefix in plan:
                path, manifest = local[artifact_id]
                member_prefix = f"Q{int(child['quarter'])}/"
                if kind == "zip":
                    components.append(AnnualComponentInput(
                        dataset_id=child["dataset_id"], revision=int(child["published_revision"]),
                        member_prefix=member_prefix, source=path, source_prefix="",
                        sha256=manifest.sha256, byte_size=manifest.byte_size, manifest=manifest.data))
                else:
                    entry = _annual_component(manifest.data, child["dataset_id"])
                    if entry is None or str(entry.get("member_prefix")) != str(prefix):
                        raise ArchiveError("archive_revision_pending")
                    components.append(AnnualComponentInput(
                        dataset_id=child["dataset_id"], revision=int(entry["revision"]),
                        member_prefix=member_prefix, source=path, source_prefix=str(prefix),
                        sha256=str(entry["sha256"]), byte_size=int(entry["byte_size"]),
                        manifest=entry["manifest"]))
            directory = self.root / "staging" / str(claim.job.job_id)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination = directory / f"{year}-annual.zip"
            estimate = sum(int(component.byte_size) for component in components)
            await self.cache.preflight(directory, raw_bytes=0, zip_bytes=estimate)
            await self._build_annual(claim, annual, revision, components, destination)
            manifest = await self._verify_file(destination, None)
            sha256 = manifest.sha256
            byte_size = manifest.byte_size
            name = self._artifact_name(annual, revision, sha256)
            upload_id, resume_from = self._resume_state(claim, sha256=sha256, byte_size=byte_size)

            def checkpoint(payload: dict[str, Any]) -> Awaitable[None]:
                return self.queue.checkpoint(claim, {**payload, "sha256": sha256})

            async with self.dav() as dav:
                instance_id = dav.instance_id
                remote_path, remote_manifest = await dav.upload(
                    destination, name, upload_id=upload_id, verifier=self._verify_file,
                    checkpoint=checkpoint, resume_from=resume_from)
            artifact_id = await self._publish_annual(
                claim, dataset_id, revision, instance_id=instance_id, remote_path=remote_path,
                sha256=sha256, byte_size=byte_size, manifest=remote_manifest, components=components,
                previous=previous_id)
            await self.cache.adopt_local_copy(self.pool, artifact_id=artifact_id, source=destination,
                                              sha256=sha256, byte_size=byte_size)
            try:
                await self._delete_superseded(dataset_id)
            except ArchiveError as error:
                logger.warning("annual %s cleanup deferred code %s", artifact_id, error.code)
                await self.queue.enqueue(dataset_id, "artifact_cleanup", revision)
            await self._drop_staging(claim)
            return artifact_id

        finally:
            for source_path in input_paths:
                await self.release_prior_archive(source_path)

    async def _build_annual(self, claim: JobClaim, annual: Mapping[str, Any], revision: int,
                            components: Sequence[AnnualComponentInput],
                            destination: Path) -> Mapping[str, Any]:
        def build(control: Any) -> Mapping[str, Any]:
            def check() -> None:
                control.checkpoint()
                self.cache.require_space(destination.parent)

            return build_annual_zip(
                annual["dataset_id"], revision, calendar_revision=int(annual["calendar_revision"]),
                calendar_year=int(annual["calendar_year"]), room=str(annual["room"]),
                components=components, destination=destination, checkpoint=check)

        try:
            manifest = await self.heavy.run(build)
        except (AnnualIntegrityError, ArchiveIntegrityError) as error:
            raise ArchiveError("archive_integrity_failed") from error
        await self.queue.checkpoint(claim, {"phase": "annual", "revision": revision})
        return manifest

    async def _publish_annual(self, claim: JobClaim, dataset_id: UUID, revision: int, *,
                              instance_id: UUID, remote_path: str, sha256: str, byte_size: int,
                              manifest: ArchiveManifest, components: Sequence[AnnualComponentInput],
                              previous: UUID | None) -> UUID:
        _require_manifest_identity(manifest, dataset_id, revision)
        manifest_hash = manifest_sha256(manifest.data)
        async with self.pool.acquire() as conn, conn.transaction():
            await self.queue.check_fence(conn, claim, lock=True)
            row = await conn.fetchrow(
                "SELECT artifact_id,state,sha256,byte_size FROM public.archive_artifact "
                "WHERE dataset_id=$1 AND revision=$2 FOR UPDATE", dataset_id, revision)
            if row is None:
                artifact_id = uuid4()
                await conn.execute(
                    "INSERT INTO public.archive_artifact(artifact_id,dataset_id,revision,state,"
                    "remote_instance_id,remote_path,sha256,byte_size,verified_at,manifest_sha256) "
                    "VALUES($1,$2,$3,'verified',$4,$5,$6,$7,now(),$8)",
                    artifact_id, dataset_id, revision, instance_id, remote_path, sha256, byte_size,
                    manifest_hash)
            else:
                if row["state"] == "verified" and (row["sha256"] != sha256 or int(row["byte_size"]) != byte_size):
                    raise ArchiveError("archive_integrity_failed")
                if row["state"] == "superseded":
                    raise ArchiveError("archive_job_superseded")
                artifact_id = row["artifact_id"]
                await conn.execute(
                    "UPDATE public.archive_artifact SET state='verified',remote_instance_id=$3,"
                    "remote_path=$4,sha256=$5,byte_size=$6,verified_at=now(),manifest_sha256=$7,"
                    "updated_at=now() WHERE artifact_id=$1 AND dataset_id=$2 AND revision=$8",
                    artifact_id, dataset_id, instance_id, remote_path, sha256, byte_size,
                    manifest_hash, revision)
            for component in components:
                await conn.execute(
                    "INSERT INTO public.archive_component(parent_artifact_id,child_dataset_id,child_revision,"
                    "member_prefix,manifest_sha256,observation_counts) VALUES($1,$2,$3,$4,$5,$6::jsonb) "
                    "ON CONFLICT(parent_artifact_id,child_dataset_id) DO UPDATE SET "
                    "child_revision=EXCLUDED.child_revision,member_prefix=EXCLUDED.member_prefix,"
                    "manifest_sha256=EXCLUDED.manifest_sha256,observation_counts=EXCLUDED.observation_counts",
                    artifact_id, component.dataset_id, component.revision, component.member_prefix,
                    manifest_sha256(component.manifest),
                    json.dumps(component.manifest.get("observation_counts") or {}))
            for component in components:
                updated = await conn.execute(
                    "UPDATE public.archive_dataset SET published_revision=$2,updated_at=clock_timestamp() "
                    "WHERE dataset_id=$1 AND data_revision=$2 AND published_revision=$2",
                    component.dataset_id, component.revision)
                if updated != "UPDATE 1":
                    raise ArchiveError("archive_job_superseded")
                await conn.execute(
                    "UPDATE public.archive_artifact SET state='superseded',updated_at=clock_timestamp() "
                    "WHERE dataset_id=$1 AND revision=$2 AND state='verified' AND artifact_id<>$3",
                    component.dataset_id, component.revision, artifact_id)
            if previous is not None:
                await conn.execute(
                    "UPDATE public.archive_artifact SET state='superseded',updated_at=clock_timestamp() "
                    "WHERE artifact_id=$1 AND state='verified'", previous)
            await conn.execute(
                "UPDATE public.archive_dataset SET published_revision=$2,state='zip_only',manifest=$3::jsonb,"
                "last_error_code=NULL,updated_at=clock_timestamp() WHERE dataset_id=$1",
                dataset_id, revision, json.dumps(manifest.data))
        return artifact_id

    # ------------------------------------------------------------------
    # Superseded artifact cleanup
    # ------------------------------------------------------------------

    @_translates
    async def cleanup_artifacts(self, claim: JobClaim) -> int:
        async with self.pool.acquire() as conn:
            await self.queue.check_fence(conn, claim)
        return await self._delete_superseded(claim.fence.dataset_id)

    async def _delete_superseded(self, dataset_id: UUID) -> int:
        """Delete only known superseded SJCEA-owned paths with verified replacements."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT a.artifact_id,a.remote_path,a.remote_instance_id,a.local_path "
                "FROM public.archive_artifact a WHERE a.state='superseded' "
                "AND a.remote_path IS NOT NULL AND a.remote_instance_id IS NOT NULL AND ("
                "  (a.dataset_id=$1 AND EXISTS(SELECT 1 FROM public.archive_artifact r "
                "     WHERE r.dataset_id=$1 AND r.revision>a.revision AND r.state='verified')) "
                "  OR EXISTS(SELECT 1 FROM public.archive_component c "
                "     JOIN public.archive_artifact p ON p.artifact_id=c.parent_artifact_id "
                "     WHERE c.child_dataset_id=a.dataset_id AND c.child_revision=a.revision "
                "     AND p.dataset_id=$1 AND p.state='verified')) "
                "ORDER BY a.revision", dataset_id)
        removed = 0
        failures: list[str] = []
        deferred_for_pins = False
        for row in rows:
            async with self.pool.acquire() as conn:
                pinned = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM public.archive_load WHERE artifact_id=$1 "
                    "AND expires_at>now())", row["artifact_id"])
            active_reader = bool(row["local_path"]) and await self.cache.has_active_reader(Path(str(row["local_path"])))
            if pinned or active_reader:
                deferred_for_pins = True
                continue
            try:
                async with self.dav(instance_id=row["remote_instance_id"]) as dav:
                    await dav.delete(str(row["remote_path"]))
            except ArchiveError as error:
                failures.append(error.code)
                continue
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE public.archive_artifact SET remote_path=NULL,updated_at=now() "
                    "WHERE artifact_id=$1 AND state='superseded'", row["artifact_id"])
                if row["local_path"]:
                    await self.cache.drop_local_copy(conn, str(row["local_path"]))
            removed += 1
        if failures:
            raise ArchiveError(failures[0])
        if deferred_for_pins:
            raise ArchiveError("archive_artifact_pinned")
        return removed
