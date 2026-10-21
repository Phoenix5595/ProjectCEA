from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from shared.grow_lifecycle import (
    CALENDAR_TZ,
    add_calendar_months,
    retention_cutoffs,
    toronto_midnight,
)

from .lifecycle_calendar import (
    latest_quarter_boundary,
    previous_quarter,
    quarter_bounds,
    quarter_of,
    quarter_start_date,
)
from .lifecycle_constants import VEG_ROOM, ZIP_DUE_MONTHS
from .lifecycle_types import HeldScope, PlannedJob, _IdentityRoomIndex
from .membership import CaptureWindow, load_windows, selection_sql, window_arrays
from .models import JobKind
from .schema import SourceStream
from .streams import StreamBinding


class PlanningMixin:
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

