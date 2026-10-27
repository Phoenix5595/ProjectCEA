"""Annual containers for ExportService: merge four verified quarters, publish, and clean superseded artifacts."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from shared.infra_logging import get_logger

from .annual import (
    AnnualComponentInput, AnnualIntegrityError, build_annual_zip,
    manifest_sha256,
)
from .exporter_support import _annual_component, _require_manifest_identity, _translates
from .manifest import ArchiveManifest
from .models import ArchiveError, JobClaim
from .parquet_codec import ArchiveIntegrityError

logger = get_logger("grow_archive_service.exporter")


class AnnualMixin:
    # ------------------------------------------------------------------
    # Annual merge
    # ------------------------------------------------------------------

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
