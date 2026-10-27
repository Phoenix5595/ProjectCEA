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
import json
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import asyncpg
import httpx

from shared.nextcloud_credentials import decrypt_secret
from shared.nextcloud_dav import DavError, discover_nextcloud

from .annual import manifest_sha256
from .cache import ArchiveCache, CachedArtifact
from .capture import CaptureResult, PriorArchive, SourceCapture
from .exporter_annual import AnnualMixin as _AnnualMixin
from .exporter_loads import LoadMixin as _LoadMixin
from .exporter_support import _require_manifest_identity, _translates
from .jobs import ArchiveJobQueue
from .manifest import ArchiveManifest
from .models import ArchiveError, JobClaim
from .parquet_codec import ArchiveIntegrityError, iter_verify_archive, iter_write_archive
from .webdav import ArtifactName, WebDAVArtifactClient


DISCOVERY_TIMEOUT_SECONDS = 15.0
REMOTE_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
UPLOAD_NAMESPACE = "sj-cea-upload"


def _tree_bytes(root: Path) -> int:
    total = 0
    for path in Path(root).rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


@dataclass(frozen=True, slots=True)
class ExportRequest:
    revision: int
    artifact_id: UUID | None
    job_id: UUID | None


class ExportService(_LoadMixin, _AnnualMixin):
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
    # Export requests (routes)
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
