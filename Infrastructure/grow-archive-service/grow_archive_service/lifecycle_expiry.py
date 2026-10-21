from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
import json
from typing import Any

import asyncpg

from shared.grow_lifecycle import retention_cutoffs
from shared.infra_logging import get_logger

from .lifecycle_constants import (
    CURSOR_RELATION,
    EXPIRY_DELETE_SLICE,
    EXPIRY_SLICES_PER_PAIR,
    EXPIRY_TICK_ROW_BUDGET,
    EXPIRY_WINDOWS_PER_PAIR,
    FLOWER_ROOM,
    IDENTITY_STREAMS,
    MAX_WINDOW_BOUNDARIES,
    PURGE_BATCH_BYTES,
    PURGE_BATCH_ROWS,
    VEG_ROOM,
)
from .lifecycle_types import (
    ExpiredInterval,
    HeldScope,
    NongrowExpiryResult,
    _IdentityAssignment,
    _IdentityRoomIndex,
    _unique_held,
)
from .models import ArchiveError
from .schema import SourceStream
from .source_retention import ANCHOR_COLUMNS, prepare_retention_anchors, removable_observation_sql
from .streams import STREAM_BINDINGS, StreamBinding, time_nullable

logger = get_logger("grow_archive_service.lifecycle")


class ExpiryMixin:
    def _freshness_paused(self) -> bool:
        # Destructive work and archive IO fail closed unless the live freshness
        # guard was explicitly wired by the service lifespan.
        return self.freshness is None or self.freshness.paused

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

