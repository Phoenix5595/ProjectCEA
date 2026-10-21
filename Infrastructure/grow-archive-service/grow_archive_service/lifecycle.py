"""Grow, Veg and nongrow lifecycle planning plus fenced source deletion.

Closed Flower grows are exported as lossless ZIPs and retained until their
anniversary; Veg quarters receive boundary ZIP exports and progressive
twelve-month source expiry. Annual ZIPs compose verified quarters, while
nongrow expiry uses a forward cursor and keeps ambiguous or protected rows.

Source deletion is delegated to SourceDeletion, which verifies the current
source bytes against the published ZIP within the deleting transaction. This
module performs no lifecycle planning unless
``ARCHIVE_LIFECYCLE_ENABLED=true``.

The nongrow expiry cursor (``public.archive_expiry_cursor``) is a forward
frontier per (source table, room): every eligible observation with
``time < processed_before`` was deleted, and rows held at decision time
(protected grow windows, captured availability, unresolved identity) are not
revisited until the cursor is deliberately reset through
:meth:`LifecycleService.reset_expiry_cursor`. A missing cursor table holds
expiry with the fixed ``archive_expiry_cursor_missing`` code; the forward
migration is ``Infrastructure/database/grow_data_lifecycle_expiry_cursor.sql``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from uuid import UUID

import asyncpg

from shared.grow_lifecycle import (
    add_calendar_months as _add_calendar_months,
    retention_cutoffs as _retention_cutoffs,
    toronto_midnight as _toronto_midnight,
)
from shared.infra_logging import get_logger

from .deletion import SourceDeletion
from .lifecycle_calendar import quarter_bounds as _quarter_bounds
from .lifecycle_expiry import ExpiryMixin as _ExpiryMixin
from .lifecycle_planning import PlanningMixin as _PlanningMixin
from .lifecycle_types import (
    DerivedHistoryMaintenance as _DerivedHistoryMaintenance,
    ExportExecutor as _ExportExecutor,
    FreshnessGate as _FreshnessGate,
    HeldScope as _HeldScope,
    LifecycleTickResult as _LifecycleTickResult,
    PlannedJob as _PlannedJob,
)
from .manifest import ArchiveManifest
from .models import ArchiveError, ArchiveJob, JobClaim, JobKind
from .policy_gate import (
    SourceAgePolicyBlocked,
    SourceAgePolicyInspectionFailed,
    assert_source_age_policies_clear,
)
from .schema import (
    SchemaPreflightBlocked,
    SourceCatalog,
    assert_schema_preflight,
    load_source_catalog,
)

logger = get_logger(__name__)


class LifecycleService(_PlanningMixin, _ExpiryMixin):
    """Policy planning plus fenced expiry execution for grow/Veg/nongrow data."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        export_service: _ExportExecutor,
        deletion: SourceDeletion,
        derived_maintenance: _DerivedHistoryMaintenance,
        *,
        enabled: bool,
        clock: Callable[[], datetime] | None = None,
        freshness: _FreshnessGate | None = None,
        catalog: SourceCatalog | None = None,
    ) -> None:
        self.pool = pool
        self.queue = export_service.queue
        self.export_service = export_service
        self.deletion = deletion
        self.maintenance = derived_maintenance
        self.remote_reverify = export_service.reverify_artifact
        self.enabled = bool(enabled)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.freshness = freshness
        self._catalog = catalog
        self._catalog_cache: SourceCatalog | None = None
        self._cursor_rotation = 0
    # Clock and catalog helpers
    # ------------------------------------------------------------------

    def _now(self, now: datetime | None) -> datetime:
        moment = now if now is not None else self.clock()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("lifecycle clock must be aware")
        return moment.astimezone(UTC)

    def _source_catalog(self) -> SourceCatalog:
        if self._catalog_cache is None:
            self._catalog_cache = self._catalog or load_source_catalog()
        return self._catalog_cache

    @asynccontextmanager
    async def _db_errors(self):
        try:
            yield
        except asyncpg.exceptions.QueryCanceledError as error:
            raise ArchiveError("archive_database_unavailable", status_code=503) from error
        except asyncpg.exceptions.LockNotAvailableError as error:
            raise ArchiveError("archive_lock_timeout", status_code=503) from error
        except asyncpg.exceptions.DeadlockDetectedError as error:
            raise ArchiveError("archive_lock_timeout", status_code=503) from error

    async def _enqueue(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        dataset_id: UUID,
        kind: JobKind,
        requested_revision: int,
        *,
        required_closed: bool | None = None,
    ) -> ArchiveJob | None:
        try:
            return await self.queue.enqueue(
                dataset_id, kind, requested_revision, required_closed=required_closed
            )
        except ArchiveError as error:
            if error.code in {"archive_revision_conflict", "grow_not_closed", "archive_not_found"}:
                logger.debug("lifecycle enqueue skipped: %s", error.code)
                return None
            raise

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    async def preflight_activation(self) -> None:
        """Fail closed on source-schema drift or any conflicting source-age job."""
        catalog = self._source_catalog()
        async with self.pool.acquire() as conn:
            await assert_schema_preflight(conn, catalog=catalog)
            await assert_source_age_policies_clear(conn, catalog=catalog)

    async def run_lifecycle_tick(self, now: datetime | None = None) -> _LifecycleTickResult:
        """Plan durable archive jobs once per minute; never delete source rows here."""
        moment = self._now(now)
        if not self.enabled:
            return _LifecycleTickResult(enabled=False, now=moment)
        planned: list[_PlannedJob] = []
        held: list[_HeldScope] = []
        errors: list[str] = []
        try:
            await self.preflight_activation()
        except SourceAgePolicyBlocked as error:
            code = getattr(error, "code", "source_age_policy_conflict")
            held.append(_HeldScope("lifecycle", "source-age-policies", code))
            errors.append(f"activation_preflight:{code}")
            return _LifecycleTickResult(enabled=True, now=moment, held=tuple(held), errors=tuple(errors))
        except SourceAgePolicyInspectionFailed as error:
            code = getattr(error, "code", "source_age_policy_preflight_failed")
            held.append(_HeldScope("lifecycle", "source-age-policies", code))
            errors.append(f"activation_preflight:{code}")
            return _LifecycleTickResult(enabled=True, now=moment, held=tuple(held), errors=tuple(errors))
        except (ArchiveError, SchemaPreflightBlocked, asyncpg.PostgresError) as error:
            code = getattr(error, "code", "archive_database_unavailable")
            held.append(_HeldScope("lifecycle", "activation", code))
            errors.append(f"activation_preflight:{code}")
            logger.warning("lifecycle activation preflight blocked", exc_info=True)
            return _LifecycleTickResult(enabled=True, now=moment, held=tuple(held), errors=tuple(errors))

        async with self._db_errors():
            async with self.pool.acquire() as conn:
                try:
                    planned.extend(await self._plan_grows(conn, moment, held))
                except (ArchiveError, SchemaPreflightBlocked, asyncpg.PostgresError) as error:
                    code = getattr(error, "code", type(error).__name__)
                    errors.append(f"grow_planning:{code}")
                    logger.warning("grow lifecycle planning failed", exc_info=True)
                try:
                    planned.extend(await self._plan_veg(conn, moment, held))
                except (ArchiveError, SchemaPreflightBlocked, asyncpg.PostgresError) as error:
                    code = getattr(error, "code", type(error).__name__)
                    errors.append(f"veg_planning:{code}")
                    logger.warning("Veg lifecycle planning failed", exc_info=True)
        if planned or held or errors:
            logger.info("lifecycle tick planned=%s held=%s errors=%s", len(planned), len(held), len(errors))
        return _LifecycleTickResult(
            enabled=True, now=moment, planned=tuple(planned), held=tuple(held), errors=tuple(errors),
        )



    async def execute_export(self, claim: JobClaim) -> UUID:
        """Run explicit/manual export even while lifecycle removal is disabled."""
        if claim.job.kind != "export":
            raise ArchiveError("archive_job_kind_invalid")
        if self.enabled:
            await self.preflight_activation()
        return await self.export_service.export_dataset(claim)

    async def execute_annual_merge(self, claim: JobClaim) -> UUID:
        if claim.job.kind != "annual_merge" or not self.enabled:
            raise ArchiveError("archive_lifecycle_disabled")
        await self.preflight_activation()
        return await self.export_service.merge_annual(claim)

    async def execute_artifact_cleanup(self, claim: JobClaim) -> int:
        if claim.job.kind != "artifact_cleanup" or not self.enabled:
            raise ArchiveError("archive_lifecycle_disabled")
        return await self.export_service.cleanup_artifacts(claim)

    async def expire(self, claim: JobClaim) -> int:
        """Remove only source rows covered by a verified ZIP."""
        if claim.job.kind != "expire":
            raise ArchiveError("archive_job_kind_invalid")
        if not self.enabled:
            raise ArchiveError("archive_lifecycle_disabled")
        if self._freshness_paused():
            raise ArchiveError("archive_freshness_paused")
        await self.preflight_activation()
        now = self._now(None)
        async with self.pool.acquire() as conn:
            dataset = await conn.fetchrow(
                "SELECT d.*,g.state AS lifecycle_state,g.original_drying_end,g.first_closed_at "
                "FROM public.archive_dataset d LEFT JOIN public.grow_lifecycle g USING(grow_plan_id) "
                "WHERE d.dataset_id=$1",
                claim.fence.dataset_id,
            )
            if dataset is None:
                raise ArchiveError("archive_not_found", status_code=404)
            await self.queue.check_fence(conn, claim)
            if dataset["data_revision"] != dataset["published_revision"]:
                raise ArchiveError("archive_job_superseded")
            if dataset["state"] not in ("ready", "zip_only"):
                raise ArchiveError("archive_job_superseded")
            artifact = await self._current_artifact(
                conn, dataset["dataset_id"], dataset["published_revision"], kind=dataset["kind"]
            )
            if artifact is None:
                raise ArchiveError("archive_remote_unavailable", status_code=503)
            if dataset["kind"] == "grow":
                if dataset["lifecycle_state"] != "closed" or dataset["first_closed_at"] is None:
                    raise ArchiveError("grow_not_closed")
                if dataset["original_drying_end"] is None or dataset["anniversary_at"] is None:
                    raise ArchiveError("archive_anniversary_missing")
                expected = _toronto_midnight(
                    _add_calendar_months(dataset["original_drying_end"] + timedelta(days=1), 12)
                )
                if dataset["anniversary_at"].astimezone(UTC) != expected:
                    raise ArchiveError("archive_anniversary_invalid")
                if now < dataset["anniversary_at"]:
                    raise ArchiveError("archive_expiry_not_due")
                if artifact.get("component_revision") is not None:
                    raise ArchiveError("archive_integrity_failed")
                verified_manifest = await self._verify_remote_artifact(artifact)
                rows_before = now
            else:
                if dataset["kind"] != "veg_quarter":
                    raise ArchiveError("archive_job_kind_invalid")
                year, quarter = int(dataset["calendar_year"]), int(dataset["quarter"])
                quarter_start, quarter_end = _quarter_bounds(year, quarter)
                if dataset["available_to"] is None or dataset["available_to"] < quarter_end:
                    raise ArchiveError("archive_veg_quarter_incomplete")
                rows_before = _retention_cutoffs(now).local_expiry_before
                if rows_before <= quarter_start:
                    return 0
                if (
                    artifact.get("component_revision") is None
                    and int(artifact["revision"]) != int(dataset["published_revision"])
                ):
                    raise ArchiveError("archive_integrity_failed")
                verified_manifest = await self._verify_remote_artifact(artifact)
        pinned_archive = await self.export_service.prior_archive_path(dataset)
        try:
            deleted = await self.deletion.delete_verified(
                claim,
                verified_manifest,
                rows_before=rows_before,
                now=now,
                archive_path=pinned_archive.path,
                component_dataset_id=pinned_archive.component_dataset_id,
            )
        finally:
            await self.export_service.release_prior_archive(pinned_archive.path)
        async with self.pool.acquire() as conn, conn.transaction():
            await self.queue.check_fence(conn, claim, lock=True)
            if not await self._source_observations_exist(
                conn, dataset, outside_removed_availability=True
            ):
                await conn.execute(
                    "UPDATE public.archive_dataset SET state='zip_only',updated_at=clock_timestamp() "
                    "WHERE dataset_id=$1 AND data_revision=$2 AND published_revision=$2",
                    dataset["dataset_id"], claim.fence.data_revision,
                )
        return deleted
    async def _verify_remote_artifact(self, artifact: Mapping[str, Any]) -> ArchiveManifest:
        """Perform a real full-GET verifier call and bind it to catalog identity."""
        manifest = await self.remote_reverify(artifact["artifact_id"])
        if (
            not isinstance(manifest, ArchiveManifest)
            or manifest.dataset_id != str(artifact["dataset_id"])
            or manifest.revision != int(artifact["revision"])
            or manifest.sha256 != str(artifact["sha256"])
            or manifest.byte_size != int(artifact["byte_size"])
        ):
            raise ArchiveError("archive_integrity_failed")
        component_revision = artifact.get("component_revision")
        if component_revision is not None:
            from .annual import manifest_sha256

            matching = [
                component for component in manifest.data.get("components", ())
                if isinstance(component, Mapping)
                and str(component.get("dataset_id")) == str(artifact["child_dataset_id"])
                and int(component.get("revision", -1)) == int(component_revision)
            ]
            if len(matching) != 1:
                raise ArchiveError("archive_integrity_failed")
            component = matching[0]
            child_manifest = component.get("manifest")
            if not isinstance(child_manifest, Mapping):
                raise ArchiveError("archive_integrity_failed")
            if (
                component.get("member_prefix") != artifact.get("member_prefix")
                or component.get("manifest_sha256") != artifact.get("component_manifest_sha256")
                or manifest_sha256(child_manifest) != artifact.get("component_manifest_sha256")
                or str(child_manifest.get("dataset_id")) != str(artifact["child_dataset_id"])
                or int(child_manifest.get("revision", -1)) != int(component_revision)
            ):
                raise ArchiveError("archive_integrity_failed")
            counts = artifact.get("observation_counts")
            if isinstance(counts, str):
                counts = json.loads(counts)
            if dict(child_manifest.get("observation_counts") or {}) != dict(counts or {}):
                raise ArchiveError("archive_integrity_failed")
        return manifest
