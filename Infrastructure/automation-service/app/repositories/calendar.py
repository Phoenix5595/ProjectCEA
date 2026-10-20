"""Calendar events, grow plans, and sync metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
from typing import Any
import uuid
from zoneinfo import ZoneInfo

import asyncpg

from app.calendar.flower_grow_plan import (
    GROW_PLAN_PHASE_TYPES,
    FlowerGrowPlanInput,
    build_flower_grow_plan,
    phase_to_metadata,
)
from app.repositories.base import BaseRepository, logger
from shared.grow_lifecycle import (
    GrowLifecycleInputError,
    GrowWindow,
    add_calendar_months,
    calendar_windows,
    toronto_midnight,
)

LOCAL_TZ = ZoneInfo("America/Toronto")
UTC = UTC

DEFAULT_LIMIT = 500
MAX_LIMIT = 2000


_CURRENT_RUN_SQL = (
    "SELECT grow_plan_id, MIN(start_date) AS run_start, "
    "MAX(COALESCE(end_date, start_date)) AS run_end "
    "FROM calendar_event "
    "WHERE deleted_at IS NULL "
    "AND location = $1 "
    "AND grow_plan_id IS NOT NULL "
    "AND event_type = ANY($2::text[]) "
    "GROUP BY grow_plan_id "
    "HAVING MIN(start_date) <= $3 "
    "AND MAX(COALESCE(end_date, start_date)) >= $3 "
    "ORDER BY run_start, grow_plan_id "
    "LIMIT 2"
)


class CalendarMutationError(Exception):
    """A deterministic Calendar/lifecycle mutation rejection."""

    def __init__(self, code: str, status_code: int = 409, detail: Any = None) -> None:
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


class _RetryRoomLocks(Exception):
    """A concurrent Calendar edit changed the room set before it was locked."""


@dataclass(frozen=True, slots=True)
class GrowPlanCurrentRun:
    location: str
    grow_plan_id: uuid.UUID
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class GrowLifecycle:
    grow_plan_id: uuid.UUID
    crop_batch_id: int
    state: str
    calendar_revision: int
    original_drying_end: date | None
    first_closed_at: datetime | None
    closed_at: datetime | None
    anniversary_at: datetime | None
    dataset_id: uuid.UUID | None
    data_revision: int | None
    published_revision: int | None


@dataclass(frozen=True, slots=True)
class GrowClosePreview:
    lifecycle: GrowLifecycle
    revision: int
    phase_windows: tuple[dict[str, Any], ...]
    drying_end: date | None
    affected_counts: dict[str, Any]
    gaps: tuple[dict[str, Any], ...]
    scope_unresolved: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]
    newly_included: dict[str, Any]
    released: dict[str, Any]
    unavailable: tuple[dict[str, Any], ...]


_REQUIRED_PHASE_END_TYPES = frozenset(
    {"bed_veg", "flip_to_flower", "flower_stretch", "flower_bulk", "flower_ripen", "drying"}
)
_FLOWER_DATE_TYPES = frozenset({"flip_to_flower", "flower_stretch", "flower_bulk", "flower_ripen"})

_ROOM_SOURCE_STREAMS = (
    ("automation_state", '"time"', "location", False),
    ("effective_setpoints", '"time"', "location", False),
    ("control_history", '"time"', "location", False),
    ("relay_observation", '"time"', "location", True),
    ("monitoring_room_photoperiod", '"time"', "location", False),
    ("monitoring_photoperiod_coverage", '"time"', "location", False),
    ("mode_transition_history", '"time"', "location", False),
    ("pid_parameter_history", '"time"', "location", False),
    ("config_versions", "timestamp", "location", True),
)
_IDENTITY_SOURCE_STREAMS = (("measurement", '"time"', "sensor_id", "sensor_id"),)
_OPTIONAL_SOURCE_STREAMS = (
    "light_state_history",
    "pid_adjustments",
    "setpoint_history",
    "rollout_events",
)


class GrowPlanCurrentRunAmbiguousError(Exception):
    """Multiple canonical grow-plan groups span today in the same room."""


class CalendarRepository(BaseRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        super().__init__(pool)

    @staticmethod
    @asynccontextmanager
    async def _mutation_transaction(conn: asyncpg.Connection):
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock_shared(hashtextextended('sj-calendar-sync-connection',0))"
            )
            yield

    @staticmethod
    async def _lock_rooms(conn: asyncpg.Connection, rooms: Sequence[str]) -> None:
        for room in sorted({value for value in rooms if value}):
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('sj-history-room:' || $1, 0))",
                room,
            )

    @staticmethod
    async def _lock_grows(
        conn: asyncpg.Connection, grow_ids: Sequence[uuid.UUID], *, require_all: bool = False
    ) -> dict[uuid.UUID, asyncpg.Record]:
        ids = sorted(set(grow_ids), key=str)
        if not ids:
            return {}
        rows = await conn.fetch(
            "SELECT * FROM grow_lifecycle WHERE grow_plan_id = ANY($1::uuid[]) "
            "ORDER BY grow_plan_id FOR UPDATE",
            ids,
        )
        found = {row["grow_plan_id"]: row for row in rows}
        if require_all and set(ids) != set(found):
            raise CalendarMutationError("grow_not_found", 404)
        return found

    @staticmethod
    async def _lock_datasets(
        conn: asyncpg.Connection, grow_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, asyncpg.Record]:
        ids = sorted(set(grow_ids), key=str)
        if not ids:
            return {}
        rows = await conn.fetch(
            "SELECT * FROM archive_dataset WHERE grow_plan_id = ANY($1::uuid[]) "
            "ORDER BY dataset_id FOR UPDATE",
            ids,
        )
        return {row["grow_plan_id"]: row for row in rows}

    @staticmethod
    def _phase_windows(events: Sequence[Mapping[str, Any]]) -> tuple[GrowWindow, ...]:
        try:
            return calendar_windows(events)
        except GrowLifecycleInputError as error:
            raise CalendarMutationError("grow_dates_invalid", 409, str(error)) from error

    @staticmethod
    async def _active_phase_events(
        conn: asyncpg.Connection, grow_plan_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            "SELECT * FROM calendar_event WHERE grow_plan_id=$1 AND deleted_at IS NULL "
            "AND event_type=ANY($2::text[]) ORDER BY start_date,id",
            grow_plan_id,
            sorted(GROW_PLAN_PHASE_TYPES),
        )
        return [dict(row) for row in rows]

    @classmethod
    async def _append_archive_windows(
        cls,
        conn: asyncpg.Connection,
        dataset_id: uuid.UUID,
        calendar_revision: int,
        grow_plan_id: uuid.UUID,
    ) -> None:
        events = await cls._active_phase_events(conn, grow_plan_id)
        windows = cls._phase_windows(events)
        for window in windows:
            await conn.execute(
                "INSERT INTO archive_window "
                "(dataset_id,calendar_revision,event_id,phase_type,room,start_at,end_at) "
                "VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT "
                "(dataset_id,calendar_revision,event_id) DO UPDATE SET "
                "phase_type=EXCLUDED.phase_type,room=EXCLUDED.room,"
                "start_at=EXCLUDED.start_at,end_at=EXCLUDED.end_at",
                dataset_id,
                calendar_revision,
                window.event_id,
                window.phase_type,
                window.room,
                window.start_at,
                window.end_at,
            )
        flower_windows = [window for window in windows if window.room == "Flower Room"]
        available_from = min((window.start_at for window in flower_windows), default=None)
        available_to = max((window.end_at for window in flower_windows), default=None)
        await conn.execute(
            "UPDATE archive_dataset SET available_from=$2,available_to=$3,"
            "updated_at=clock_timestamp() WHERE dataset_id=$1",
            dataset_id,
            available_from,
            available_to,
        )

    @staticmethod
    async def _supersede_jobs(conn: asyncpg.Connection, dataset_ids: Sequence[uuid.UUID]) -> None:
        ids = sorted(set(dataset_ids), key=str)
        if not ids:
            return
        await conn.execute(
            "UPDATE archive_job SET state='failed',last_error_code='archive_job_superseded',"
            "lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() "
            "WHERE dataset_id=ANY($1::uuid[]) AND state IN ('pending','running','blocked')",
            ids,
        )

    @classmethod
    async def _advance_membership(
        cls,
        conn: asyncpg.Connection,
        grow_ids: Sequence[uuid.UUID],
        datasets: Mapping[uuid.UUID, asyncpg.Record],
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """Advance Calendar and content fences after a phase membership edit."""
        revisions: dict[uuid.UUID, tuple[int, int]] = {}
        for grow_id in sorted(set(grow_ids), key=str):
            row = await conn.fetchrow(
                "UPDATE grow_lifecycle SET calendar_revision=calendar_revision+1,"
                "updated_at=clock_timestamp() WHERE grow_plan_id=$1 "
                "RETURNING calendar_revision",
                grow_id,
            )
            if row is None:
                raise CalendarMutationError("grow_not_found", 404)
            dataset = datasets.get(grow_id)
            if dataset is None:
                continue
            changed = await conn.fetchrow(
                "UPDATE archive_dataset SET calendar_revision=$2,data_revision=data_revision+1,"
                "state='dirty',last_error_code=NULL,updated_at=clock_timestamp() "
                "WHERE dataset_id=$1 RETURNING dataset_id,calendar_revision,data_revision",
                dataset["dataset_id"],
                row["calendar_revision"],
            )
            if changed is None:
                raise RuntimeError("locked archive dataset disappeared during Calendar mutation")
            revisions[grow_id] = (
                changed["calendar_revision"],
                changed["data_revision"],
            )
        await cls._supersede_jobs(conn, [datasets[grow_id]["dataset_id"] for grow_id in revisions])
        for grow_id, (calendar_revision, _) in revisions.items():
            await cls._append_archive_windows(
                conn, datasets[grow_id]["dataset_id"], calendar_revision, grow_id
            )
        return revisions

    @classmethod
    async def _advance_calendar_fence(
        cls,
        conn: asyncpg.Connection,
        grow_id: uuid.UUID,
        datasets: Mapping[uuid.UUID, asyncpg.Record],
    ) -> tuple[int, int | None]:
        row = await conn.fetchrow(
            "UPDATE grow_lifecycle SET calendar_revision=calendar_revision+1,"
            "updated_at=clock_timestamp() WHERE grow_plan_id=$1 RETURNING calendar_revision",
            grow_id,
        )
        if row is None:
            raise CalendarMutationError("grow_not_found", 404)
        dataset = datasets.get(grow_id)
        if dataset is None:
            return row["calendar_revision"], None
        changed = await conn.fetchrow(
            "UPDATE archive_dataset SET calendar_revision=$2,updated_at=clock_timestamp() "
            "WHERE dataset_id=$1 RETURNING dataset_id,calendar_revision,data_revision",
            dataset["dataset_id"],
            row["calendar_revision"],
        )
        if changed is None:
            raise RuntimeError("locked archive dataset disappeared during lifecycle transition")
        await cls._supersede_jobs(conn, [dataset["dataset_id"]])
        await cls._append_archive_windows(
            conn, dataset["dataset_id"], changed["calendar_revision"], grow_id
        )
        return changed["calendar_revision"], changed["data_revision"]

    @staticmethod
    async def _overlapping_grow_ids(
        conn: asyncpg.Connection,
        windows: Sequence[GrowWindow],
        *,
        exclude_event_ids: Sequence[int] = (),
    ) -> set[uuid.UUID]:
        ids: set[uuid.UUID] = set()
        for window in windows:
            rows = await conn.fetch(
                "SELECT DISTINCT grow_plan_id FROM calendar_event "
                "WHERE deleted_at IS NULL AND location=$1 "
                "AND event_type=ANY($2::text[]) "
                "AND start_date <= $3 AND COALESCE(end_date,start_date) >= $4 "
                "AND grow_plan_id IS NOT NULL "
                "AND NOT (id=ANY($5::integer[]))",
                window.room,
                sorted(GROW_PLAN_PHASE_TYPES),
                window.end_date or window.start_date,
                window.start_date,
                list(exclude_event_ids),
            )
            ids.update(row["grow_plan_id"] for row in rows)
        return ids

    @staticmethod
    async def _assert_no_overlaps(
        conn: asyncpg.Connection,
        windows: Sequence[GrowWindow],
        grow_plan_id: uuid.UUID | None,
        *,
        exclude_event_ids: Sequence[int] = (),
    ) -> None:
        for window in windows:
            rows = await conn.fetch(
                "SELECT id,grow_plan_id FROM calendar_event "
                "WHERE deleted_at IS NULL AND location=$1 "
                "AND event_type=ANY($2::text[]) "
                "AND start_date <= $3 AND COALESCE(end_date,start_date) >= $4 "
                "AND NOT (id=ANY($5::integer[]))",
                window.room,
                sorted(GROW_PLAN_PHASE_TYPES),
                window.end_date or window.start_date,
                window.start_date,
                list(exclude_event_ids),
            )
            for row in rows:
                if grow_plan_id is not None and row["grow_plan_id"] == grow_plan_id:
                    continue
                raise CalendarMutationError(
                    "grow_overlap",
                    409,
                    {
                        "event_id": row["id"],
                        "grow_plan_id": (
                            str(row["grow_plan_id"]) if row["grow_plan_id"] is not None else None
                        ),
                    },
                )

    @classmethod
    async def _lifecycle(cls, conn: asyncpg.Connection, grow_plan_id: uuid.UUID) -> GrowLifecycle:
        row = await conn.fetchrow(
            "SELECT g.grow_plan_id,g.crop_batch_id,g.state,g.calendar_revision,"
            "g.original_drying_end,g.first_closed_at,g.closed_at,d.anniversary_at,"
            "d.dataset_id,d.data_revision,d.published_revision "
            "FROM grow_lifecycle g LEFT JOIN archive_dataset d USING(grow_plan_id) "
            "WHERE g.grow_plan_id=$1",
            grow_plan_id,
        )
        if row is None:
            raise CalendarMutationError("grow_not_found", 404)
        return GrowLifecycle(**dict(row))

    @staticmethod
    async def _enqueue_lifecycle_job(
        conn: asyncpg.Connection,
        dataset_id: uuid.UUID,
        kind: str,
        data_revision: int,
        calendar_revision: int,
    ) -> None:
        await conn.execute(
            "INSERT INTO archive_job(dataset_id,kind,requested_revision,state,checkpoint) "
            "VALUES($1::uuid,$2::text,$3::bigint,'pending',jsonb_build_object("
            "'calendar_revision',$4::bigint,'data_revision',$3::bigint,'required_closed',true,'progress','{}'::jsonb)) "
            "ON CONFLICT(dataset_id,kind,requested_revision) "
            "WHERE state IN ('pending','running','blocked') "
            "DO UPDATE SET updated_at=clock_timestamp()",
            dataset_id,
            kind,
            data_revision,
            calendar_revision,
        )

    async def list_room_profiles(self) -> list[dict[str, Any]]:
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM calendar_room_profile WHERE enabled = true ORDER BY sort_order"
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to list room profiles: {e}")
            return []

    async def flower_calendar_mode_transitions_enabled(self) -> bool:
        try:
            async with self.pool.acquire() as conn:
                value = await conn.fetchval(
                    "SELECT calendar_mode_transitions_enabled FROM calendar_room_profile "
                    "WHERE location = 'Flower Room'"
                )
                return value is not False
        except Exception as e:
            logger.error(f"Failed to read Flower calendar mode transition setting: {e}")
            return True

    async def set_flower_calendar_mode_transitions_enabled(
        self, enabled: bool
    ) -> dict[str, Any] | None:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "UPDATE calendar_room_profile SET calendar_mode_transitions_enabled = $1, "
                    "updated_at = NOW() WHERE location = 'Flower Room' RETURNING location, "
                    "calendar_mode_transitions_enabled",
                    enabled,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to update Flower calendar mode transition setting: {e}")
            return None

    async def get_event(
        self, event_id: int, include_deleted: bool = False
    ) -> dict[str, Any] | None:
        clause = "" if include_deleted else "AND deleted_at IS NULL"
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT * FROM calendar_event WHERE id = $1 {clause}",
                    event_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get event {event_id}: {e}")
            return None

    async def list_events(
        self,
        from_date: date,
        to_date: date,
        location: str | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[dict[str, Any]], str | None]:
        limit = min(max(1, limit), MAX_LIMIT)
        params: list[Any] = [from_date, to_date]
        clauses = [
            "e.deleted_at IS NULL" if not include_deleted else "TRUE",
            "e.start_date <= $2",
            "COALESCE(e.end_date, e.start_date) >= $1",
        ]
        if location:
            params.append(location)
            clauses.append(f"e.location = ${len(params)}")
        cursor_start: date | None = None
        cursor_id: int | None = None
        if cursor:
            try:
                parts = cursor.split(",", 1)
                cursor_start = date.fromisoformat(parts[0])
                cursor_id = int(parts[1])
            except (ValueError, IndexError):
                pass
        if cursor_start is not None and cursor_id is not None:
            params.extend([cursor_start, cursor_id])
            i, j = len(params) - 1, len(params)
            clauses.append(f"(e.start_date, e.id) > (${i}, ${j})")

        params.append(limit + 1)
        lim = len(params)
        sql = f"""
            SELECT e.* FROM calendar_event e
            WHERE {" AND ".join(clauses)}
            ORDER BY e.start_date, e.id
            LIMIT ${lim}
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                items = [dict(r) for r in rows[:limit]]
                next_cursor = None
                if len(rows) > limit:
                    last = items[-1]
                    next_cursor = f"{last['start_date'].isoformat()},{last['id']}"
                return items, next_cursor
        except Exception as e:
            logger.error(f"Failed to list events: {e}")
            return [], None

    async def list_mode_transitions(
        self,
        from_date: date,
        to_date: date,
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [from_date, to_date]
        loc_clause = ""
        if location:
            params.append(location)
            loc_clause = f"AND h.location = ${len(params)}"
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT h.*,
                           rm_old.name AS old_mode_name,
                           rm_new.name AS new_mode_name,
                           fs_old.name AS old_submode_name,
                           fs_new.name AS new_submode_name
                    FROM mode_transition_history h
                    LEFT JOIN room_modes rm_old ON rm_old.id = h.old_mode_id
                    LEFT JOIN room_modes rm_new ON rm_new.id = h.new_mode_id
                    LEFT JOIN flower_submodes fs_old ON fs_old.id = h.old_submode_id
                    LEFT JOIN flower_submodes fs_new ON fs_new.id = h.new_submode_id
                    WHERE h."time"::date >= $1
                      AND h."time"::date <= $2
                      {loc_clause}
                    ORDER BY h."time" DESC
                    """,
                    *params,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to list mode transitions: {e}")
            return []

    @classmethod
    def _event_window(cls, event: Mapping[str, Any], grow_plan_id: uuid.UUID | None) -> GrowWindow:
        window_event = dict(event)
        window_event["id"] = int(window_event.get("id", -1))
        window_event["grow_plan_id"] = grow_plan_id or uuid.UUID(int=0)
        return cls._phase_windows([window_event])[0]

    @staticmethod
    def _validate_date_range(start_date: Any, end_date: Any) -> None:
        if (
            isinstance(start_date, datetime)
            or not isinstance(start_date, date)
            or (
                end_date is not None
                and (isinstance(end_date, datetime) or not isinstance(end_date, date))
            )
            or (end_date is not None and end_date < start_date)
        ):
            raise CalendarMutationError("grow_dates_invalid", 409)

    async def create_event(self, data: dict[str, Any]) -> dict[str, Any]:
        grow_plan_id = data.get("grow_plan_id")
        if grow_plan_id is not None and not isinstance(grow_plan_id, uuid.UUID):
            grow_plan_id = uuid.UUID(str(grow_plan_id))
        event_type = data["event_type"]
        if grow_plan_id is not None and event_type not in GROW_PLAN_PHASE_TYPES:
            raise CalendarMutationError(
                "grow_dates_invalid", 409, "grow_plan_id can only be assigned to a grow phase"
            )
        start_date = data["start_date"]
        end_date = data.get("end_date")
        self._validate_date_range(start_date, end_date)
        candidate = {
            "id": -1,
            "grow_plan_id": grow_plan_id,
            "event_type": event_type,
            "location": data["location"],
            "start_date": start_date,
            "end_date": end_date,
        }
        windows = (
            [self._event_window(candidate, grow_plan_id)]
            if event_type in GROW_PLAN_PHASE_TYPES
            else []
        )
        ical_uid = (
            f"cea-cal-{data['location'].lower().replace(' ', '-')}-"
            f"{uuid.uuid4().hex[:12]}@siberianjungle.local"
        )
        async with self.pool.acquire() as conn, self._mutation_transaction(conn):
            await self._lock_rooms(conn, [data["location"]])
            grow_ids = await self._overlapping_grow_ids(conn, windows)
            if grow_plan_id is not None:
                grow_ids.add(grow_plan_id)
            grows = await self._lock_grows(conn, list(grow_ids))
            if grow_plan_id is not None:
                target = grows.get(grow_plan_id)
                if target is None:
                    raise CalendarMutationError("grow_not_found", 404)
                if target["state"] == "closed":
                    raise CalendarMutationError("grow_closed")
                requested_batch = data.get("crop_batch_id")
                if requested_batch is not None and requested_batch != target["crop_batch_id"]:
                    raise CalendarMutationError(
                        "grow_dates_invalid", 409, "crop_batch_id must match the selected grow"
                    )
                crop_batch_id = target["crop_batch_id"]
            else:
                crop_batch_id = data.get("crop_batch_id")
            datasets = await self._lock_datasets(conn, list(grow_ids))
            if windows:
                await self._assert_no_overlaps(conn, windows, grow_plan_id)
            row = await conn.fetchrow(
                """
                INSERT INTO calendar_event (
                    location, cluster, event_type, title, start_date, end_date,
                    all_day, notes, metadata, crop_batch_id, grow_plan_id,
                    ical_uid, sync_status, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11,
                    $12, 'pending_push', $13
                )
                RETURNING *
                """,
                data["location"],
                data.get("cluster", "main"),
                event_type,
                data["title"],
                start_date,
                end_date,
                data.get("all_day", True),
                data.get("notes"),
                json.dumps(data.get("metadata") or {}),
                crop_batch_id,
                grow_plan_id,
                ical_uid,
                data.get("created_by"),
            )
            if grow_plan_id is not None:
                await self._advance_membership(conn, [grow_plan_id], datasets)
            return dict(row)

    async def update_event(self, event_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        if "grow_plan_id" in data:
            raise CalendarMutationError(
                "grow_association_required",
                409,
                "Use the dedicated grow-association route to change grow membership",
            )
        if "crop_batch_id" in data:
            raise CalendarMutationError(
                "grow_dates_invalid", 409, "crop_batch_id is derived from grow association"
            )
        update_keys = (
            "title",
            "start_date",
            "end_date",
            "notes",
            "event_type",
            "location",
            "cluster",
        )
        if not any(key in data for key in update_keys) and "metadata" not in data:
            return await self.get_event(event_id)

        for _ in range(4):
            try:
                async with self.pool.acquire() as conn, self._mutation_transaction(conn):
                    initial = await conn.fetchrow(
                        "SELECT * FROM calendar_event WHERE id=$1 AND deleted_at IS NULL", event_id
                    )
                    if initial is None:
                        return None
                    rooms = [initial["location"], data.get("location", initial["location"])]
                    await self._lock_rooms(conn, rooms)
                    current = await conn.fetchrow(
                        "SELECT * FROM calendar_event WHERE id=$1 AND deleted_at IS NULL", event_id
                    )
                    if current is None:
                        return None
                    if current["location"] not in rooms:
                        raise _RetryRoomLocks

                    merged = dict(current)
                    merged.update(data)
                    start_date = merged["start_date"]
                    end_date = merged.get("end_date")
                    self._validate_date_range(start_date, end_date)
                    current_is_phase = current["event_type"] in GROW_PLAN_PHASE_TYPES
                    phase = merged["event_type"] in GROW_PLAN_PHASE_TYPES
                    if current_is_phase and not phase:
                        raise CalendarMutationError(
                            "grow_dates_invalid", 409, "A grow phase must remain a grow phase"
                        )
                    grow_plan_id = current["grow_plan_id"] if phase else None
                    window = self._event_window(merged, grow_plan_id) if phase else None
                    windows = [window] if window else []
                    grow_ids = await self._overlapping_grow_ids(
                        conn, windows, exclude_event_ids=[event_id]
                    )
                    if grow_plan_id is not None:
                        grow_ids.add(grow_plan_id)
                    grows = await self._lock_grows(conn, list(grow_ids))
                    if grow_plan_id is not None:
                        lifecycle = grows.get(grow_plan_id)
                        if lifecycle is None:
                            raise CalendarMutationError("grow_not_found", 404)
                        if lifecycle["state"] == "closed":
                            raise CalendarMutationError("grow_closed")
                    datasets = await self._lock_datasets(conn, list(grow_ids))
                    locked_event = await conn.fetchrow(
                        "SELECT * FROM calendar_event WHERE id=$1 AND deleted_at IS NULL FOR UPDATE",
                        event_id,
                    )
                    if locked_event is None:
                        return None
                    if (
                        locked_event["location"] != current["location"]
                        or locked_event["grow_plan_id"] != current["grow_plan_id"]
                    ):
                        raise _RetryRoomLocks
                    if windows:
                        await self._assert_no_overlaps(
                            conn, windows, grow_plan_id, exclude_event_ids=[event_id]
                        )

                    fields: list[str] = []
                    params: list[Any] = []
                    for key in update_keys:
                        if key in data:
                            params.append(data[key])
                            fields.append(f"{key}=${len(params)}")
                    if "metadata" in data:
                        params.append(json.dumps(data["metadata"]))
                        fields.append(f"metadata=${len(params)}::jsonb")
                    params.append(event_id)
                    fields.extend(["updated_at=clock_timestamp()", "sync_status='pending_push'"])
                    row = await conn.fetchrow(
                        f"UPDATE calendar_event SET {', '.join(fields)} "
                        f"WHERE id=${len(params)} AND deleted_at IS NULL RETURNING *",
                        *params,
                    )
                    if row is None:
                        return None
                    if grow_plan_id is not None:
                        await self._advance_membership(conn, [grow_plan_id], datasets)
                    return dict(row)
            except _RetryRoomLocks:
                continue
        raise CalendarMutationError("calendar_event_conflict", 409)

    async def soft_delete_event(self, event_id: int) -> bool:
        for _ in range(4):
            try:
                async with self.pool.acquire() as conn, self._mutation_transaction(conn):
                    initial = await conn.fetchrow(
                        "SELECT * FROM calendar_event WHERE id=$1 AND deleted_at IS NULL", event_id
                    )
                    if initial is None:
                        return False
                    await self._lock_rooms(conn, [initial["location"]])
                    current = await conn.fetchrow(
                        "SELECT * FROM calendar_event WHERE id=$1 AND deleted_at IS NULL", event_id
                    )
                    if current is None:
                        return False
                    if current["location"] != initial["location"]:
                        raise _RetryRoomLocks
                    grow_id = (
                        current["grow_plan_id"]
                        if current["event_type"] in GROW_PLAN_PHASE_TYPES
                        else None
                    )
                    grow_ids = [grow_id] if grow_id is not None else []
                    grows = await self._lock_grows(conn, grow_ids, require_all=bool(grow_ids))
                    if grow_id is not None and grows[grow_id]["state"] == "closed":
                        raise CalendarMutationError("grow_closed")
                    datasets = await self._lock_datasets(conn, grow_ids)
                    locked_event = await conn.fetchrow(
                        "SELECT * FROM calendar_event WHERE id=$1 AND deleted_at IS NULL FOR UPDATE",
                        event_id,
                    )
                    if locked_event is None:
                        return False
                    if locked_event["location"] != current["location"]:
                        raise _RetryRoomLocks
                    result = await conn.fetchrow(
                        "UPDATE calendar_event SET deleted_at=clock_timestamp(),"
                        "sync_status='pending_delete',updated_at=clock_timestamp() "
                        "WHERE id=$1 AND deleted_at IS NULL RETURNING id",
                        event_id,
                    )
                    if result is None:
                        return False
                    if grow_id is not None:
                        await self._advance_membership(conn, [grow_id], datasets)
                    return True
            except _RetryRoomLocks:
                continue
        raise CalendarMutationError("calendar_event_conflict", 409)

    async def create_flower_grow_plan(
        self,
        inp: FlowerGrowPlanInput,
        idempotency_key: uuid.UUID,
        created_by: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        phases, error = build_flower_grow_plan(inp)
        if error:
            return None, error
        grow_plan_id = uuid.uuid4()
        plan_events = [
            {
                "id": -index,
                "grow_plan_id": grow_plan_id,
                "event_type": phase.event_type,
                "location": phase.location,
                "start_date": phase.start_date,
                "end_date": phase.end_date,
            }
            for index, phase in enumerate(phases, start=1)
        ]
        windows = self._phase_windows(plan_events)
        rooms = [phase.location for phase in phases]
        batch_start = min(phase.start_date for phase in phases)
        batch_end = max(phase.end_date for phase in phases)
        async with self.pool.acquire() as conn, self._mutation_transaction(conn):
            await self._lock_rooms(conn, rooms)
            existing = await conn.fetchrow(
                "SELECT grow_plan_id FROM grow_plan_idempotency "
                "WHERE idempotency_key=$1 AND expires_at>clock_timestamp()",
                idempotency_key,
            )
            if existing is not None:
                existing_id = existing["grow_plan_id"]
                events = await conn.fetch(
                    "SELECT * FROM calendar_event WHERE grow_plan_id=$1 AND deleted_at IS NULL "
                    "ORDER BY start_date,id",
                    existing_id,
                )
                batch_id = await conn.fetchval(
                    "SELECT crop_batch_id FROM grow_lifecycle WHERE grow_plan_id=$1", existing_id
                )
                return {
                    "grow_plan_id": str(existing_id),
                    "crop_batch_id": batch_id,
                    "events": [dict(event) for event in events],
                }, None

            conflicts = await self._overlapping_grow_ids(conn, windows)
            room = await conn.fetchrow("SELECT room_id FROM room WHERE name='Flower Room'")
            if room is None:
                raise CalendarMutationError("grow_dates_invalid", 409, "Flower Room is unavailable")
            batch = await conn.fetchrow(
                "INSERT INTO crop_batch(crop_name,start_date,end_date,room_id) "
                "VALUES($1,$2,$3,$4) RETURNING batch_id",
                inp.crop_name,
                batch_start,
                batch_end,
                room["room_id"],
            )
            await conn.execute(
                "INSERT INTO grow_lifecycle(grow_plan_id,crop_batch_id,state) VALUES($1,$2,'open')",
                grow_plan_id,
                batch["batch_id"],
            )
            grow_ids = set(conflicts)
            grow_ids.add(grow_plan_id)
            grows = await self._lock_grows(conn, list(grow_ids))
            if grow_plan_id not in grows:
                raise RuntimeError("new lifecycle row disappeared before it was locked")
            await self._lock_datasets(conn, list(grow_ids))
            await self._assert_no_overlaps(conn, windows, grow_plan_id)
            created_events: list[dict[str, Any]] = []
            for phase in phases:
                metadata = phase_to_metadata(
                    phase, str(grow_plan_id), inp.environment, inp.crop_name
                )
                uid = (
                    f"cea-cal-{phase.location.lower().replace(' ', '-')}-"
                    f"{uuid.uuid4().hex[:12]}@siberianjungle.local"
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO calendar_event (
                        location,cluster,event_type,title,start_date,end_date,all_day,metadata,
                        crop_batch_id,grow_plan_id,ical_uid,sync_status,created_by
                    ) VALUES($1,$2,$3,$4,$5,$6,true,$7::jsonb,$8,$9,$10,'pending_push',$11)
                    RETURNING *
                    """,
                    phase.location,
                    phase.cluster,
                    phase.event_type,
                    phase.title,
                    phase.start_date,
                    phase.end_date,
                    json.dumps(metadata),
                    batch["batch_id"],
                    grow_plan_id,
                    uid,
                    created_by,
                )
                created_events.append(dict(row))
            await conn.execute(
                "INSERT INTO grow_plan_idempotency(idempotency_key,grow_plan_id,expires_at) "
                "VALUES($1,$2,clock_timestamp()+interval '24 hours')",
                idempotency_key,
                grow_plan_id,
            )
            return {
                "grow_plan_id": str(grow_plan_id),
                "crop_batch_id": batch["batch_id"],
                "events": created_events,
            }, None

    async def delete_grow_plan(self, grow_plan_id: uuid.UUID) -> list[dict[str, Any]]:
        for _ in range(4):
            try:
                async with self.pool.acquire() as conn, self._mutation_transaction(conn):
                    initial = await conn.fetch(
                        "SELECT * FROM calendar_event WHERE grow_plan_id=$1 AND deleted_at IS NULL "
                        "ORDER BY id",
                        grow_plan_id,
                    )
                    rooms = [row["location"] for row in initial]
                    await self._lock_rooms(conn, rooms)
                    current = await conn.fetch(
                        "SELECT * FROM calendar_event WHERE grow_plan_id=$1 AND deleted_at IS NULL "
                        "ORDER BY id",
                        grow_plan_id,
                    )
                    if any(row["location"] not in rooms for row in current):
                        raise _RetryRoomLocks
                    grows = await self._lock_grows(conn, [grow_plan_id])
                    if not grows:
                        return []
                    if grows[grow_plan_id]["state"] == "closed":
                        raise CalendarMutationError("grow_closed")
                    datasets = await self._lock_datasets(conn, [grow_plan_id])
                    deleted = await conn.fetch(
                        "UPDATE calendar_event SET deleted_at=clock_timestamp(),"
                        "sync_status='pending_delete',updated_at=clock_timestamp() "
                        "WHERE grow_plan_id=$1 AND deleted_at IS NULL RETURNING *",
                        grow_plan_id,
                    )
                    if deleted:
                        await self._advance_membership(conn, [grow_plan_id], datasets)
                    return [dict(row) for row in deleted]
            except _RetryRoomLocks:
                continue
        raise CalendarMutationError("calendar_event_conflict", 409)

    @staticmethod
    def _window_arrays(
        windows: Sequence[GrowWindow],
    ) -> tuple[list[str], list[datetime], list[datetime]]:
        return (
            [window.room for window in windows],
            [window.start_at for window in windows],
            [window.end_at for window in windows],
        )

    @classmethod
    async def _build_close_preview(
        cls,
        conn: asyncpg.Connection,
        lifecycle: GrowLifecycle,
        events: Sequence[Mapping[str, Any]],
        dataset: Mapping[str, Any] | None,
        *,
        now: datetime,
    ) -> GrowClosePreview:
        windows = cls._phase_windows(events)
        archive_windows = tuple(window for window in windows if window.room == "Flower Room")
        new_rooms, new_starts, new_ends = cls._window_arrays(archive_windows)
        dataset_id = dataset["dataset_id"] if dataset else None
        published_revision = dataset["published_revision"] if dataset else None
        prior_windows: list[dict[str, Any]] = []
        if dataset_id is not None:
            prior_windows = [
                dict(row)
                for row in await conn.fetch(
                    "SELECT room,start_at,end_at FROM archive_window "
                    "WHERE dataset_id=$1 AND calendar_revision=$2 AND room='Flower Room' "
                    "ORDER BY start_at,end_at,event_id",
                    dataset_id,
                    dataset["calendar_revision"],
                )
            ]
        old_rooms = [row["room"] for row in prior_windows]
        old_starts = [row["start_at"] for row in prior_windows]
        old_ends = [row["end_at"] for row in prior_windows]

        raw_counts: dict[str, int] = {}
        context_counts: dict[str, int] = {}
        newly_included: dict[str, int] = {}
        released: dict[str, int] = {}
        scope_unresolved: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []

        def selection_sql(alias: str, time_column: str, rooms_param: str) -> str:
            room_array, start_array, end_array = rooms_param.split(",")
            return (
                "EXISTS (SELECT 1 FROM unnest("
                f"${room_array}::text[],${start_array}::timestamptz[],${end_array}::timestamptz[]"
                f") AS w(room,start_at,end_at) WHERE {alias}.{time_column}>=w.start_at "
                f"AND {alias}.{time_column}<w.end_at AND {alias}.location=w.room)"
            )

        source_tables = {
            row["table_name"]
            for row in await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
        }
        new_params: list[Any] = [new_rooms, new_starts, new_ends]
        old_params: list[Any] = [old_rooms, old_starts, old_ends]

        for table, time_column, location_column, null_is_context in _ROOM_SOURCE_STREAMS:
            if table not in source_tables:
                unavailable.append({"source_table": table, "code": "source_stream_absent"})
                continue
            new_predicate = selection_sql("s", time_column, "1,2,3")
            old_predicate = selection_sql("s", time_column, "4,5,6")
            # These identifiers come only from the checked-in stream list above.
            row = await conn.fetchrow(
                f"SELECT count(*) FILTER (WHERE {new_predicate}) AS current_count,"
                f"count(*) FILTER (WHERE {new_predicate} AND NOT ({old_predicate})) AS newly_included,"
                f"count(*) FILTER (WHERE {old_predicate} AND NOT ({new_predicate})) AS released "
                f"FROM public.{table} s WHERE s.{time_column} IS NOT NULL",
                *new_params,
                *old_params,
            )
            raw_counts[table] = int(row["current_count"])
            newly_included[table] = int(row["newly_included"])
            released[table] = int(row["released"])
            if null_is_context:
                context_count = await conn.fetchval(
                    f"SELECT count(*) FROM public.{table} s WHERE s.{time_column} IS NOT NULL "
                    f"AND s.{location_column} IS NULL AND EXISTS (SELECT 1 FROM unnest("
                    "$1::text[],$2::timestamptz[],$3::timestamptz[]) "
                    "AS w(room,start_at,end_at) WHERE s."
                    f"{time_column}>=w.start_at AND s.{time_column}<w.end_at)",
                    new_rooms,
                    new_starts,
                    new_ends,
                )
                context_counts[table] = int(context_count)
        if archive_windows and "mode_transition_history" in source_tables:
            null_time_context = await conn.fetchval(
                "SELECT count(*) FROM mode_transition_history "
                'WHERE "time" IS NULL AND (location=ANY($1::text[]) OR location IS NULL)',
                sorted(set(new_rooms)),
            )
            context_counts["mode_transition_history_null_time"] = int(null_time_context)

        # Registry-backed raw rows need a dated override or a frozen identity
        # snapshot; today's registry placement is not historical evidence.
        for table, time_column, identity_column, identity_key in _IDENTITY_SOURCE_STREAMS:
            if table not in source_tables:
                unavailable.append({"source_table": table, "code": "source_stream_absent"})
                continue
            override_any = (
                "EXISTS (SELECT 1 FROM archive_scope_override o WHERE "
                f"o.source_table='{table}' AND o.source_identity=jsonb_build_object('{identity_key}',s.{identity_column}) "
                f"AND o.start_at<=s.{time_column} AND o.end_at>s.{time_column})"
            )
            override_for_room = (
                "EXISTS (SELECT 1 FROM archive_scope_override o WHERE "
                f"o.source_table='{table}' AND o.source_identity=jsonb_build_object('{identity_key}',s.{identity_column}) "
                f"AND o.room=w.room AND o.start_at<=s.{time_column} AND o.end_at>s.{time_column})"
            )
            frozen_any = (
                "EXISTS (SELECT 1 FROM archive_dataset d "
                "CROSS JOIN LATERAL jsonb_array_elements(COALESCE(d.manifest->'identity_snapshots','[]'::jsonb)) snap "
                f"WHERE snap->>'source_table'='{table}' "
                f"AND snap->'source_identity'=jsonb_build_object('{identity_key}',s.{identity_column}) "
                f"AND (snap->>'start')::timestamptz<=s.{time_column} "
                f"AND (snap->>'end')::timestamptz>s.{time_column})"
            )
            frozen_for_room = (
                "EXISTS (SELECT 1 FROM archive_dataset d "
                "CROSS JOIN LATERAL jsonb_array_elements(COALESCE(d.manifest->'identity_snapshots','[]'::jsonb)) snap "
                f"WHERE snap->>'source_table'='{table}' "
                f"AND snap->'source_identity'=jsonb_build_object('{identity_key}',s.{identity_column}) "
                "AND snap->>'room'=w.room "
                f"AND (snap->>'start')::timestamptz<=s.{time_column} "
                f"AND (snap->>'end')::timestamptz>s.{time_column})"
            )
            new_assignment = (
                f"(CASE WHEN {override_any} THEN {override_for_room} ELSE {frozen_for_room} END)"
            )
            any_assignment = f"({override_any} OR {frozen_any})"
            new_predicate = (
                "EXISTS (SELECT 1 FROM unnest($1::text[],$2::timestamptz[],$3::timestamptz[]) "
                f"AS w(room,start_at,end_at) WHERE s.{time_column}>=w.start_at "
                f"AND s.{time_column}<w.end_at AND ({new_assignment}))"
            )
            old_predicate = (
                "EXISTS (SELECT 1 FROM unnest($4::text[],$5::timestamptz[],$6::timestamptz[]) "
                f"AS w(room,start_at,end_at) WHERE s.{time_column}>=w.start_at "
                f"AND s.{time_column}<w.end_at AND ({new_assignment}))"
            )
            counts = await conn.fetchrow(
                f"SELECT count(*) FILTER (WHERE {new_predicate}) AS current_count,"
                f"count(*) FILTER (WHERE {new_predicate} AND NOT ({old_predicate})) AS newly_included,"
                f"count(*) FILTER (WHERE {old_predicate} AND NOT ({new_predicate})) AS released "
                f"FROM public.{table} s WHERE s.{time_column} IS NOT NULL",
                *new_params,
                *old_params,
            )
            raw_counts[table] = int(counts["current_count"])
            newly_included[table] = int(counts["newly_included"])
            released[table] = int(counts["released"])
            unresolved = await conn.fetch(
                "SELECT s."
                f"{identity_column} AS identity,min(s.{time_column}) AS start_at,"
                f"(max(s.{time_column}) + interval '1 microsecond') AS end_at,count(*) AS row_count "
                f"FROM public.{table} s WHERE s.{time_column} IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM unnest($1::text[],$2::timestamptz[],$3::timestamptz[]) "
                f"AS w(room,start_at,end_at) WHERE s.{time_column}>=w.start_at "
                f"AND s.{time_column}<w.end_at AND NOT ({any_assignment})) "
                f"GROUP BY s.{identity_column} ORDER BY s.{identity_column}",
                new_rooms,
                new_starts,
                new_ends,
            )
            for row in unresolved:
                scope_unresolved.append(
                    {
                        "source_table": table,
                        "source_identity": {identity_key: row["identity"]},
                        "start": row["start_at"].isoformat(),
                        "end": row["end_at"].isoformat(),
                        "row_count": int(row["row_count"]),
                    }
                )
            ambiguous = await conn.fetch(
                "WITH ambiguous_rows AS (SELECT s."
                f"{identity_column} AS identity,s.{time_column} AS event_time "
                f"FROM public.{table} s WHERE s.{time_column} IS NOT NULL "
                f"AND NOT ({override_any}) "
                "AND EXISTS (SELECT 1 FROM unnest($1::text[],$2::timestamptz[],$3::timestamptz[]) "
                f"AS w(room,start_at,end_at) WHERE s.{time_column}>=w.start_at "
                f"AND s.{time_column}<w.end_at) AND ("
                "SELECT count(DISTINCT snap->>'room') FROM archive_dataset d "
                "CROSS JOIN LATERAL jsonb_array_elements(COALESCE(d.manifest->'identity_snapshots','[]'::jsonb)) snap "
                f"WHERE snap->>'source_table'='{table}' "
                f"AND snap->'source_identity'=jsonb_build_object('{identity_key}',s.{identity_column}) "
                f"AND (snap->>'start')::timestamptz<=s.{time_column} "
                f"AND (snap->>'end')::timestamptz>s.{time_column})>1) "
                "SELECT identity,min(event_time) AS start_at,"
                "(max(event_time)+interval '1 microsecond') AS end_at,count(*) AS row_count "
                "FROM ambiguous_rows GROUP BY identity ORDER BY identity",
                new_rooms,
                new_starts,
                new_ends,
            )
            for row in ambiguous:
                scope_unresolved.append(
                    {
                        "source_table": table,
                        "source_identity": {identity_key: row["identity"]},
                        "start": row["start_at"].isoformat(),
                        "end": row["end_at"].isoformat(),
                        "row_count": int(row["row_count"]),
                        "cause": "conflicting_frozen_identity_snapshots",
                    }
                )

        phase_ids = [int(event["id"]) for event in events]
        if phase_ids and "calendar_mode_application" in source_tables:
            count = await conn.fetchval(
                "SELECT count(*) FROM calendar_mode_application WHERE event_id=ANY($1::integer[])",
                phase_ids,
            )
            context_counts["calendar_mode_application"] = int(count)
        elif "calendar_mode_application" not in source_tables:
            unavailable.append(
                {"source_table": "calendar_mode_application", "code": "source_stream_absent"}
            )

        gaps: list[dict[str, Any]] = []
        if archive_windows and await conn.fetchval(
            "SELECT to_regclass('public.archive_availability') IS NOT NULL"
        ):
            rows = await conn.fetch(
                "SELECT source_table,room,start_at,end_at FROM archive_availability a "
                "WHERE state='expired' AND source_rows_removed AND EXISTS ("
                "SELECT 1 FROM unnest($1::text[],$2::timestamptz[],$3::timestamptz[]) "
                "AS w(room,start_at,end_at) WHERE (a.room IS NULL OR a.room=w.room) "
                "AND a.start_at<w.end_at AND a.end_at>w.start_at) "
                "ORDER BY source_table,start_at,end_at",
                new_rooms,
                new_starts,
                new_ends,
            )
            gaps.extend(
                {
                    "source_table": row["source_table"],
                    "room": row["room"],
                    "start_at": row["start_at"].isoformat(),
                    "end_at": row["end_at"].isoformat(),
                    "reason": "history_expired",
                }
                for row in rows
            )

        for table in _OPTIONAL_SOURCE_STREAMS:
            present = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")
            unavailable.append(
                {
                    "source_table": table,
                    "code": "schema_descriptor_unavailable",
                    "present": bool(present),
                }
            )

        conflicts: list[dict[str, Any]] = []
        drying_events = [event for event in events if event["event_type"] == "drying"]
        flower_events = [event for event in events if event["event_type"] in _FLOWER_DATE_TYPES]
        if not drying_events:
            conflicts.append({"code": "grow_dates_invalid", "detail": "A drying phase is required"})
        for event in events:
            if event.get("crop_batch_id") != lifecycle.crop_batch_id:
                conflicts.append(
                    {
                        "code": "grow_dates_invalid",
                        "event_id": event["id"],
                        "detail": "Grow phase crop batch does not match lifecycle linkage",
                    }
                )
        for event in events:
            if event["event_type"] in _REQUIRED_PHASE_END_TYPES and event.get("end_date") is None:
                conflicts.append(
                    {
                        "code": "grow_dates_invalid",
                        "event_id": event["id"],
                        "detail": "Required grow phase needs an explicit end date",
                    }
                )
        for window in windows:
            if window.end_at > now.astimezone(UTC):
                conflicts.append(
                    {
                        "code": "grow_dates_invalid",
                        "event_id": window.event_id,
                        "detail": "The inclusive phase end has not elapsed in Toronto",
                    }
                )
        flower_end = max(
            (event["end_date"] or event["start_date"] for event in flower_events),
            default=None,
        )
        drying_start = min((event["start_date"] for event in drying_events), default=None)
        if flower_end is None:
            conflicts.append(
                {"code": "grow_dates_invalid", "detail": "A flowering phase is required"}
            )
        elif drying_start is not None and drying_start <= flower_end:
            conflicts.append(
                {
                    "code": "grow_dates_invalid",
                    "detail": "Drying must start after the final flowering date",
                }
            )
        drying_end = max(
            (event["end_date"] or event["start_date"] for event in drying_events),
            default=None,
        )
        event_ids = [int(event["id"]) for event in events]
        for window in windows:
            rows = await conn.fetch(
                "SELECT id,grow_plan_id FROM calendar_event WHERE deleted_at IS NULL "
                "AND id<>ALL($1::integer[]) AND location=$2 "
                "AND event_type=ANY($3::text[]) AND start_date<=$4 "
                "AND COALESCE(end_date,start_date)>=$5",
                event_ids,
                window.room,
                sorted(GROW_PLAN_PHASE_TYPES),
                window.end_date or window.start_date,
                window.start_date,
            )
            conflicts.extend(
                {
                    "code": "grow_overlap",
                    "event_id": row["id"],
                    "grow_plan_id": (
                        str(row["grow_plan_id"]) if row["grow_plan_id"] is not None else None
                    ),
                }
                for row in rows
            )

        unique_conflicts: list[dict[str, Any]] = []
        seen_conflicts: set[tuple[Any, ...]] = set()
        for conflict in conflicts:
            if conflict["code"] == "grow_overlap":
                key = (
                    conflict["code"],
                    conflict.get("event_id"),
                    conflict.get("grow_plan_id"),
                )
            else:
                key = (
                    conflict["code"],
                    conflict.get("event_id"),
                    conflict.get("detail"),
                )
            if key not in seen_conflicts:
                seen_conflicts.add(key)
                unique_conflicts.append(conflict)
        conflicts = unique_conflicts

        phase_payload = tuple(
            {
                "event_id": window.event_id,
                "phase_type": window.phase_type,
                "room": window.room,
                "start_at": window.start_at.isoformat(),
                "end_at": window.end_at.isoformat(),
                "start_date": window.start_date.isoformat(),
                "end_date": window.end_date.isoformat() if window.end_date else None,
                "explicit_end": window.explicit_end,
            }
            for window in windows
        )
        zip_counts: dict[str, int] = {}
        zip_new: dict[str, int] = {}
        zip_released: dict[str, int] = {}
        zip_membership_exact = False
        zip_current = False
        manifest_value = dataset["manifest"] if dataset is not None else None
        if isinstance(manifest_value, str):
            manifest_value = json.loads(manifest_value)
        if isinstance(manifest_value, Mapping) and published_revision is not None:
            manifest = dict(manifest_value)
            manifest_phases = manifest.get("phases")
            expected_phases = [
                {
                    key: phase[key]
                    for key in ("event_id", "phase_type", "room", "start_date", "end_date")
                }
                for phase in phase_payload
                if phase["room"] == "Flower Room"
            ]
            actual_phases = (
                [
                    {
                        key: phase.get(key)
                        for key in ("event_id", "phase_type", "room", "start_date", "end_date")
                    }
                    for phase in manifest_phases
                    if isinstance(phase, Mapping) and phase.get("room") == "Flower Room"
                ]
                if isinstance(manifest_phases, list)
                else None
            )
            expected_phases.sort(key=lambda phase: phase["event_id"])
            if actual_phases is not None:
                actual_phases.sort(key=lambda phase: phase["event_id"])
            counts_value = manifest.get("observation_counts")
            manifest_revision_matches = (
                manifest.get("revision") == published_revision
                and actual_phases == expected_phases
                and isinstance(counts_value, Mapping)
            )
            zip_membership_exact = manifest_revision_matches
            zip_current = (
                manifest_revision_matches and dataset["data_revision"] == published_revision
            )
            if manifest_revision_matches:
                zip_counts = {
                    str(table): int(count)
                    for table, count in counts_value.items()
                    if isinstance(table, str)
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                    and count >= 0
                }
                if dataset["data_revision"] == published_revision:
                    zip_new = dict.fromkeys(zip_counts, 0)
                    zip_released = dict.fromkeys(zip_counts, 0)
                else:
                    unavailable.append(
                        {
                            "dataset_id": str(dataset_id),
                            "code": "archive_revision_pending",
                            "revision": dataset["data_revision"],
                            "published_revision": published_revision,
                        }
                    )
                manifest_context = manifest.get("context_counts")
                if isinstance(manifest_context, Mapping):
                    for table, count in manifest_context.items():
                        if (
                            isinstance(table, str)
                            and isinstance(count, int)
                            and not isinstance(count, bool)
                            and count >= 0
                        ):
                            context_counts[f"zip:{table}"] = count
                manifest_gaps = manifest.get("gaps")
                if isinstance(manifest_gaps, list):
                    gaps.extend(dict(gap) for gap in manifest_gaps if isinstance(gap, Mapping))
            elif dataset["state"] == "zip_only":
                unavailable.append(
                    {
                        "dataset_id": str(dataset_id),
                        "code": "archive_membership_counts_unavailable",
                        "revision": published_revision,
                    }
                )
        elif dataset is not None and dataset["state"] == "zip_only":
            unavailable.append(
                {
                    "dataset_id": str(dataset_id),
                    "code": "archive_manifest_unavailable",
                    "revision": published_revision,
                }
            )
        affected_counts: dict[str, Any] = {
            "raw_by_table": raw_counts,
            "zip_by_table": zip_counts,
            "zip_revision": published_revision,
            "zip_membership_exact": zip_membership_exact,
            "zip_current": zip_current,
            "context_by_table": context_counts,
            "raw_total": sum(raw_counts.values()),
            "zip_total": sum(zip_counts.values()) if zip_counts else None,
        }
        return GrowClosePreview(
            lifecycle=lifecycle,
            revision=lifecycle.calendar_revision,
            phase_windows=phase_payload,
            drying_end=drying_end,
            affected_counts=affected_counts,
            gaps=tuple(gaps),
            scope_unresolved=tuple(scope_unresolved),
            conflicts=tuple(conflicts),
            newly_included={
                "raw_by_table": newly_included,
                "zip_by_table": zip_new,
            },
            released={
                "raw_by_table": released,
                "zip_by_table": zip_released,
            },
            unavailable=tuple(unavailable),
        )

    async def get_grow_lifecycle(self, grow_plan_id: uuid.UUID) -> GrowLifecycle:
        async with self.pool.acquire() as conn:
            return await self._lifecycle(conn, grow_plan_id)

    async def preview_grow_close(
        self, grow_plan_id: uuid.UUID, *, now: datetime | None = None
    ) -> GrowClosePreview:
        preview_at = now or datetime.now(UTC)
        if preview_at.tzinfo is None or preview_at.utcoffset() is None:
            raise ValueError("preview clock must be timezone-aware")
        for _ in range(4):
            try:
                async with self.pool.acquire() as conn, self._mutation_transaction(conn):
                    initial = await self._active_phase_events(conn, grow_plan_id)
                    rooms = [event["location"] for event in initial]
                    await self._lock_rooms(conn, rooms)
                    events = await self._active_phase_events(conn, grow_plan_id)
                    if any(event["location"] not in rooms for event in events):
                        raise _RetryRoomLocks
                    windows = self._phase_windows(events)
                    event_ids = [event["id"] for event in events]
                    conflicts = await self._overlapping_grow_ids(
                        conn, windows, exclude_event_ids=event_ids
                    )
                    grow_ids = conflicts | {grow_plan_id}
                    await self._lock_grows(conn, list(grow_ids), require_all=True)
                    datasets = await self._lock_datasets(conn, list(grow_ids))
                    lifecycle = await self._lifecycle(conn, grow_plan_id)
                    return await self._build_close_preview(
                        conn,
                        lifecycle,
                        events,
                        datasets.get(grow_plan_id),
                        now=preview_at,
                    )
            except _RetryRoomLocks:
                continue
        raise CalendarMutationError("calendar_event_conflict", 409)

    async def associate_grow_phase(
        self,
        event_id: int,
        grow_plan_id: uuid.UUID,
        expected_revision: int,
    ) -> dict[str, Any]:
        for _ in range(4):
            try:
                async with self.pool.acquire() as conn, self._mutation_transaction(conn):
                    initial = await conn.fetchrow(
                        "SELECT * FROM calendar_event WHERE id=$1 AND deleted_at IS NULL", event_id
                    )
                    if initial is None:
                        raise CalendarMutationError("calendar_event_not_found", 404)
                    await self._lock_rooms(conn, [initial["location"]])
                    current = await conn.fetchrow(
                        "SELECT * FROM calendar_event WHERE id=$1 AND deleted_at IS NULL", event_id
                    )
                    if current is None:
                        raise CalendarMutationError("calendar_event_not_found", 404)
                    if current["location"] != initial["location"]:
                        raise _RetryRoomLocks
                    if current["event_type"] not in GROW_PLAN_PHASE_TYPES:
                        raise CalendarMutationError(
                            "grow_dates_invalid", 409, "Only grow phase events can be associated"
                        )
                    old_grow_id = current["grow_plan_id"]
                    grow_ids = {grow_plan_id}
                    if old_grow_id is not None:
                        grow_ids.add(old_grow_id)
                    candidate = dict(current)
                    candidate["grow_plan_id"] = grow_plan_id
                    window = self._event_window(candidate, grow_plan_id)
                    conflicts = await self._overlapping_grow_ids(
                        conn, [window], exclude_event_ids=[event_id]
                    )
                    grow_ids.update(conflicts)
                    grows = await self._lock_grows(conn, list(grow_ids), require_all=True)
                    target = grows[grow_plan_id]
                    if target["calendar_revision"] != expected_revision:
                        raise CalendarMutationError("grow_revision_conflict")
                    if target["state"] == "closed":
                        raise CalendarMutationError("grow_closed")
                    if old_grow_id is not None and grows[old_grow_id]["state"] == "closed":
                        raise CalendarMutationError("grow_closed")
                    datasets = await self._lock_datasets(conn, list(grow_ids))
                    locked = await conn.fetchrow(
                        "SELECT * FROM calendar_event WHERE id=$1 AND deleted_at IS NULL FOR UPDATE",
                        event_id,
                    )
                    if locked is None:
                        raise CalendarMutationError("calendar_event_not_found", 404)
                    if locked["location"] != current["location"]:
                        raise _RetryRoomLocks
                    await self._assert_no_overlaps(
                        conn, [window], grow_plan_id, exclude_event_ids=[event_id]
                    )
                    if old_grow_id == grow_plan_id:
                        return dict(locked)
                    row = await conn.fetchrow(
                        "UPDATE calendar_event SET grow_plan_id=$2,crop_batch_id=$3,"
                        "sync_status='pending_push',updated_at=clock_timestamp() "
                        "WHERE id=$1 AND deleted_at IS NULL RETURNING *",
                        event_id,
                        grow_plan_id,
                        target["crop_batch_id"],
                    )
                    changed_grows = [grow_plan_id]
                    if old_grow_id is not None:
                        changed_grows.append(old_grow_id)
                    await self._advance_membership(conn, changed_grows, datasets)
                    return dict(row)
            except _RetryRoomLocks:
                continue
        raise CalendarMutationError("calendar_event_conflict", 409)

    async def close_grow(
        self,
        grow_plan_id: uuid.UUID,
        expected_revision: int,
        *,
        now: datetime | None = None,
    ) -> GrowLifecycle:
        closed_at = now or datetime.now(UTC)
        if closed_at.tzinfo is None or closed_at.utcoffset() is None:
            raise ValueError("closure clock must be timezone-aware")
        closed_at = closed_at.astimezone(UTC)
        for _ in range(4):
            try:
                async with self.pool.acquire() as conn, self._mutation_transaction(conn):
                    initial = await self._active_phase_events(conn, grow_plan_id)
                    rooms = [event["location"] for event in initial]
                    await self._lock_rooms(conn, rooms)
                    events = await self._active_phase_events(conn, grow_plan_id)
                    if any(event["location"] not in rooms for event in events):
                        raise _RetryRoomLocks
                    windows = self._phase_windows(events)
                    event_ids = [event["id"] for event in events]
                    overlaps = await self._overlapping_grow_ids(
                        conn, windows, exclude_event_ids=event_ids
                    )
                    grow_ids = overlaps | {grow_plan_id}
                    grows = await self._lock_grows(conn, list(grow_ids), require_all=True)
                    datasets = await self._lock_datasets(conn, list(grow_ids))
                    lifecycle_row = grows[grow_plan_id]
                    if lifecycle_row["calendar_revision"] != expected_revision:
                        raise CalendarMutationError("grow_revision_conflict")
                    if lifecycle_row["state"] == "closed":
                        raise CalendarMutationError("grow_closed")
                    lifecycle = await self._lifecycle(conn, grow_plan_id)
                    dataset = datasets.get(grow_plan_id)
                    preview = await self._build_close_preview(
                        conn, lifecycle, events, dataset, now=closed_at
                    )
                    if preview.conflicts:
                        conflict = preview.conflicts[0]
                        raise CalendarMutationError(
                            conflict["code"], 409, conflict.get("detail", conflict)
                        )
                    if preview.scope_unresolved:
                        raise CalendarMutationError(
                            "grow_scope_unresolved", 409, list(preview.scope_unresolved)
                        )
                    if preview.drying_end is None:
                        raise CalendarMutationError("grow_dates_invalid", 409)

                    new_calendar_revision = expected_revision + 1
                    original_drying_end = lifecycle.original_drying_end or preview.drying_end
                    anniversary_at = (
                        dataset["anniversary_at"]
                        if dataset is not None and dataset["anniversary_at"] is not None
                        else toronto_midnight(
                            add_calendar_months(original_drying_end + timedelta(days=1), 12)
                        )
                    )
                    await conn.execute(
                        "UPDATE grow_lifecycle SET state='closed',calendar_revision=$2,"
                        "original_drying_end=COALESCE(original_drying_end,$3),"
                        "first_closed_at=COALESCE(first_closed_at,$4),closed_at=$4,"
                        "updated_at=clock_timestamp() WHERE grow_plan_id=$1",
                        grow_plan_id,
                        new_calendar_revision,
                        preview.drying_end,
                        closed_at,
                    )
                    if dataset is None:
                        flower_windows = [
                            window for window in windows if window.room == "Flower Room"
                        ]
                        if not flower_windows:
                            raise CalendarMutationError("grow_dates_invalid", 409)
                        available_from = min(window.start_at for window in flower_windows)
                        available_to = max(window.end_at for window in flower_windows)
                        dataset = await conn.fetchrow(
                            "INSERT INTO archive_dataset("
                            "kind,grow_plan_id,room,calendar_revision,data_revision,state,"
                            "available_from,available_to,anniversary_at) "
                            "VALUES('grow',$1,'Flower Room',$2,1,'pending',$3,$4,$5) "
                            "RETURNING *",
                            grow_plan_id,
                            new_calendar_revision,
                            available_from,
                            available_to,
                            anniversary_at,
                        )
                    else:
                        dataset = await conn.fetchrow(
                            "UPDATE archive_dataset SET calendar_revision=$2,"
                            "anniversary_at=COALESCE(anniversary_at,$3),"
                            "updated_at=clock_timestamp() WHERE dataset_id=$1 RETURNING *",
                            dataset["dataset_id"],
                            new_calendar_revision,
                            anniversary_at,
                        )
                    if dataset is None:
                        raise RuntimeError("grow archive dataset was not persisted")
                    await self._supersede_jobs(conn, [dataset["dataset_id"]])
                    await self._append_archive_windows(
                        conn, dataset["dataset_id"], new_calendar_revision, grow_plan_id
                    )
                    await self._enqueue_lifecycle_job(
                        conn,
                        dataset["dataset_id"],
                        "export",
                        dataset["data_revision"],
                        new_calendar_revision,
                    )
                    return await self._lifecycle(conn, grow_plan_id)
            except _RetryRoomLocks:
                continue
        raise CalendarMutationError("calendar_event_conflict", 409)

    async def reopen_grow(
        self,
        grow_plan_id: uuid.UUID,
        expected_revision: int,
    ) -> GrowLifecycle:
        for _ in range(4):
            try:
                async with self.pool.acquire() as conn, self._mutation_transaction(conn):
                    initial = await self._active_phase_events(conn, grow_plan_id)
                    rooms = [event["location"] for event in initial]
                    await self._lock_rooms(conn, rooms)
                    events = await self._active_phase_events(conn, grow_plan_id)
                    if any(event["location"] not in rooms for event in events):
                        raise _RetryRoomLocks
                    grows = await self._lock_grows(conn, [grow_plan_id], require_all=True)
                    datasets = await self._lock_datasets(conn, [grow_plan_id])
                    lifecycle = grows[grow_plan_id]
                    if lifecycle["calendar_revision"] != expected_revision:
                        raise CalendarMutationError("grow_revision_conflict")
                    if lifecycle["state"] != "closed":
                        raise CalendarMutationError("grow_not_closed")
                    await conn.execute(
                        "UPDATE grow_lifecycle SET state='open',closed_at=NULL,"
                        "updated_at=clock_timestamp() WHERE grow_plan_id=$1",
                        grow_plan_id,
                    )
                    await self._advance_calendar_fence(conn, grow_plan_id, datasets)
                    return await self._lifecycle(conn, grow_plan_id)
            except _RetryRoomLocks:
                continue
        raise CalendarMutationError("calendar_event_conflict", 409)

    @staticmethod
    def _parse_metadata(meta: Any) -> dict[str, Any]:
        if meta is None:
            return {}
        if isinstance(meta, str):
            return json.loads(meta)
        return dict(meta)

    @staticmethod
    def parse_metadata(meta: Any) -> dict[str, Any]:
        return CalendarRepository._parse_metadata(meta)

    async def get_active_flower_phase_event(self, on_date: date) -> dict[str, Any] | None:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM calendar_event
                    WHERE deleted_at IS NULL
                      AND location = 'Flower Room'
                      AND cluster = 'main'
                      AND (metadata->>'auto_mode_transition')::boolean IS NOT FALSE
                      AND start_date <= $1
                      AND COALESCE(end_date, start_date) >= $1
                    ORDER BY (metadata->>'phase_order')::int DESC NULLS LAST
                    LIMIT 1
                    """,
                    on_date,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get active flower phase event: {e}")
            return None

    async def get_last_ended_plan_end(self, location: str) -> date | None:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT MAX(COALESCE(end_date, start_date)) AS last_end
                    FROM calendar_event
                    WHERE deleted_at IS NULL
                      AND location = $1
                      AND grow_plan_id IS NOT NULL
                      AND event_type IN ('harvest', 'drying')
                    """,
                    location,
                )
                return row["last_end"] if row and row["last_end"] else None
        except Exception as e:
            logger.error(f"Failed to get last ended plan end for {location}: {e}")
            return None

    async def get_current_run(
        self, location: str, today: date | None = None
    ) -> GrowPlanCurrentRun | None:
        """Resolve a canonical room plan spanning Toronto today, including phase gaps.

        Only non-deleted phases with an authoritative top-level plan ID define
        the full room extent. Missing plans return None; ambiguity and database
        errors must not become a guessed run or a fake empty result.
        """
        span_day = today if today is not None else datetime.now(tz=LOCAL_TZ).date()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                _CURRENT_RUN_SQL, location, sorted(GROW_PLAN_PHASE_TYPES), span_day
            )
        if len(rows) > 1:
            ids = ", ".join(str(row["grow_plan_id"]) for row in rows)
            raise GrowPlanCurrentRunAmbiguousError(
                f"{location} has at least 2 distinct grow plans spanning "
                f"{span_day.isoformat()}: {ids}"
            )
        if not rows:
            return None
        row = rows[0]
        return GrowPlanCurrentRun(
            location=location,
            grow_plan_id=row["grow_plan_id"],
            start_date=row["run_start"],
            end_date=row["run_end"],
        )

    async def record_mode_application(
        self,
        event_id: int,
        applied_date: date,
        mode_id: int | None,
        submode_id: int | None,
        triggered_by: str,
    ) -> None:
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO calendar_mode_application (
                        event_id, applied_date, mode_id, submode_id, triggered_by
                    ) VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (event_id, applied_date) DO NOTHING
                    """,
                    event_id,
                    applied_date,
                    mode_id,
                    submode_id,
                    triggered_by,
                )
        except Exception as e:
            logger.error(f"Failed to record mode application for event {event_id}: {e}")

    async def mode_application_exists(self, event_id: int, applied_date: date) -> bool:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT 1 FROM calendar_mode_application
                    WHERE event_id = $1 AND applied_date = $2
                    """,
                    event_id,
                    applied_date,
                )
                return row is not None
        except Exception as e:
            logger.error(f"Failed to check mode application for event {event_id}: {e}")
            return False

    # --- Confirmed outbound Calendar sync ---
    @staticmethod
    def _connection_public(row: asyncpg.Record) -> dict[str, Any]:
        return {key: value for key, value in dict(row).items() if key != "credentials_encrypted"}

    async def get_sync_connection(self) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM calendar_sync_connection ORDER BY id DESC LIMIT 1"
        )
        return self._connection_public(row) if row else None

    async def get_sync_credentials(self, connection_id: int) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM calendar_sync_connection WHERE id=$1", connection_id
        )
        return dict(row) if row else None

    async def upsert_sync_connection(self, data: dict[str, Any]) -> dict[str, Any]:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('sj-calendar-sync-connection',0))"
            )
            generation = await conn.fetchval(
                "SELECT COALESCE(max(generation),0)+1 FROM calendar_sync_connection WHERE instance_id=$1",
                data["instance_id"],
            )
            row = await conn.fetchrow(
                "INSERT INTO calendar_sync_connection(display_name,account_email,caldav_base_url,"
                "credentials_encrypted,target_calendar_url,enabled,instance_id,generation,files_root_url) "
                "VALUES($1,$2,$3,$4,$5,false,$6,$7,$8) RETURNING *",
                data.get("display_name"),
                data["account_email"],
                data["caldav_base_url"],
                data["credentials_encrypted"],
                data["target_calendar_url"],
                data["instance_id"],
                generation,
                data["files_root_url"],
            )
            return self._connection_public(row)

    async def mirror_preview(self, connection_id: int) -> dict[str, Any]:
        row = await self.pool.fetchrow(
            "SELECT * FROM calendar_sync_connection WHERE id=$1", connection_id
        )
        if row is None:
            raise CalendarMutationError("calendar_connection_not_found", 404)
        if row["files_root_url"] is None:
            raise CalendarMutationError("calendar_connection_unavailable", 503)
        count = await self.pool.fetchval(
            "SELECT count(*) FROM calendar_event WHERE deleted_at IS NULL"
        )
        return {
            "connection_id": connection_id,
            "generation": row["generation"],
            "event_count": count,
            "backup_required": True,
        }

    async def reconnect_and_mirror(
        self, connection_id: int, expected_generation: int
    ) -> dict[str, Any]:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('sj-calendar-sync-connection',0))"
            )
            row = await conn.fetchrow(
                "SELECT * FROM calendar_sync_connection WHERE id=$1 FOR UPDATE", connection_id
            )
            if row is None:
                raise CalendarMutationError("calendar_connection_not_found", 404)
            if row["generation"] != expected_generation:
                raise CalendarMutationError("calendar_generation_conflict")
            if row["files_root_url"] is None:
                raise CalendarMutationError("calendar_connection_unavailable", 503)
            await conn.execute("UPDATE calendar_sync_connection SET enabled=false WHERE enabled")
            generation = await conn.fetchval(
                "UPDATE calendar_sync_connection SET enabled=true,generation=generation+1,last_error=NULL "
                "WHERE id=$1 RETURNING generation",
                connection_id,
            )
            events = await conn.fetch(
                "UPDATE calendar_event SET local_revision=local_revision+1,sync_connection_id=$1,"
                "sync_generation=$2,sync_status='pending_push',sync_error_code=NULL,sync_attempts=0,"
                "sync_next_attempt_at=clock_timestamp(),sync_lease_owner=NULL,sync_lease_until=NULL,"
                "sync_mirror_all=true,external_provider=NULL,external_calendar_id=NULL,"
                "external_event_id=NULL,external_etag=NULL WHERE deleted_at IS NULL RETURNING id",
                connection_id,
                generation,
            )
            # Deleted old rows are not mirrored into the rebuilt collection.
            await conn.execute(
                "UPDATE calendar_event SET external_provider=NULL,external_calendar_id=NULL,"
                "external_event_id=NULL,external_etag=NULL,sync_lease_owner=NULL,sync_lease_until=NULL "
                "WHERE deleted_at IS NOT NULL"
            )
            return {
                "connection_id": connection_id,
                "generation": generation,
                "event_count": len(events),
                "event_ids": [event["id"] for event in events],
            }

    async def delete_sync_connection(self) -> bool:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('sj-calendar-sync-connection',0))"
            )
            rows = await conn.fetch(
                "UPDATE calendar_sync_connection SET enabled=false WHERE enabled RETURNING id"
            )
            await conn.execute(
                "UPDATE calendar_event SET sync_status='error',sync_error_code='calendar_connection_unavailable',"
                "sync_lease_owner=NULL,sync_lease_until=NULL WHERE sync_status IN ('pending_push','pending_delete')"
            )
            return bool(rows)

    async def events_pending_sync(self) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT e.id,e.local_revision FROM calendar_event e JOIN calendar_sync_connection c "
            "ON c.id=e.sync_connection_id AND c.generation=e.sync_generation "
            "WHERE c.enabled AND c.files_root_url IS NOT NULL AND e.sync_status IN ('pending_push','pending_delete') "
            "AND e.sync_next_attempt_at<=clock_timestamp() "
            "AND (e.sync_lease_until IS NULL OR e.sync_lease_until<=clock_timestamp()) "
            "ORDER BY e.sync_next_attempt_at,e.id LIMIT 4"
        )
        return [dict(row) for row in rows]

    async def claim_sync_event(self, event_id: int, owner: uuid.UUID) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "UPDATE calendar_event e SET sync_lease_owner=$2,sync_lease_until=clock_timestamp()+interval '15 seconds',"
            "sync_attempts=e.sync_attempts+1 FROM calendar_sync_connection c "
            "WHERE e.id=$1 AND c.id=e.sync_connection_id AND c.generation=e.sync_generation AND c.enabled "
            "AND c.files_root_url IS NOT NULL AND e.sync_status IN ('pending_push','pending_delete') "
            "AND e.sync_next_attempt_at<=clock_timestamp() "
            "AND (e.sync_lease_until IS NULL OR e.sync_lease_until<=clock_timestamp()) "
            "RETURNING e.*,c.credentials_encrypted,c.account_email,c.target_calendar_url,c.caldav_base_url",
            event_id,
            owner,
        )
        return dict(row) if row else None

    async def complete_sync_event(
        self, row: Mapping[str, Any], owner: uuid.UUID, href: str | None, etag: str | None
    ) -> bool:
        result = await self.pool.fetchval(
            "UPDATE calendar_event e SET sync_status='synced',sync_error_code=NULL,last_synced_at=clock_timestamp(),"
            "sync_lease_owner=NULL,sync_lease_until=NULL,sync_mirror_all=false,external_provider='nextcloud',"
            "external_event_id=$6,external_etag=$7,external_calendar_id=c.target_calendar_url "
            "FROM calendar_sync_connection c WHERE e.id=$1 AND e.local_revision=$2 AND e.sync_generation=$3 "
            "AND e.sync_connection_id=$4 AND e.sync_lease_owner=$5 AND e.sync_lease_until>clock_timestamp() "
            "AND c.id=e.sync_connection_id AND c.generation=e.sync_generation AND c.enabled RETURNING true",
            row["id"],
            row["local_revision"],
            row["sync_generation"],
            row["sync_connection_id"],
            owner,
            href,
            etag,
        )
        return result is True

    async def fail_sync_event(
        self, row: Mapping[str, Any], owner: uuid.UUID, code: str, *, blocked: bool = False
    ) -> None:
        delays = (1, 5, 30, 60, 300)
        delay = delays[min(max(row["sync_attempts"] - 1, 0), len(delays) - 1)]
        state = (
            ("conflict" if code == "calendar_remote_conflict" else "error")
            if blocked
            else row["sync_status"]
        )
        await self.pool.execute(
            "UPDATE calendar_event SET sync_status=$6,sync_error_code=$7,"
            "sync_next_attempt_at=clock_timestamp()+$8*interval '1 second',sync_lease_owner=NULL,sync_lease_until=NULL "
            "WHERE id=$1 AND local_revision=$2 AND sync_generation=$3 AND sync_connection_id=$4 AND sync_lease_owner=$5",
            row["id"],
            row["local_revision"],
            row["sync_generation"],
            row["sync_connection_id"],
            owner,
            state,
            code,
            delay,
        )
