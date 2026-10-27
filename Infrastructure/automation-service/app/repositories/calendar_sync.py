"""Outbound Calendar sync persistence for CalendarRepository: connections and the event push queue."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import uuid

import asyncpg

from app.repositories.calendar_errors import CalendarMutationError


class CalendarSyncMixin:
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
