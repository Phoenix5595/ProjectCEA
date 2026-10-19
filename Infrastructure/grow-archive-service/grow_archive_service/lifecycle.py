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

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
from typing import Any, Final, Protocol, TYPE_CHECKING
from uuid import UUID

import asyncpg

from shared.grow_lifecycle import (
    CALENDAR_TZ,
    add_calendar_months,
    retention_cutoffs,
    toronto_midnight,
)
from shared.infra_logging import get_logger

from .deletion import SourceDeletion
from .capture import PriorArchive
from .jobs import ArchiveJobQueue
from .manifest import ArchiveManifest
from .membership import CaptureWindow, load_windows, selection_sql, window_arrays
from .models import ArchiveError, ArchiveJob, JobClaim, JobKind
from .policy_gate import (
    SourceAgePolicyBlocked,
    SourceAgePolicyInspectionFailed,
    assert_source_age_policies_clear,
)
from .source_retention import ANCHOR_COLUMNS, prepare_retention_anchors, removable_observation_sql
from .streams import STREAM_BINDINGS, StreamBinding, time_nullable
if TYPE_CHECKING:
    from .exporter import LoadPin

from .schema import (
    SchemaPreflightBlocked,
    SourceCatalog,
    SourceStream,
    assert_schema_preflight,
    load_source_catalog,
)

logger = get_logger(__name__)

FLOWER_ROOM: Final = "Flower Room"
VEG_ROOM: Final = "Veg Room"
IDENTITY_STREAMS: Final = frozenset({"measurement"})

#: Forward expiry window and bounded delete slices.
EXPIRY_DELETE_SLICE: Final = timedelta(hours=1)
EXPIRY_WINDOWS_PER_PAIR: Final = 2
EXPIRY_SLICES_PER_PAIR: Final = 4
EXPIRY_TICK_ROW_BUDGET: Final = 80_000
PURGE_BATCH_ROWS: Final = 4096
PURGE_BATCH_BYTES: Final = 8 * 1024 * 1024
ZIP_DUE_MONTHS: Final = 9
CURSOR_RELATION: Final = "public.archive_expiry_cursor"
LIFECYCLE_TICK_SECONDS: Final = 60
DISPATCH_POLL_SECONDS: Final = 1
MAX_WINDOW_BOUNDARIES: Final = 128


def quarter_start_date(year: int, quarter: int) -> date:
    """Return the Toronto calendar date starting a calendar quarter."""
    if quarter < 1 or quarter > 4:
        raise ValueError("quarter must be between 1 and 4")
    return date(year, 3 * (quarter - 1) + 1, 1)


def quarter_bounds(year: int, quarter: int) -> tuple[datetime, datetime]:
    """Return the half-open UTC bounds of a Toronto calendar quarter."""
    start = quarter_start_date(year, quarter)
    return toronto_midnight(start), toronto_midnight(add_calendar_months(start, 3))


def quarter_of(day: date) -> tuple[int, int]:
    """Return the (year, quarter) containing a Toronto local date."""
    return day.year, (day.month - 1) // 3 + 1


def previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    """Return the calendar quarter immediately before ``(year, quarter)``."""
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def latest_quarter_boundary(now: datetime) -> date:
    """Return the most recent Toronto Jan/Apr/Jul/Oct 1 boundary at or before ``now``."""
    local = now.astimezone(CALENDAR_TZ).date()
    return date(local.year, 3 * ((local.month - 1) // 3) + 1, 1)



@dataclass(frozen=True, slots=True)
class PlannedJob:
    """One durable archive job the tick ensured exists."""

    dataset_id: UUID
    kind: JobKind
    requested_revision: int
    job_id: UUID


@dataclass(frozen=True, slots=True)
class ExpiredInterval:
    """One nongrow interval whose observations were removed."""

    source_table: str
    room: str
    start_at: datetime
    end_at: datetime
    rows: int


@dataclass(frozen=True, slots=True)
class HeldScope:
    """One scope deliberately held back from expiry or removal."""

    scope: str
    reference: str
    code: str


@dataclass(frozen=True, slots=True)
class LifecycleTickResult:
    """The complete observable outcome of one lifecycle planning tick."""

    enabled: bool
    now: datetime
    planned: tuple[PlannedJob, ...] = ()
    expired: tuple[ExpiredInterval, ...] = ()
    held: tuple[HeldScope, ...] = ()
    errors: tuple[str, ...] = ()
    paused_early: bool = False


@dataclass(frozen=True, slots=True)
class NongrowExpiryResult:
    """One bounded nongrow expiry pass plus scopes deliberately retained."""

    expired: tuple[ExpiredInterval, ...] = ()
    held: tuple[HeldScope, ...] = ()
    deleted_rows: int = 0
    paused_early: bool = False


@dataclass(frozen=True, slots=True)
class _IdentityAssignment:
    source_table: str
    identity: int
    room: str
    start: datetime
    end: datetime
    precedence: int


@dataclass(frozen=True, slots=True)
class _Segment:
    start: datetime
    end: datetime
    room: str


class DerivedHistoryMaintenance(Protocol):
    """The step-5 derived-aggregate maintenance surface owned by its worker."""

    async def preflight(self, source_tables: Sequence[str]) -> Any: ...

    async def refresh(
        self, source_table: str, room: str | None, start: datetime, end: datetime
    ) -> None: ...






class ExportExecutor(Protocol):
    """The real step-6 handlers used by the one archive dispatcher."""
    queue: ArchiveJobQueue

    async def export_dataset(self, claim: JobClaim) -> UUID: ...

    async def merge_annual(self, claim: JobClaim) -> UUID: ...

    async def run_load(self, claim: JobClaim) -> "LoadPin": ...

    async def cleanup_artifacts(self, claim: JobClaim) -> int: ...

    async def expire_loads(self) -> int: ...

    async def reverify_artifact(self, artifact_id: UUID) -> ArchiveManifest: ...
    async def prior_archive_path(self, dataset: Mapping[str, Any]) -> PriorArchive: ...

    async def release_prior_archive(self, path: Path) -> None: ...


class FreshnessGate(Protocol):
    """The read-only freshness view used to pause expiry batches."""

    @property
    def paused(self) -> bool: ...


def _effective_segments(assignments: Sequence[_IdentityAssignment]) -> tuple[_Segment, ...]:
    """Resolve date-bounded assignments; conflicts remain deliberately uncovered.

    Explicit owner corrections (precedence 1) override frozen capture snapshots
    (precedence 0). Two different rooms at the same precedence are ambiguous,
    so that interval is not assigned to either room and can never be expired.
    """
    points = sorted({point for item in assignments for point in (item.start, item.end)})
    resolved: list[_Segment] = []
    for start, end in zip(points, points[1:]):
        if start >= end:
            continue
        covering = [item for item in assignments if item.start <= start and item.end >= end]
        if not covering:
            continue
        highest = max(item.precedence for item in covering)
        rooms = {item.room for item in covering if item.precedence == highest}
        if len(rooms) != 1:
            continue
        room = next(iter(rooms))
        if resolved and resolved[-1].end == start and resolved[-1].room == room:
            resolved[-1] = _Segment(resolved[-1].start, end, room)
        else:
            resolved.append(_Segment(start, end, room))
    return tuple(resolved)


class _IdentityRoomIndex:
    """Bounded override-first / frozen-snapshot room resolution for bulk expiry."""

    def __init__(self, assignments: Sequence[_IdentityAssignment]) -> None:
        grouped: dict[str, dict[int, list[_IdentityAssignment]]] = defaultdict(lambda: defaultdict(list))
        for item in assignments:
            grouped[item.source_table][item.identity].append(item)
        self._segments: dict[str, dict[int, tuple[_Segment, ...]]] = {
            table: {identity: _effective_segments(items) for identity, items in identities.items()}
            for table, identities in grouped.items()
        }

    def identities_for_room(self, source_table: str, room: str) -> frozenset[int]:
        """Every identity with a resolved interval in this room."""
        return frozenset(
            identity for identity, segments in self._segments.get(source_table, {}).items()
            if any(segment.room == room for segment in segments)
        )

    def ids_for(self, source_table: str, room: str, start: datetime, end: datetime) -> frozenset[int]:
        """Identities whose effective room covers the complete half-open interval."""
        if start >= end:
            raise ValueError("identity room interval must be nonempty")
        result: set[int] = set()
        for identity, segments in self._segments.get(source_table, {}).items():
            position = start
            for segment in segments:
                if segment.end <= position:
                    continue
                if segment.start > position or segment.room != room:
                    break
                position = min(segment.end, end)
                if position >= end:
                    result.add(identity)
                    break
        return frozenset(result)

    def covers_identity(self, source_table: str, identity: int, start: datetime, end: datetime) -> bool:
        """Whether every instant in an observation span has one known room."""
        if start >= end:
            return False
        position = start
        for segment in self._segments.get(source_table, {}).get(identity, ()):
            if segment.end <= position:
                continue
            if segment.start > position:
                return False
            position = min(segment.end, end)
            if position >= end:
                return True
        return False

    def boundaries(self, source_table: str, start: datetime, end: datetime) -> tuple[datetime, ...]:
        """Effective-room change points strictly inside ``(start, end)``."""
        points: set[datetime] = set()
        for segments in self._segments.get(source_table, {}).values():
            for segment in segments:
                if start < segment.start < end:
                    points.add(segment.start)
                if start < segment.end < end:
                    points.add(segment.end)
        return tuple(sorted(points))



def _unique_held(items: Sequence[HeldScope]) -> list[HeldScope]:
    seen: set[tuple[str, str, str]] = set()
    result: list[HeldScope] = []
    for item in items:
        key = (item.scope, item.reference, item.code)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

class LifecycleService:
    """Policy planning plus fenced expiry execution for grow/Veg/nongrow data."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        export_service: ExportExecutor,
        deletion: SourceDeletion,
        derived_maintenance: DerivedHistoryMaintenance,
        *,
        enabled: bool,
        clock: Callable[[], datetime] | None = None,
        freshness: FreshnessGate | None = None,
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

    async def run_lifecycle_tick(self, now: datetime | None = None) -> LifecycleTickResult:
        """Plan durable archive jobs once per minute; never delete source rows here."""
        moment = self._now(now)
        if not self.enabled:
            return LifecycleTickResult(enabled=False, now=moment)
        planned: list[PlannedJob] = []
        held: list[HeldScope] = []
        errors: list[str] = []
        try:
            await self.preflight_activation()
        except SourceAgePolicyBlocked as error:
            code = getattr(error, "code", "source_age_policy_conflict")
            held.append(HeldScope("lifecycle", "source-age-policies", code))
            errors.append(f"activation_preflight:{code}")
            return LifecycleTickResult(enabled=True, now=moment, held=tuple(held), errors=tuple(errors))
        except SourceAgePolicyInspectionFailed as error:
            code = getattr(error, "code", "source_age_policy_preflight_failed")
            held.append(HeldScope("lifecycle", "source-age-policies", code))
            errors.append(f"activation_preflight:{code}")
            return LifecycleTickResult(enabled=True, now=moment, held=tuple(held), errors=tuple(errors))
        except (ArchiveError, SchemaPreflightBlocked, asyncpg.PostgresError) as error:
            code = getattr(error, "code", "archive_database_unavailable")
            held.append(HeldScope("lifecycle", "activation", code))
            errors.append(f"activation_preflight:{code}")
            logger.warning("lifecycle activation preflight blocked", exc_info=True)
            return LifecycleTickResult(enabled=True, now=moment, held=tuple(held), errors=tuple(errors))

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
        return LifecycleTickResult(
            enabled=True, now=moment, planned=tuple(planned), held=tuple(held), errors=tuple(errors),
        )

    def _freshness_paused(self) -> bool:
        # Destructive work and archive IO fail closed unless the live freshness
        # guard was explicitly wired by the service lifespan.
        return self.freshness is None or self.freshness.paused

    # ------------------------------------------------------------------
    # Grow planning
    # ------------------------------------------------------------------

    async def _plan_grows(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        now: datetime,
        held: list[HeldScope],
    ) -> list[PlannedJob]:
        rows = await conn.fetch(
            "SELECT d.dataset_id,d.data_revision,d.published_revision,d.state,d.anniversary_at,"
            "g.state AS lifecycle_state,g.original_drying_end,g.first_closed_at "
            "FROM public.archive_dataset d JOIN public.grow_lifecycle g USING(grow_plan_id) "
            "WHERE d.kind='grow' AND g.state='closed' "
            "ORDER BY d.anniversary_at NULLS LAST,d.dataset_id"
        )
        planned: list[PlannedJob] = []
        for row in rows:
            dataset_id = row["dataset_id"]
            if row["first_closed_at"] is None or row["original_drying_end"] is None:
                held.append(HeldScope("grow", str(dataset_id), "archive_anniversary_missing"))
                continue
            expected_anniversary = toronto_midnight(
                add_calendar_months(row["original_drying_end"] + timedelta(days=1), 12)
            )
            anniversary = row["anniversary_at"]
            if anniversary is None or anniversary.astimezone(UTC) != expected_anniversary:
                held.append(HeldScope("grow", str(dataset_id), "archive_anniversary_invalid"))
                continue
            state = row["state"]
            if state == "blocked":
                held.append(HeldScope("grow", str(dataset_id), "archive_blocked"))
                continue
            if state == "copying":
                continue
            revision = int(row["data_revision"])
            published = row["published_revision"]
            if published != revision:
                kind: JobKind = "export"
            elif now >= anniversary and state != "zip_only":
                kind = "expire"
            else:
                continue
            job = await self._enqueue(conn, dataset_id, kind, revision)
            if job is not None:
                planned.append(PlannedJob(dataset_id, kind, revision, job.job_id))
        return planned


    async def _current_artifact(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        dataset_id: UUID,
        revision: int | None,
        *,
        kind: str,
    ) -> Mapping[str, Any] | None:
        if revision is None:
            return None
        row = await conn.fetchrow(
            "SELECT artifact_id,dataset_id,revision,remote_instance_id,remote_path,sha256,byte_size "
            "FROM public.archive_artifact WHERE dataset_id=$1 AND revision=$2 AND state='verified' "
            "AND remote_instance_id IS NOT NULL AND remote_path IS NOT NULL AND sha256 IS NOT NULL "
            "AND byte_size IS NOT NULL ORDER BY verified_at DESC NULLS LAST LIMIT 1",
            dataset_id, revision,
        )
        if row is not None:
            return dict(row)
        if kind == "veg_quarter":
            component = await conn.fetchrow(
                "SELECT p.artifact_id,p.dataset_id,p.revision,p.remote_instance_id,p.remote_path,"
                "p.sha256,p.byte_size,c.child_dataset_id,c.child_revision AS component_revision,c.member_prefix,"
                "c.manifest_sha256 AS component_manifest_sha256,c.observation_counts "
                "FROM public.archive_component c JOIN public.archive_artifact p "
                "ON p.artifact_id=c.parent_artifact_id "
                "WHERE c.child_dataset_id=$1 AND c.child_revision=$2 AND p.state='verified' "
                "AND p.remote_instance_id IS NOT NULL AND p.remote_path IS NOT NULL "
                "AND p.sha256 IS NOT NULL AND p.byte_size IS NOT NULL "
                "ORDER BY p.verified_at DESC NULLS LAST LIMIT 1",
                dataset_id, revision,
            )
            if component is not None:
                return dict(component)
        return None

    # ------------------------------------------------------------------
    # Veg quarter and annual planning
    # ------------------------------------------------------------------

    async def _plan_veg(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        now: datetime,
        held: list[HeldScope],
    ) -> list[PlannedJob]:
        cutoffs = retention_cutoffs(now)
        compress_before = cutoffs.veg_compress_before
        expiry_before = cutoffs.local_expiry_before
        latest_boundary = latest_quarter_boundary(now)
        due_before = add_calendar_months(latest_boundary, -ZIP_DUE_MONTHS)
        rows = await conn.fetch(
            "SELECT * FROM public.archive_dataset WHERE kind IN ('veg_quarter','veg_year') "
            "ORDER BY calendar_year,quarter NULLS FIRST"
        )
        quarters: dict[tuple[int, int], Mapping[str, Any]] = {
            (int(row["calendar_year"]), int(row["quarter"])): dict(row)
            for row in rows if row["kind"] == "veg_quarter"
        }
        years: dict[int, Mapping[str, Any]] = {
            int(row["calendar_year"]): dict(row) for row in rows if row["kind"] == "veg_year"
        }
        streams = await self._expirable_streams(conn)
        identity_index = await self._identity_room_index(conn)
        oldest = await self._oldest_veg_quarter(
            conn, streams, identity_index, compress_before, tuple(quarters.values())
        )
        planned: list[PlannedJob] = []
        year, quarter = quarter_of(now.astimezone(CALENDAR_TZ).date())
        while oldest is not None:
            current_start = quarter_start_date(year, quarter)
            if current_start < oldest:
                break
            start_utc, end_utc = quarter_bounds(year, quarter)
            if start_utc >= compress_before:
                # This newest partial quarter has not crossed the three-month
                # cutoff yet; continue backward rather than skipping older work.
                year, quarter = previous_quarter(year, quarter)
                continue
            eligible_to = min(end_utc, compress_before)
            if eligible_to <= start_utc:
                year, quarter = previous_quarter(year, quarter)
                continue
            dataset = quarters.get((year, quarter))
            if dataset is None:
                if not await self._quarter_has_data(
                    conn, identity_index, start_utc, eligible_to, streams
                ):
                    year, quarter = previous_quarter(year, quarter)
                    continue
                created = await conn.fetchrow(
                    "INSERT INTO public.archive_dataset(kind,grow_plan_id,calendar_year,quarter,room,"
                    "calendar_revision,data_revision,state,available_from,available_to) "
                    "VALUES('veg_quarter',NULL,$1,$2,$3,1,1,'pending',$4,$5) "
                    "ON CONFLICT DO NOTHING RETURNING *",
                    year, quarter, VEG_ROOM, start_utc, eligible_to,
                )
                if created is None:
                    created = await conn.fetchrow(
                        "SELECT * FROM public.archive_dataset WHERE kind='veg_quarter' "
                        "AND calendar_year=$1 AND quarter=$2",
                        year, quarter,
                    )
                if created is None:
                    year, quarter = previous_quarter(year, quarter)
                    continue
                dataset = dict(created)
                quarters[(year, quarter)] = dataset
            state = dataset["state"]
            if state == "blocked":
                held.append(HeldScope("veg_quarter", str(dataset["dataset_id"]), "archive_blocked"))
                year, quarter = previous_quarter(year, quarter)
                continue

            old_to = dataset["available_to"]
            if state != "zip_only" and (old_to is None or old_to < eligible_to):
                updated = await conn.fetchrow(
                    "UPDATE public.archive_dataset SET available_from=LEAST(COALESCE(available_from,$2),$2),"
                    "available_to=$3,data_revision=data_revision+1,"
                    "state=CASE WHEN state='zip_only' THEN state ELSE 'dirty' END,"
                    "updated_at=clock_timestamp() WHERE dataset_id=$1 RETURNING *",
                    dataset["dataset_id"], start_utc, eligible_to,
                )
                if updated is not None:
                    dataset = dict(updated)
                    quarters[(year, quarter)] = dataset
                    state = dataset["state"]

            published = dataset["published_revision"]
            revision = int(dataset["data_revision"])
            artifact = await self._current_artifact(
                conn, dataset["dataset_id"], published, kind="veg_quarter"
            )
            quarter_is_complete = dataset["available_to"] is not None and dataset["available_to"] >= end_utc
            if (
                quarter_is_complete
                and end_utc.astimezone(CALENDAR_TZ).date() <= due_before
                and (artifact is None or published != revision)
            ):
                job = await self._enqueue(conn, dataset["dataset_id"], "export", revision, required_closed=False)
                if job is not None:
                    planned.append(PlannedJob(dataset["dataset_id"], "export", revision, job.job_id))

            if (
                quarter_is_complete
                and artifact is not None
                and published is not None
                and published == revision
                and dataset["available_from"] is not None
            ):
                bound = min(dataset["available_to"], expiry_before)
                if bound > dataset["available_from"] and await self._source_observations_exist(
                    conn, dataset, before=expiry_before
                ):
                    job = await self._enqueue(conn, dataset["dataset_id"], "expire", revision, required_closed=False)
                    if job is not None:
                        planned.append(PlannedJob(dataset["dataset_id"], "expire", revision, job.job_id))

            year, quarter = previous_quarter(year, quarter)

        planned.extend(await self._plan_annual_merges(conn, tuple((*quarters.values(), *years.values())), years, held))
        return planned

    async def _oldest_veg_quarter(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        streams: Sequence[tuple[SourceStream, StreamBinding]],
        identity_index: _IdentityRoomIndex,
        before: datetime,
        existing: Sequence[Mapping[str, Any]],
    ) -> date | None:
        candidates: list[date] = []
        for dataset in existing:
            candidates.append(quarter_start_date(int(dataset["calendar_year"]), int(dataset["quarter"])))
        for descriptor, binding in streams:
            room_sql, room_args = self._room_expression(
                descriptor, binding, identity_index, VEG_ROOM, None, None,
                first_arg=2, alias="m",
            )
            if room_sql is None:
                continue
            time_column = self._sql_identifier(binding.time_column)
            value = await conn.fetchval(
                f'SELECT min(m.{time_column}) FROM public.{self._sql_identifier(descriptor.name)} m '
                f'WHERE m.{time_column}<$1 AND {room_sql}',
                before, *room_args,
            )
            if value is not None:
                candidates.append(quarter_start_date(*quarter_of(value.astimezone(CALENDAR_TZ).date())))
        return min(candidates) if candidates else None

    async def _plan_annual_merges(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        rows: Sequence[Mapping[str, Any]],
        years: Mapping[int, Mapping[str, Any]],
        held: list[HeldScope],
    ) -> list[PlannedJob]:
        children: dict[int, dict[int, Mapping[str, Any]]] = defaultdict(dict)
        for row in rows:
            if row["kind"] == "veg_quarter":
                children[int(row["calendar_year"])][int(row["quarter"])] = dict(row)
        planned: list[PlannedJob] = []
        for year in sorted(children):
            quarters = children[year]
            if set(quarters) != {1, 2, 3, 4}:
                continue
            complete = True
            for quarter in (1, 2, 3, 4):
                child = quarters[quarter]
                _, end = quarter_bounds(year, quarter)
                if (
                    child["state"] not in ("ready", "zip_only")
                    or child["published_revision"] is None
                    or child["published_revision"] != child["data_revision"]
                    or child["available_to"] is None
                    or child["available_to"] < end
                    or await self._current_artifact(
                        conn, child["dataset_id"], child["published_revision"], kind="veg_quarter"
                    ) is None
                ):
                    complete = False
                    break
            if not complete:
                continue
            desired_floor = 1 + sum(int(quarters[q]["published_revision"]) for q in (1, 2, 3, 4))
            annual = years.get(year)
            if annual is None:
                start = toronto_midnight(date(year, 1, 1))
                end = toronto_midnight(date(year + 1, 1, 1))
                created = await conn.fetchrow(
                    "INSERT INTO public.archive_dataset(kind,grow_plan_id,calendar_year,quarter,room,"
                    "calendar_revision,data_revision,state,available_from,available_to) "
                    "VALUES('veg_year',NULL,$1,NULL,$2,1,$3,'pending',$4,$5) "
                    "ON CONFLICT DO NOTHING RETURNING *",
                    year, VEG_ROOM, desired_floor, start, end,
                )
                if created is None:
                    created = await conn.fetchrow(
                        "SELECT * FROM public.archive_dataset WHERE kind='veg_year' AND calendar_year=$1",
                        year,
                    )
                if created is None:
                    continue
                annual = dict(created)
            if annual["state"] == "blocked":
                held.append(HeldScope("veg_year", str(annual["dataset_id"]), "archive_blocked"))
                continue
            desired = max(int(annual["data_revision"]), desired_floor)
            if desired > int(annual["data_revision"]):
                updated = await conn.fetchrow(
                    "UPDATE public.archive_dataset SET data_revision=$2,state='dirty',"
                    "updated_at=clock_timestamp() WHERE dataset_id=$1 RETURNING *",
                    annual["dataset_id"], desired,
                )
                if updated is not None:
                    annual = dict(updated)
            if (
                annual["published_revision"] == desired
                and await self._current_artifact(conn, annual["dataset_id"], desired, kind="veg_year") is not None
            ):
                continue
            job = await self._enqueue(
                conn, annual["dataset_id"], "annual_merge", desired, required_closed=False
            )
            if job is not None:
                planned.append(PlannedJob(annual["dataset_id"], "annual_merge", desired, job.job_id))
        return planned

    async def _quarter_has_data(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        identity_index: _IdentityRoomIndex,
        start: datetime,
        end: datetime,
        streams: Sequence[tuple[SourceStream, StreamBinding]],
    ) -> bool:
        if start >= end:
            return False
        for descriptor, binding in streams:
            room_sql, room_args = self._room_expression(
                descriptor, binding, identity_index, VEG_ROOM, start, end,
                first_arg=3, alias="m",
            )
            if room_sql is None:
                continue
            time_column = self._sql_identifier(binding.time_column)
            relation = self._sql_identifier(descriptor.name)
            exists = await conn.fetchval(
                f'SELECT EXISTS(SELECT 1 FROM public.{relation} m '
                f'WHERE m.{time_column}>=$1 AND m.{time_column}<$2 AND {room_sql} LIMIT 1)',
                start, end, *room_args,
            )
            if exists:
                return True
        return False

    async def _source_observations_exist(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        dataset: Mapping[str, Any],
        *,
        before: datetime | None = None,
        outside_removed_availability: bool = False,
    ) -> bool:
        windows = await load_windows(conn, dataset)
        if before is not None:
            windows = tuple(
                CaptureWindow(window.room, window.start, min(window.end, before))
                for window in windows
                if window.start < min(window.end, before)
            )
        if not windows:
            return False
        rooms, starts, ends = window_arrays(windows)
        bindings = await self._bound_streams(conn)
        for stream in self._source_catalog().defined_streams():
            if (
                stream.default_record_role != "observation"
                or stream.time_column is None
                or stream.name not in bindings
            ):
                continue
            predicate, _ = selection_sql(stream)
            relation = self._sql_identifier(stream.name)
            query = f"SELECT EXISTS(SELECT 1 FROM public.{relation} m WHERE {predicate}"
            args: tuple[Any, ...] = (rooms, starts, ends)
            if outside_removed_availability:
                time_column = self._sql_identifier(stream.time_column)
                query += (
                    " AND NOT EXISTS(SELECT 1 FROM public.archive_availability a "
                    "WHERE a.dataset_id=$4 AND a.source_table=$5 AND a.source_rows_removed "
                    "AND (a.room IS NULL OR a.room=$6) "
                    f"AND a.start_at<=m.{time_column} AND a.end_at>m.{time_column})"
                )
                args += (dataset["dataset_id"], stream.name, dataset["room"])
            exists = await conn.fetchval(query + " LIMIT 1)", *args)
            if exists:
                return True
        return False

    @staticmethod
    def _sql_identifier(value: str) -> str:
        """Quote only simple catalog-controlled identifiers used in SQL text."""
        if not value or not (value[0].isalpha() or value[0] == "_") or not all(
            char.isalnum() or char == "_" for char in value
        ):
            raise ArchiveError("schema_descriptor_unavailable")
        return f'"{value}"'

    # ------------------------------------------------------------------
    # Identity index and stream bindings
    # ------------------------------------------------------------------

    async def _identity_room_index(
        self, conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy
    ) -> _IdentityRoomIndex:
        assignments: list[_IdentityAssignment] = []
        for row in await conn.fetch(
            "SELECT source_table,source_identity,room,start_at,end_at FROM archive_scope_override"
        ):
            table = row["source_table"]
            if table not in IDENTITY_STREAMS:
                continue
            identity = self._identity_value(row["source_identity"])
            if identity is None:
                continue
            assignments.append(
                _IdentityAssignment(table, identity, row["room"], row["start_at"], row["end_at"], 1)
            )
        for row in await conn.fetch(
            "SELECT manifest->'identity_snapshots' AS snapshots FROM archive_dataset "
            "WHERE manifest ? 'identity_snapshots'"
        ):
            snapshots = row["snapshots"]
            if isinstance(snapshots, str):
                snapshots = json.loads(snapshots)
            for item in snapshots or ():
                if not isinstance(item, Mapping):
                    continue
                table = item.get("source_table")
                if table not in IDENTITY_STREAMS:
                    continue
                identity = self._identity_value(item.get("source_identity"))
                if identity is None:
                    continue
                try:
                    start = datetime.fromisoformat(str(item["start"]).replace("Z", "+00:00"))
                    end = datetime.fromisoformat(str(item["end"]).replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    logger.warning("frozen identity snapshot interval is invalid; identity held")
                    continue
                room = item.get("room")
                if not isinstance(room, str) or not room or start >= end:
                    continue
                assignments.append(_IdentityAssignment(table, identity, room, start, end, 0))
        return _IdentityRoomIndex(assignments)

    @staticmethod
    def _identity_value(identity: object) -> int | None:
        key = "sensor_id"
        if not isinstance(identity, Mapping):
            return None
        value = identity.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value

    async def _bound_streams(
        self, conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy
    ) -> Mapping[str, StreamBinding]:
        rows = await conn.fetch(
            "SELECT stream_name FROM unnest($1::text[]) AS streams(stream_name) "
            "WHERE to_regclass('public.'||quote_ident(stream_name)) IS NOT NULL",
            list(STREAM_BINDINGS),
        )
        present = {row["stream_name"] for row in rows}
        return {
            name: binding for name, binding in STREAM_BINDINGS.items() if name in present
        }

    async def _expirable_streams(
        self, conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy
    ) -> tuple[tuple[SourceStream, StreamBinding], ...]:
        """Bind every present, described, room-owned observation stream safely.

        Optional absent streams are omitted because schema preflight records
        them explicitly. Core/mismatched streams are blocked by preflight, not
        guessed here. Context streams and global observations never enter the
        age-expiry delete set.
        """
        catalog = self._source_catalog()
        bindings = await self._bound_streams(conn)
        result: list[tuple[SourceStream, StreamBinding]] = []
        for descriptor in catalog.defined_streams():
            if descriptor.default_record_role != "observation" or descriptor.time_column is None:
                continue
            binding = bindings.get(descriptor.name)
            if binding is None or binding.time_column != descriptor.time_column:
                continue
            columns = {column.name for column in descriptor.columns}
            if descriptor.name in IDENTITY_STREAMS:
                identity_column = "sensor_id"
                bound_column = binding.sensor_column
                if bound_column != identity_column or identity_column not in columns:
                    continue
            else:
                room_column = binding.room_column
                if not room_column or room_column not in columns:
                    continue
            if descriptor.name not in ANCHOR_COLUMNS:
                # A new stream is never destructive until its operational
                # latest/predecessor grouping semantics are explicitly known.
                continue
            result.append((descriptor, binding))
        return tuple(result)

    async def run_nongrow_expiry(self, now: datetime | None = None) -> NongrowExpiryResult:
        """Expire non-Veg observations older than twelve Toronto calendar months.

        The known room set is acquired first; each room advisory lock is held
        before identity/window classification and throughout a bounded source
        delete transaction. Any ambiguous, NULL/global, manual-expiry or anchor
        data stays in place and is reported as held.
        """
        moment = self._now(now)
        if not self.enabled:
            return NongrowExpiryResult()
        await self.preflight_activation()
        cutoff = retention_cutoffs(moment).local_expiry_before
        async with self._db_errors():
            async with self.pool.acquire() as conn:
                if await conn.fetchval("SELECT to_regclass($1)::text", CURSOR_RELATION) is None:
                    raise ArchiveError("archive_expiry_cursor_missing")
                streams = await self._expirable_streams(conn)
                if not streams:
                    return NongrowExpiryResult()
                await self.maintenance.preflight([item[0].name for item in streams])
                all_rooms = await self._known_rooms(conn)
                rooms = tuple(room for room in all_rooms if room != VEG_ROOM)
                if not rooms:
                    return NongrowExpiryResult()
                async with conn.transaction():
                    await self._lock_rooms(conn, all_rooms)
                    identity_index = await self._identity_room_index(conn)
                    held = await self._unresolved_scope_holds(
                        conn, streams, identity_index, all_rooms, cutoff
                    )
                await self._refresh_pending_expired(conn)
                pairs = [(descriptor, binding, room) for descriptor, binding in streams for room in rooms]
                if pairs:
                    rotation = self._cursor_rotation % len(pairs)
                    pairs = pairs[rotation:] + pairs[:rotation]
                    self._cursor_rotation += 1
                expired: list[ExpiredInterval] = []
                budget = EXPIRY_TICK_ROW_BUDGET
                for descriptor, binding, room in pairs:
                    if budget <= 0 or self._freshness_paused():
                        break
                    before_count = len(expired)
                    deleted = 0
                    async with conn.transaction():
                        await self._lock_rooms(conn, (room,))
                        # Re-read ownership only after the matching Calendar /
                        # scope-correction lock is held.
                        identity_index = await self._identity_room_index(conn)
                        if descriptor.name in IDENTITY_STREAMS and not identity_index.identities_for_room(
                            descriptor.name, room
                        ):
                            continue
                        await self._bootstrap_cursor(conn, descriptor, binding, identity_index, room, cutoff)
                        deleted = await self._expire_pair(
                            conn, descriptor, binding, identity_index, room, cutoff,
                            min(budget, EXPIRY_TICK_ROW_BUDGET), expired, held, moment,
                        )
                    budget -= deleted
                    for interval in expired[before_count:]:
                        await self._refresh_expired_interval(interval, moment)
                paused = self._freshness_paused()
                return NongrowExpiryResult(tuple(expired), tuple(_unique_held(held)),
                                           sum(item.rows for item in expired), paused)

    async def _known_rooms(
        self, conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy
    ) -> tuple[str, ...]:
        rows = await conn.fetch(
            "SELECT name AS room FROM public.room WHERE name IS NOT NULL "
            "UNION SELECT location AS room FROM public.calendar_event WHERE location IS NOT NULL "
            "UNION SELECT room FROM public.archive_scope_override WHERE room IS NOT NULL "
            "UNION SELECT room FROM public.archive_window WHERE room IS NOT NULL "
            "UNION SELECT room FROM public.archive_dataset WHERE room IS NOT NULL"
        )
        names = {FLOWER_ROOM, VEG_ROOM}
        names.update(str(row["room"]) for row in rows if isinstance(row["room"], str) and row["room"])
        return tuple(sorted(names))

    async def _lock_rooms(
        self, conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy, rooms: Sequence[str]
    ) -> None:
        for room in sorted(set(rooms)):
            await conn.execute(
                "SELECT pg_advisory_xact_lock(public.sj_history_room_lock_key($1))", room
            )

    async def _unresolved_scope_holds(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        streams: Sequence[tuple[SourceStream, StreamBinding]],
        identity_index: _IdentityRoomIndex,
        known_rooms: Sequence[str],
        cutoff: datetime,
    ) -> list[HeldScope]:
        held: list[HeldScope] = [
            HeldScope("static", "configuration-and-current-state", "static_state_retained")
        ]
        rooms = tuple(sorted(set(known_rooms)))
        for descriptor, binding in streams:
            anchor_relation = await prepare_retention_anchors(conn, descriptor, cutoff)
            relation = f"public.{self._sql_identifier(descriptor.name)}"
            time_column = self._sql_identifier(binding.time_column)
            if descriptor.name in IDENTITY_STREAMS:
                identity_column = "sensor_id"
                identity_sql = self._sql_identifier(identity_column)
                ids = sorted(set().union(*(
                    identity_index.identities_for_room(descriptor.name, room) for room in rooms
                ))) if rooms else []
                nulls = await conn.fetchval(
                    f"SELECT count(*) FROM {relation} m WHERE m.{time_column}<$1 "
                    f"AND m.{identity_sql} IS NULL", cutoff
                )
                if nulls:
                    held.append(HeldScope("nongrow", descriptor.name, "archive_scope_unresolved"))
                if ids:
                    groups = await conn.fetch(
                        f"SELECT m.{identity_sql} AS identity,min(m.{time_column}) AS first_at,"
                        f"max(m.{time_column}) AS last_at FROM {relation} m "
                        f"WHERE m.{time_column}<$1 AND m.{identity_sql}=ANY($2::bigint[]) "
                        f"GROUP BY m.{identity_sql}", cutoff, ids
                    )
                    for row in groups:
                        last_at = row["last_at"] + timedelta(microseconds=1)
                        if not identity_index.covers_identity(
                            descriptor.name, int(row["identity"]), row["first_at"], last_at
                        ):
                            held.append(HeldScope("nongrow", descriptor.name, "archive_scope_unresolved"))
                            break
                else:
                    unresolved = await conn.fetchval(
                        f"SELECT EXISTS(SELECT 1 FROM {relation} m WHERE m.{time_column}<$1 "
                        f"AND m.{identity_sql} IS NOT NULL)", cutoff
                    )
                    if unresolved:
                        held.append(HeldScope("nongrow", descriptor.name, "archive_scope_unresolved"))
                if ids:
                    unknown = await conn.fetchval(
                        f"SELECT EXISTS(SELECT 1 FROM {relation} m WHERE m.{time_column}<$1 "
                        f"AND (m.{identity_sql} IS NULL OR NOT (m.{identity_sql}=ANY($2::bigint[]))))",
                        cutoff, ids,
                    )
                    if unknown:
                        held.append(HeldScope("nongrow", descriptor.name, "archive_scope_unresolved"))
                    removable = removable_observation_sql(
                        descriptor, alias="m", cutoff="$1", anchor_relation=anchor_relation,
                    )
                    anchors = await conn.fetchval(
                        f"SELECT EXISTS(SELECT 1 FROM {relation} m WHERE m.{time_column}<$1 "
                        f"AND m.{identity_sql}=ANY($2::bigint[]) AND NOT {removable})",
                        cutoff, ids,
                    )
                    if anchors:
                        held.append(HeldScope("nongrow", descriptor.name, "operational_anchor_retained"))
                continue

            room_column = self._sql_identifier(str(binding.room_column))
            unknown = await conn.fetchval(
                f"SELECT EXISTS(SELECT 1 FROM {relation} m WHERE m.{time_column}<$1 "
                f"AND (m.{room_column} IS NULL OR NOT (m.{room_column}=ANY($2::text[]))) "
                f"AND m.{room_column} IS DISTINCT FROM $3)",
                cutoff, list(rooms), VEG_ROOM,
            )
            if unknown:
                held.append(HeldScope("nongrow", descriptor.name, "archive_scope_unresolved"))
            removable = removable_observation_sql(
                descriptor, alias="m", cutoff="$1", anchor_relation=anchor_relation,
            )
            anchors = await conn.fetchval(
                f"SELECT EXISTS(SELECT 1 FROM {relation} m WHERE m.{time_column}<$1 "
                f"AND m.{room_column}=ANY($2::text[]) AND NOT {removable})",
                cutoff, list(rooms),
            )
            if anchors:
                held.append(HeldScope("nongrow", descriptor.name, "operational_anchor_retained"))
            if descriptor.name == "relay_observation":
                global_facts = await conn.fetchval(
                    f"SELECT EXISTS(SELECT 1 FROM {relation} m WHERE m.{time_column}<$1 "
                    f"AND (m.channel IS NULL OR m.reason IN ('heartbeat','session_started','session_ended')))",
                    cutoff,
                )
                if global_facts:
                    held.append(HeldScope("nongrow", descriptor.name, "global_relay_context_retained"))
            if await time_nullable(conn, descriptor.name, binding.time_column):
                null_time = await conn.fetchval(
                    f"SELECT EXISTS(SELECT 1 FROM {relation} m WHERE m.{time_column} IS NULL)"
                )
                if null_time:
                    held.append(HeldScope("nongrow", descriptor.name, "null_time_context_retained"))
            if descriptor.name == "control_history" and "manual_expires_at" in {
                column.name for column in descriptor.columns
            }:
                manual_anchor = await conn.fetchval(
                    f"SELECT EXISTS(SELECT 1 FROM {relation} m WHERE m.manual_expires_at IS NOT NULL "
                    f"AND m.{time_column}<$1)", cutoff
                )
                if manual_anchor:
                    held.append(HeldScope("nongrow", descriptor.name, "manual_expiry_anchor_retained"))
        for descriptor in self._source_catalog().defined_streams():
            if descriptor.default_record_role == "context":
                held.append(HeldScope("context", descriptor.name, "replay_context_retained"))
        return held

    async def _refresh_pending_expired(
        self, conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy
    ) -> None:
        rows = await conn.fetch(
            "SELECT availability_id,source_table,room,start_at,end_at FROM public.archive_availability "
            "WHERE state='expired' AND source_rows_removed AND NOT aggregates_refreshed "
            "ORDER BY start_at,availability_id LIMIT 64"
        )
        for row in rows:
            await self.maintenance.refresh(
                row["source_table"], row["room"], row["start_at"], row["end_at"]
            )
            await conn.execute(
                "UPDATE public.archive_availability SET aggregates_refreshed=TRUE,"
                "aggregates_refreshed_at=clock_timestamp(),updated_at=clock_timestamp() "
                "WHERE availability_id=$1 AND state='expired' AND source_rows_removed",
                row["availability_id"],
            )

    async def _refresh_expired_interval(self, interval: ExpiredInterval, now: datetime) -> None:
        # Refresh is deliberately outside the source DELETE transaction: some
        # native continuous aggregates require a top-level refresh call. The
        # expired availability marker already blocks reads if refresh fails.
        await self.maintenance.refresh(
            interval.source_table, interval.room, interval.start_at, interval.end_at
        )
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.archive_availability SET aggregates_refreshed=TRUE,"
                "aggregates_refreshed_at=$5,updated_at=$5 WHERE state='expired' "
                "AND source_rows_removed AND source_table=$1 AND room=$2 "
                "AND start_at=$3 AND end_at=$4",
                interval.source_table, interval.room, interval.start_at, interval.end_at, now,
            )

    async def _bootstrap_cursor(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        descriptor: SourceStream,
        binding: StreamBinding,
        identity_index: _IdentityRoomIndex,
        room: str,
        cutoff: datetime,
    ) -> None:
        room_sql, room_args = self._room_expression(
            descriptor, binding, identity_index, room, None, None, first_arg=3, alias="m"
        )
        if room_sql is None:
            return
        time_column = self._sql_identifier(binding.time_column)
        cutoff_index = 3 + len(room_args)
        relation = self._sql_identifier(descriptor.name)
        await conn.execute(
            f"INSERT INTO public.archive_expiry_cursor(source_table,room,processed_before) "
            f"VALUES($1,$2,COALESCE((SELECT min(m.{time_column}) FROM public.{relation} m "
            f"WHERE {room_sql} AND m.{time_column}<${cutoff_index}),${cutoff_index})) "
            f"ON CONFLICT(source_table,room) DO NOTHING",
            descriptor.name, room, *room_args, cutoff,
        )

    def _room_expression(
        self,
        descriptor: SourceStream,
        binding: StreamBinding,
        identity_index: _IdentityRoomIndex,
        room: str,
        start: datetime | None,
        end: datetime | None,
        *,
        first_arg: int = 1,
        alias: str = "m",
    ) -> tuple[str | None, tuple[Any, ...]]:
        """Return a typed, parameterized room predicate with a caller offset."""
        if alias not in ("m", "a"):
            raise ValueError("untrusted SQL alias")
        if descriptor.name in IDENTITY_STREAMS:
            key = "sensor_id"
            ids = (
                identity_index.identities_for_room(descriptor.name, room)
                if start is None or end is None
                else identity_index.ids_for(descriptor.name, room, start, end)
            )
            if not ids:
                return None, ()
            column = self._sql_identifier(key)
            return f'{alias}.{column}=ANY(${first_arg}::bigint[])', (sorted(ids),)
        room_column = binding.room_column
        if not room_column:
            return None, ()
        column = self._sql_identifier(str(room_column))
        return f'{alias}.{column}=${first_arg}', (room,)

    def _room_predicate(
        self,
        descriptor: SourceStream,
        binding: StreamBinding,
        identity_index: _IdentityRoomIndex,
        room: str,
        start: datetime,
        end: datetime,
    ) -> tuple[str | None, tuple[Any, ...]]:
        return self._room_expression(
            descriptor, binding, identity_index, room, start, end, first_arg=3, alias="m"
        )

    async def _protection_intervals(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        source_table: str,
        room: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[datetime], list[datetime]]:
        """Calendar phases, archive windows and captured scopes protected in this slice."""
        rows = await conn.fetch(
            "SELECT (e.start_date::timestamp AT TIME ZONE 'America/Toronto') AS start_at,"
            "(((COALESCE(e.end_date,e.start_date))+1)::timestamp AT TIME ZONE 'America/Toronto') AS end_at "
            "FROM public.calendar_event e WHERE e.grow_plan_id IS NOT NULL AND e.deleted_at IS NULL "
            "AND e.location=$1 AND e.start_date <= ($3::timestamptz AT TIME ZONE 'America/Toronto')::date "
            "AND ((COALESCE(e.end_date,e.start_date))+1) > ($2::timestamptz AT TIME ZONE 'America/Toronto')::date "
            "UNION ALL SELECT w.start_at,w.end_at FROM public.archive_window w WHERE w.room=$1 "
            "AND w.start_at<$3 AND w.end_at>$2 "
            "UNION ALL SELECT a.start_at,a.end_at FROM public.archive_availability a "
            "WHERE a.state='captured' AND a.source_table=$4 AND (a.room IS NULL OR a.room=$1) "
            "AND a.start_at<$3 AND a.end_at>$2",
            room, start, end, source_table,
        )
        return [row["start_at"] for row in rows], [row["end_at"] for row in rows]

    async def _expire_pair(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        descriptor: SourceStream,
        binding: StreamBinding,
        identity_index: _IdentityRoomIndex,
        room: str,
        cutoff: datetime,
        budget: int,
        expired: list[ExpiredInterval],
        held: list[HeldScope],
        now: datetime,
    ) -> int:
        anchor_relation = await prepare_retention_anchors(conn, descriptor, cutoff)
        deleted_total = 0
        for _ in range(EXPIRY_WINDOWS_PER_PAIR):
            cursor = await conn.fetchrow(
                "SELECT processed_before FROM public.archive_expiry_cursor "
                "WHERE source_table=$1 AND room=$2",
                descriptor.name, room,
            )
            if cursor is None:
                return deleted_total
            window_start = cursor["processed_before"]
            if window_start >= cutoff:
                return deleted_total
            window_end = min(
                window_start + EXPIRY_DELETE_SLICE * EXPIRY_SLICES_PER_PAIR, cutoff
            )
            boundaries = identity_index.boundaries(descriptor.name, window_start, window_end)
            if len(boundaries) > MAX_WINDOW_BOUNDARIES:
                raise ArchiveError("archive_expiry_window_ambiguous")
            points = (window_start, *boundaries, window_end)
            slice_count = 0
            for left, right in zip(points, points[1:]):
                slice_start = left
                while slice_start < right and slice_count < EXPIRY_SLICES_PER_PAIR:
                    if deleted_total >= budget or self._freshness_paused():
                        return deleted_total
                    slice_end = min(slice_start + EXPIRY_DELETE_SLICE, right)
                    deleted_slice = await self._delete_slices(
                        conn, descriptor, binding, identity_index, room,
                        slice_start, slice_end, cutoff, anchor_relation,
                        budget - deleted_total, expired, now,
                    )
                    deleted_total += deleted_slice
                    eligible_left = await self._eligible_rows_exist(
                        conn, descriptor, binding, identity_index, room, slice_start, slice_end,
                        cutoff, anchor_relation,
                    )
                    if eligible_left:
                        return deleted_total
                    if deleted_slice == 0 and await self._present_rows_exist(
                        conn, descriptor, binding, identity_index, room, slice_start, slice_end
                    ):
                        held.append(HeldScope(
                            "nongrow", f"{descriptor.name}:{room}", "protected_or_anchor_rows_retained"
                        ))
                    advanced = await conn.execute(
                        "UPDATE public.archive_expiry_cursor SET processed_before=$3,"
                        "updated_at=$4 WHERE source_table=$1 AND room=$2 AND processed_before=$5",
                        descriptor.name, room, slice_end, now, slice_start,
                    )
                    if advanced.endswith("0"):
                        logger.warning("expiry cursor lost a race for %s/%s", descriptor.name, room)
                        return deleted_total
                    slice_start = slice_end
                    slice_count += 1
                if slice_count >= EXPIRY_SLICES_PER_PAIR:
                    break
            if self._freshness_paused():
                return deleted_total
        return deleted_total

    async def _delete_slices(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        descriptor: SourceStream,
        binding: StreamBinding,
        identity_index: _IdentityRoomIndex,
        room: str,
        start: datetime,
        end: datetime,
        cutoff: datetime,
        anchor_relation: str,
        budget: int,
        expired: list[ExpiredInterval],
        now: datetime,
    ) -> int:
        room_sql, room_args = self._room_expression(
            descriptor, binding, identity_index, room, start, end, first_arg=3, alias="m"
        )
        if room_sql is None or budget <= 0:
            return 0
        protected_start, protected_end = await self._protection_intervals(
            conn, descriptor.name, room, start, end
        )
        count = len(room_args)
        protected_start_arg = count + 3
        protected_end_arg = count + 4
        source_arg = count + 5
        room_arg = count + 6
        cutoff_arg = count + 7
        limit_arg = count + 8
        relation = self._sql_identifier(descriptor.name)
        time_column = self._sql_identifier(binding.time_column)
        removable = removable_observation_sql(
            descriptor, alias="m", cutoff=f"${cutoff_arg}", anchor_relation=anchor_relation
        )
        ordering = ",".join(
            f'm.{self._sql_identifier(column)} ASC NULLS FIRST'
            for column in descriptor.stable_ordering
            if column != descriptor.time_column
        )
        order_by = f"m.{time_column} ASC" + (f",{ordering}" if ordering else "")
        await self.deletion.decompress_compressed_chunks(conn, descriptor.name, start, end)
        result = await conn.fetchrow(
            "WITH candidates AS MATERIALIZED ("
            f"SELECT m.tableoid,m.ctid,m.{time_column} AS observed,"
            f"octet_length(record_send(m))::bigint AS payload_bytes FROM public.{relation} m "
            f"WHERE m.{time_column}>=$1 AND m.{time_column}<$2 AND {room_sql} AND {removable} "
            f"AND NOT EXISTS (SELECT 1 FROM unnest(${protected_start_arg}::timestamptz[],"
            f"${protected_end_arg}::timestamptz[]) AS p(s,e) "
            f"WHERE m.{time_column}>=p.s AND m.{time_column}<p.e) "
            f"AND NOT EXISTS (SELECT 1 FROM public.archive_availability a "
            f"WHERE a.state='captured' AND a.source_table=${source_arg} "
            f"AND (a.room IS NULL OR a.room=${room_arg}) "
            f"AND m.{time_column}>=a.start_at AND m.{time_column}<a.end_at) "
            f"ORDER BY {order_by} LIMIT ${limit_arg}), bounded AS MATERIALIZED ("
            "SELECT tableoid,ctid,sum(payload_bytes) OVER(ORDER BY observed,tableoid,ctid) AS bytes "
            "FROM candidates), chosen AS MATERIALIZED (SELECT tableoid,ctid FROM bounded "
            f"WHERE bytes<={PURGE_BATCH_BYTES}), deleted AS ("
            f"DELETE FROM public.{relation} m USING chosen c "
            "WHERE m.tableoid=c.tableoid AND m.ctid=c.ctid "
            f"RETURNING m.{time_column} AS observed) "
            "SELECT count(*) AS rows,min(observed) AS first_at,max(observed) AS last_at,"
            "(SELECT count(*) FROM candidates) AS candidates FROM deleted",
            start, end, *room_args, protected_start, protected_end, descriptor.name, room, cutoff,
            min(budget, PURGE_BATCH_ROWS),
        )
        deleted = int(result["rows"] or 0)
        if deleted == 0 and result["candidates"]:
            raise ArchiveError("archive_expiry_row_too_large")
        if deleted <= 0:
            return 0
        interval_start = result["first_at"]
        interval_end = result["last_at"] + timedelta(microseconds=1)
        await conn.execute(
            "INSERT INTO public.archive_availability(dataset_id,revision,source_table,room,"
            "start_at,end_at,state,source_rows_removed,source_removed_at,aggregates_refreshed) "
            "VALUES(NULL,NULL,$1,$2,$3,$4,'expired',TRUE,$5,FALSE) "
            "ON CONFLICT(dataset_id,revision,source_table,room,start_at,end_at) "
            "DO UPDATE SET source_rows_removed=TRUE,source_removed_at=EXCLUDED.source_removed_at,"
            "aggregates_refreshed=FALSE,aggregates_refreshed_at=NULL,updated_at=EXCLUDED.updated_at",
            descriptor.name, room, interval_start, interval_end, now,
        )
        expired.append(ExpiredInterval(descriptor.name, room, interval_start, interval_end, deleted))
        return deleted

    async def _eligible_rows_exist(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        descriptor: SourceStream,
        binding: StreamBinding,
        identity_index: _IdentityRoomIndex,
        room: str,
        start: datetime,
        end: datetime,
        cutoff: datetime,
        anchor_relation: str,
    ) -> bool:
        room_sql, room_args = self._room_expression(
            descriptor, binding, identity_index, room, start, end, first_arg=3, alias="m"
        )
        if room_sql is None:
            return False
        protected_start, protected_end = await self._protection_intervals(
            conn, descriptor.name, room, start, end
        )
        count = len(room_args)
        removable = removable_observation_sql(
            descriptor, alias="m", cutoff=f"${count + 7}", anchor_relation=anchor_relation
        )
        time_column = self._sql_identifier(binding.time_column)
        exists = await conn.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM public.{self._sql_identifier(descriptor.name)} m "
            f"WHERE m.{time_column}>=$1 AND m.{time_column}<$2 AND {room_sql} AND {removable} "
            f"AND NOT EXISTS (SELECT 1 FROM unnest(${count + 3}::timestamptz[],"
            f"${count + 4}::timestamptz[]) AS p(s,e) "
            f"WHERE m.{time_column}>=p.s AND m.{time_column}<p.e) "
            f"AND NOT EXISTS (SELECT 1 FROM public.archive_availability a "
            f"WHERE a.state='captured' AND a.source_table=${count + 5} "
            f"AND (a.room IS NULL OR a.room=${count + 6}) "
            f"AND m.{time_column}>=a.start_at AND m.{time_column}<a.end_at) LIMIT 1)",
            start, end, *room_args, protected_start, protected_end, descriptor.name, room, cutoff,
        )
        return bool(exists)

    async def _present_rows_exist(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        descriptor: SourceStream,
        binding: StreamBinding,
        identity_index: _IdentityRoomIndex,
        room: str,
        start: datetime,
        end: datetime,
    ) -> bool:
        room_sql, room_args = self._room_expression(
            descriptor, binding, identity_index, room, start, end, first_arg=3, alias="m"
        )
        if room_sql is None:
            return False
        time_column = self._sql_identifier(binding.time_column)
        exists = await conn.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM public.{self._sql_identifier(descriptor.name)} m "
            f"WHERE m.{time_column}>=$1 AND m.{time_column}<$2 AND {room_sql} LIMIT 1)",
            start, end, *room_args,
        )
        return bool(exists)

    async def reset_expiry_cursor(self, source_table: str, room: str, before: datetime) -> None:
        """Reopen the forward expiry frontier after a corrected/released scope."""
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("cursor reset bound must be aware")
        if source_table not in {stream.name for stream in self._source_catalog().defined_streams()}:
            raise ValueError("unknown source stream")
        async with self.pool.acquire() as conn, conn.transaction():
            await self._lock_rooms(conn, (room,))
            updated = await conn.execute(
                "UPDATE public.archive_expiry_cursor SET processed_before=$3,updated_at=clock_timestamp() "
                "WHERE source_table=$1 AND room=$2 AND processed_before>$3",
                source_table, room, before.astimezone(UTC),
            )
        if updated.endswith("0"):
            logger.info("expiry cursor for %s/%s already at or before reset bound", source_table, room)


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
                expected = toronto_midnight(
                    add_calendar_months(dataset["original_drying_end"] + timedelta(days=1), 12)
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
                quarter_start, quarter_end = quarter_bounds(year, quarter)
                if dataset["available_to"] is None or dataset["available_to"] < quarter_end:
                    raise ArchiveError("archive_veg_quarter_incomplete")
                rows_before = retention_cutoffs(now).local_expiry_before
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
