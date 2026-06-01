"""Calendar events, grow plans, and sync metadata."""

from __future__ import annotations

from datetime import date
import json
from typing import Any
import uuid
from zoneinfo import ZoneInfo

import asyncpg

from app.calendar.flower_grow_plan import (
    FLOWER_PHASE_TYPES,
    FlowerGrowPlanInput,
    build_flower_grow_plan,
    phase_to_metadata,
)
from shared.infra_logging import get_logger

logger = get_logger(__name__)
LOCAL_TZ = ZoneInfo("America/Toronto")

DEFAULT_LIMIT = 500
MAX_LIMIT = 2000


class CalendarRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_room_profiles(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT * FROM calendar_room_profile WHERE enabled = true ORDER BY sort_order"
        )
        return [dict(r) for r in rows]

    async def get_event(
        self, event_id: int, include_deleted: bool = False
    ) -> dict[str, Any] | None:
        clause = "" if include_deleted else "AND deleted_at IS NULL"
        row = await self._pool.fetchrow(
            f"SELECT * FROM calendar_event WHERE id = $1 {clause}",
            event_id,
        )
        return dict(row) if row else None

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
        rows = await self._pool.fetch(sql, *params)
        items = [dict(r) for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            next_cursor = f"{last['start_date'].isoformat()},{last['id']}"
        return items, next_cursor

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
        rows = await self._pool.fetch(
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
            WHERE h.triggered_at::date >= $1
              AND h.triggered_at::date <= $2
              {loc_clause}
            ORDER BY h.triggered_at DESC
            """,
            *params,
        )
        return [dict(r) for r in rows]

    async def create_event(self, data: dict[str, Any]) -> dict[str, Any]:
        ical_uid = f"cea-cal-{data['location'].lower().replace(' ', '-')}-{uuid.uuid4().hex[:12]}@siberianjungle.local"
        async with self._pool.acquire() as conn:
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
                data["event_type"],
                data["title"],
                data["start_date"],
                data.get("end_date"),
                data.get("all_day", True),
                data.get("notes"),
                json.dumps(data.get("metadata") or {}),
                data.get("crop_batch_id"),
                data.get("grow_plan_id"),
                ical_uid,
                data.get("created_by"),
            )
        return dict(row)

    async def update_event(self, event_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        fields = []
        params: list[Any] = []
        for key in (
            "title",
            "start_date",
            "end_date",
            "notes",
            "event_type",
            "location",
            "cluster",
        ):
            if key in data:
                params.append(data[key])
                fields.append(f"{key} = ${len(params)}")
        if "metadata" in data:
            params.append(json.dumps(data["metadata"]))
            fields.append(f"metadata = ${len(params)}::jsonb")
        if not fields:
            return await self.get_event(event_id)
        params.append(event_id)
        fields.append("updated_at = NOW()")
        fields.append("sync_status = 'pending_push'")
        row = await self._pool.fetchrow(
            f"UPDATE calendar_event SET {', '.join(fields)} WHERE id = ${len(params)} "
            "AND deleted_at IS NULL RETURNING *",
            *params,
        )
        return dict(row) if row else None

    async def soft_delete_event(self, event_id: int) -> bool:
        result = await self._pool.execute(
            """
            UPDATE calendar_event
            SET deleted_at = NOW(), sync_status = 'pending_delete', updated_at = NOW()
            WHERE id = $1 AND deleted_at IS NULL
            """,
            event_id,
        )
        return result.endswith("1")

    async def check_flower_overlap(
        self,
        from_date: date,
        to_date: date,
        exclude_grow_plan_id: uuid.UUID | None = None,
    ) -> str | None:
        """Return conflicting grow_plan_id if overlap exists."""
        params: list[Any] = [from_date, to_date]
        exclude = ""
        if exclude_grow_plan_id:
            params.append(exclude_grow_plan_id)
            exclude = f"AND e.grow_plan_id IS DISTINCT FROM ${len(params)}"
        row = await self._pool.fetchrow(
            f"""
            SELECT e.grow_plan_id::text AS gid
            FROM calendar_event e
            WHERE e.deleted_at IS NULL
              AND e.location = 'Flower Room'
              AND e.event_type = ANY($3::text[])
              AND e.start_date <= $2
              AND COALESCE(e.end_date, e.start_date) >= $1
              {exclude}
            LIMIT 1
            """,
            from_date,
            to_date,
            list(FLOWER_PHASE_TYPES),
        )
        return row["gid"] if row else None

    async def create_flower_grow_plan(
        self,
        inp: FlowerGrowPlanInput,
        idempotency_key: uuid.UUID,
        created_by: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        existing = await self._pool.fetchrow(
            """
            SELECT grow_plan_id::text FROM grow_plan_idempotency
            WHERE idempotency_key = $1 AND expires_at > NOW()
            """,
            idempotency_key,
        )
        if existing:
            gid = uuid.UUID(existing["grow_plan_id"])
            events = await self._pool.fetch(
                "SELECT * FROM calendar_event WHERE grow_plan_id = $1 AND deleted_at IS NULL",
                gid,
            )
            return {"grow_plan_id": str(gid), "events": [dict(e) for e in events]}, None

        phases, err = build_flower_grow_plan(inp)
        if err:
            return None, err

        flower_dates = [p for p in phases if p.location == "Flower Room"]
        if flower_dates:
            f_start = min(p.start_date for p in flower_dates)
            f_end = max(p.end_date for p in flower_dates)
            conflict = await self.check_flower_overlap(f_start, f_end)
            if conflict:
                return None, f"Overlaps existing grow plan {conflict}"

        grow_plan_id = uuid.uuid4()
        batch_start = min(p.start_date for p in phases)
        harvest_end = max(p.end_date for p in phases)

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                room_row = await conn.fetchrow(
                    "SELECT room_id FROM room WHERE name = 'Flower Room'"
                )
                if not room_row:
                    return None, "Flower Room not found in room table"
                batch_row = await conn.fetchrow(
                    """
                    INSERT INTO crop_batch (crop_name, start_date, end_date, room_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING batch_id
                    """,
                    inp.crop_name,
                    batch_start,
                    harvest_end,
                    room_row["room_id"],
                )
                batch_id = batch_row["batch_id"]
                created_events: list[dict[str, Any]] = []
                for phase in phases:
                    meta = phase_to_metadata(
                        phase, str(grow_plan_id), inp.environment, inp.crop_name
                    )
                    ical_uid = f"cea-cal-{phase.location.lower().replace(' ', '-')}-{uuid.uuid4().hex[:12]}@siberianjungle.local"
                    row = await conn.fetchrow(
                        """
                        INSERT INTO calendar_event (
                            location, cluster, event_type, title, start_date, end_date,
                            all_day, metadata, crop_batch_id, grow_plan_id, ical_uid,
                            sync_status, created_by
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, true, $7::jsonb, $8, $9, $10,
                            'pending_push', $11
                        )
                        RETURNING *
                        """,
                        phase.location,
                        phase.cluster,
                        phase.event_type,
                        phase.title,
                        phase.start_date,
                        phase.end_date,
                        json.dumps(meta),
                        batch_id,
                        grow_plan_id,
                        ical_uid,
                        created_by,
                    )
                    created_events.append(dict(row))
                await conn.execute(
                    """
                    INSERT INTO grow_plan_idempotency (idempotency_key, grow_plan_id, expires_at)
                    VALUES ($1, $2, NOW() + interval '24 hours')
                    """,
                    idempotency_key,
                    grow_plan_id,
                )
        return {
            "grow_plan_id": str(grow_plan_id),
            "crop_batch_id": batch_id,
            "events": created_events,
        }, None

    async def delete_grow_plan(self, grow_plan_id: uuid.UUID) -> int:
        result = await self._pool.fetch(
            """
            UPDATE calendar_event
            SET deleted_at = NOW(), sync_status = 'pending_delete', updated_at = NOW()
            WHERE grow_plan_id = $1 AND deleted_at IS NULL
            RETURNING id
            """,
            grow_plan_id,
        )
        return len(result)

    @staticmethod
    def _parse_metadata(meta: Any) -> dict[str, Any]:
        if meta is None:
            return {}
        if isinstance(meta, str):
            return json.loads(meta)
        return dict(meta)

    async def get_active_flower_phase_event(self, on_date: date) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
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

    async def get_last_ended_plan_end(self, location: str) -> date | None:
        row = await self._pool.fetchrow(
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

    async def record_mode_application(
        self,
        event_id: int,
        applied_date: date,
        mode_id: int | None,
        submode_id: int | None,
        triggered_by: str,
    ) -> None:
        await self._pool.execute(
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

    async def mode_application_exists(self, event_id: int, applied_date: date) -> bool:
        row = await self._pool.fetchrow(
            """
            SELECT 1 FROM calendar_mode_application
            WHERE event_id = $1 AND applied_date = $2
            """,
            event_id,
            applied_date,
        )
        return row is not None

    # --- Sync connection ---
    async def get_sync_connection(self) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT id, provider, display_name, account_email, caldav_base_url, "
            "target_calendar_url, sync_token, last_sync_at, last_error, enabled, created_at "
            "FROM calendar_sync_connection WHERE enabled = true ORDER BY id LIMIT 1"
        )
        return dict(row) if row else None

    async def upsert_sync_connection(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._pool.execute("UPDATE calendar_sync_connection SET enabled = false")
        row = await self._pool.fetchrow(
            """
            INSERT INTO calendar_sync_connection (
                display_name, account_email, caldav_base_url,
                credentials_encrypted, target_calendar_url, enabled
            ) VALUES ($1, $2, $3, $4, $5, true)
            RETURNING id, provider, display_name, account_email, caldav_base_url,
                      target_calendar_url, last_sync_at, last_error, enabled, created_at
            """,
            data.get("display_name"),
            data.get("account_email"),
            data["caldav_base_url"],
            data["credentials_encrypted"],
            data["target_calendar_url"],
        )
        return dict(row)

    async def delete_sync_connection(self) -> None:
        await self._pool.execute("UPDATE calendar_sync_connection SET enabled = false")

    async def update_sync_state(
        self,
        conn_id: int,
        sync_token: str | None = None,
        last_error: str | None = None,
    ) -> None:
        await self._pool.execute(
            """
            UPDATE calendar_sync_connection
            SET sync_token = COALESCE($2, sync_token),
                last_sync_at = NOW(),
                last_error = $3
            WHERE id = $1
            """,
            conn_id,
            sync_token,
            last_error,
        )

    async def events_pending_sync(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM calendar_event
            WHERE sync_status IN ('pending_push', 'pending_delete')
            ORDER BY updated_at
            LIMIT 200
            """
        )
        return [dict(r) for r in rows]

    async def mark_event_synced(
        self,
        event_id: int,
        external_event_id: str | None,
        external_etag: str | None,
        external_calendar_id: str | None,
    ) -> None:
        await self._pool.execute(
            """
            UPDATE calendar_event
            SET sync_status = 'synced',
                external_provider = 'nextcloud',
                external_event_id = $2,
                external_etag = $3,
                external_calendar_id = $4,
                last_synced_at = NOW()
            WHERE id = $1
            """,
            event_id,
            external_event_id,
            external_etag,
            external_calendar_id,
        )
