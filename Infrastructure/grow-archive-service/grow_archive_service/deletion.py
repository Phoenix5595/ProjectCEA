"""Verified transactional deletion of source rows already present in an archive."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import tempfile
import time
from typing import Any
from uuid import UUID

import asyncpg
from shared.grow_lifecycle import toronto_midnight

from .binary_codec import canonical, rows
from .capture import part_metrics, part_query
from .derived_refresh import DerivedHistoryMaintenance
from .execution import HeavyWorkExecutor
from .jobs import ArchiveJobQueue
from .manifest import ArchiveManifest
from .membership import CaptureWindow
from .models import ArchiveError, JobClaim
from .parquet_codec import ArchiveIntegrityError, iter_archive_binary_rows
from .pg_fields import decode
from .schema import SourceStream, load_source_catalog
from .source_retention import prepare_retention_anchors
from .streams import HYPERTABLES


class SourceDeletion:
    def __init__(
        self,
        pool: asyncpg.Pool,
        heavy: HeavyWorkExecutor,
        queue: ArchiveJobQueue,
        derived: DerivedHistoryMaintenance,
    ) -> None:
        self.pool = pool
        self.heavy = heavy
        self.queue = queue
        self.derived = derived

    async def decompress_compressed_chunks(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        source_table: str,
        start: datetime,
        end: datetime,
    ) -> None:
        """Decompress compressed source chunks overlapping a pending delete window."""
        if source_table not in HYPERTABLES or start >= end:
            return
        await conn.execute("SET LOCAL lock_timeout='1s'")
        started = time.monotonic()
        # show_chunks(newer_than/older_than) excludes chunks crossing either bound;
        # range_start/range_end let this query select every true overlap.
        await conn.fetch(
            "SELECT decompress_chunk("
            "format('%I.%I',c.chunk_schema,c.chunk_name)::regclass,if_compressed=>true) "
            "FROM timescaledb_information.chunks AS c "
            "WHERE c.hypertable_schema='public' AND c.hypertable_name=$1 "
            "AND c.is_compressed AND c.range_start<$3 AND c.range_end>$2",
            source_table,
            start,
            end,
        )
        await self.heavy.throttle_async(time.monotonic() - started)


    async def delete_verified(
        self,
        claim: JobClaim,
        artifact: ArchiveManifest,
        *,
        rows_before: datetime,
        now: datetime,
        archive_path: Path | None = None,
        component_dataset_id: UUID | None = None,
    ) -> int:
        try:
            return await self._delete_verified(
                claim,
                artifact,
                rows_before=rows_before,
                now=now,
                archive_path=archive_path,
                component_dataset_id=component_dataset_id,
            )
        except ArchiveError as error:
            if error.code == "archive_source_changed":
                try:
                    await self._mark_source_changed(claim)
                except asyncpg.exceptions.SerializationError as serialization_error:
                    raise ArchiveError("archive_database_unavailable", status_code=503) from serialization_error
            raise
        except asyncpg.exceptions.SerializationError as error:
            raise ArchiveError("archive_database_unavailable", status_code=503) from error

    async def _delete_verified(
        self,
        claim: JobClaim,
        artifact: ArchiveManifest,
        *,
        rows_before: datetime,
        now: datetime,
        archive_path: Path | None,
        component_dataset_id: UUID | None,
    ) -> int:
        if any(value.tzinfo is None or value.utcoffset() is None for value in (rows_before, now)):
            raise ArchiveError("grow_dates_invalid")
        cutoff = rows_before.astimezone(UTC)
        instant_now = now.astimezone(UTC)
        if cutoff > instant_now:
            raise ArchiveError("grow_dates_invalid")

        dataset_id = claim.fence.dataset_id
        revision = claim.fence.data_revision
        manifest = _component_manifest(artifact, dataset_id, revision)
        scope_windows = _manifest_windows(manifest)
        rooms = tuple(sorted({window.room for window in scope_windows}))
        source_streams = load_source_catalog().by_name()
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        observation_tables: set[str] = set()

        members = manifest.get("members")
        if not isinstance(members, (list, tuple)):
            raise ArchiveError("archive_integrity_failed")
        for member_value in members:
            if not isinstance(member_value, Mapping) or member_value.get("record_role") != "observation":
                continue
            table = member_value.get("source_table")
            day = member_value.get("toronto_date")
            if not isinstance(table, str) or table not in source_streams or not isinstance(day, str):
                raise ArchiveError("archive_integrity_failed")
            stream = source_streams[table]
            if stream.status != "defined" or stream.time_column is None:
                raise ArchiveError("schema_descriptor_unavailable")
            start = _instant(member_value.get("window_start"))
            end = _instant(member_value.get("window_end"))
            if start >= end:
                raise ArchiveError("archive_integrity_failed")
            matching = [window for window in scope_windows if window.start <= start and end <= window.end]
            if not matching:
                raise ArchiveError("archive_integrity_failed")
            room = manifest.get("room")
            if not isinstance(room, str) or room not in {window.room for window in matching}:
                if len(matching) != 1:
                    raise ArchiveError("archive_integrity_failed")
                room = matching[0].room
            record = {
                "member": member_value,
                "stream": stream,
                "room": room,
                "start": start,
                "end": end,
            }
            grouped[(table, day)].append(record)
            observation_tables.add(table)

        await self.derived.preflight(tuple(sorted(observation_tables)))
        if not grouped:
            return 0
        async with self.pool.acquire() as conn:
            removed_rows = await conn.fetch(
                "SELECT revision,source_table,room,start_at,end_at "
                "FROM public.archive_availability WHERE dataset_id=$1 AND source_rows_removed "
                "AND source_table=ANY($2::text[])",
                dataset_id,
                sorted(observation_tables),
            )
        prior_intervals_by_table: dict[str, set[tuple[datetime, datetime]]] = defaultdict(set)
        for (table, _day), records in grouped.items():
            for record in records:
                overlapping = [
                    row for row in removed_rows
                    if row["source_table"] == table
                    and row["room"] in (None, record["room"])
                    and row["start_at"] < record["end"]
                    and row["end_at"] > record["start"]
                ]
                record["removed_prior_revision"] = any(
                    int(row["revision"]) < revision for row in overlapping
                )
                if record["removed_prior_revision"] and record["start"] < cutoff:
                    prior_intervals_by_table[table].add((record["start"], record["end"]))

        archived_payloads: dict[str, dict[tuple[datetime, datetime], Counter[bytes]]] = {}
        if prior_intervals_by_table:
            if archive_path is None:
                raise ArchiveError("archive_integrity_failed")
            archive_path = Path(archive_path)
            if archive_path.is_symlink() or not archive_path.is_file():
                raise ArchiveError("archive_integrity_failed")
            archive_path = archive_path.resolve(strict=True)
            component_id = component_dataset_id or dataset_id
            for table, intervals in prior_intervals_by_table.items():
                stream = source_streams[table]
                archived_payloads[table] = await self.heavy.run(
                    lambda control, p=archive_path, s=stream, ranges=tuple(sorted(intervals)):
                        _archive_payload_counts(
                            p, artifact, s, ranges, component_id, control.checkpoint,
                        )
                )


        total_deleted = 0
        query_index = 0
        dirty = False
        with tempfile.TemporaryDirectory(prefix="grow-archive-delete-") as temp_root:
            temp_dir = Path(temp_root)
            for (table, day), records in sorted(grouped.items()):
                stream = records[0]["stream"]
                eligible = [record for record in records if record["start"] < cutoff]
                if not eligible:
                    continue
                touched: list[dict[str, Any]] = []
                async with self.pool.acquire() as conn:
                    async with conn.transaction(isolation="repeatable_read"):
                        await conn.execute("SET LOCAL statement_timeout='60s'")
                        await conn.execute("SET LOCAL lock_timeout='1s'")
                        for room in rooms:
                            await conn.execute(
                                "SELECT pg_advisory_xact_lock(public.sj_history_room_lock_key($1))",
                                room,
                            )
                        await self.queue.check_fence(conn, claim, lock=True)
                        anchor_relation = await prepare_retention_anchors(conn, stream, cutoff)

                        for record in eligible:
                            start = record["start"]
                            end = record["end"]
                            delete_end = min(end, cutoff)
                            if start >= delete_end:
                                continue
                            already_removed = await conn.fetchval(
                                "SELECT EXISTS(SELECT 1 FROM public.archive_availability "
                                "WHERE dataset_id=$1 AND revision=$2 AND source_table=$3 "
                                "AND room IS NOT DISTINCT FROM $4 AND source_rows_removed "
                                "AND start_at<$6 AND end_at>$5)",
                                dataset_id,
                                revision,
                                table,
                                record["room"],
                                start,
                                end,
                            )
                            if already_removed:
                                continue

                            window = CaptureWindow(record["room"], start, end)
                            check_sql, check_args = part_query(
                                stream,
                                (window,),
                                dataset_id,
                                revision,
                                start,
                                end,
                                record_role="observation",
                            )
                            check_path = temp_dir / f"recheck-{query_index:08d}.copy"
                            query_index += 1
                            started = time.monotonic()
                            await conn.copy_from_query(
                                check_sql, *check_args, output=str(check_path), format="binary",
                            )
                            await self.heavy.throttle_async(time.monotonic() - started)
                            member = record["member"]
                            if record["removed_prior_revision"]:
                                source_payloads = await self.heavy.run(
                                    lambda control, path=check_path, source=stream:
                                        _source_payload_counts(path, source, control.checkpoint)
                                )
                                archive_payloads = archived_payloads.get(table, {}).get((start, end), Counter())
                                if any(count > archive_payloads[payload]
                                       for payload, count in source_payloads.items()):
                                    check_path.unlink(missing_ok=True)
                                    raise ArchiveError("archive_source_changed")
                            else:
                                count, _decoded_bytes, digest = await self.heavy.run(
                                    lambda control, path=check_path: part_metrics(path, control.checkpoint)
                                )
                                if (
                                    count != member.get("row_count")
                                    or digest != member.get("canonical_sha256")
                                ):
                                    check_path.unlink(missing_ok=True)
                                    raise ArchiveError("archive_source_changed")
                            check_path.unlink(missing_ok=True)
                            await self.decompress_compressed_chunks(conn, table, start, delete_end)

                            delete_sql, delete_args = part_query(
                                stream,
                                (window,),
                                dataset_id,
                                revision,
                                start,
                                end,
                                operation="delete",
                                cutoff=cutoff,
                                record_role="observation",
                                anchor_relation=anchor_relation,
                            )
                            started = time.monotonic()
                            status = await conn.execute(delete_sql, *delete_args)
                            await self.heavy.throttle_async(time.monotonic() - started)
                            try:
                                deleted = int(status.rsplit(" ", 1)[1])
                            except (IndexError, ValueError) as error:
                                raise ArchiveError("archive_database_unavailable", status_code=503) from error
                            total_deleted += deleted

                            started = time.monotonic()
                            await conn.execute(
                                "INSERT INTO public.archive_availability "
                                "(dataset_id,revision,source_table,room,start_at,end_at,state,"
                                "source_rows_removed,source_removed_at,aggregates_refreshed) "
                                "VALUES($1,$2,$3,$4,$5,$6,'captured',true,clock_timestamp(),false) "
                                "ON CONFLICT(dataset_id,revision,source_table,room,start_at,end_at) "
                                "DO UPDATE SET source_rows_removed=true,"
                                "source_removed_at=EXCLUDED.source_removed_at,aggregates_refreshed=false,"
                                "aggregates_refreshed_at=NULL",
                                dataset_id,
                                revision,
                                table,
                                record["room"],
                                start,
                                delete_end,
                            )
                            await self.heavy.throttle_async(time.monotonic() - started)
                            touched.append({
                                "stream": stream,
                                "room": record["room"],
                                "start": start,
                                "end": delete_end,
                            })

                if not touched:
                    continue
                day_value = date.fromisoformat(day)
                day_start = toronto_midnight(day_value)
                day_end = toronto_midnight(day_value + timedelta(days=1))
                for room in sorted({record["room"] for record in touched}):
                    await self.derived.refresh(table, room, day_start, day_end)
                    async with self.pool.acquire() as conn, conn.transaction():
                        await conn.execute(
                            "UPDATE public.archive_availability SET aggregates_refreshed=true,"
                            "aggregates_refreshed_at=clock_timestamp(),updated_at=clock_timestamp() "
                            "WHERE dataset_id=$1 AND revision=$2 AND source_table=$3 "
                            "AND room IS NOT DISTINCT FROM $4 AND source_rows_removed "
                            "AND start_at<$6 AND end_at>$5",
                            dataset_id,
                            revision,
                            table,
                            room,
                            day_start,
                            day_end,
                        )

                async with self.pool.acquire() as conn:
                    async with conn.transaction(isolation="read_committed"):
                        anchor_relation = await prepare_retention_anchors(conn, stream, cutoff)
                        remaining = False
                        for record in touched:
                            window = CaptureWindow(record["room"], record["start"], record["end"])
                            exists_sql, exists_args = part_query(
                                record["stream"],
                                (window,),
                                dataset_id,
                                revision,
                                record["start"],
                                record["end"],
                                operation="exists",
                                cutoff=cutoff,
                                record_role="observation",
                                anchor_relation=anchor_relation,
                            )
                            if await conn.fetchval(exists_sql, *exists_args):
                                remaining = True
                                break
                        if remaining:
                            await self.queue.check_fence(conn, claim, lock=True)
                            await conn.execute(
                                "UPDATE public.archive_dataset SET data_revision=data_revision+1,"
                                "state='dirty',updated_at=clock_timestamp() WHERE dataset_id=$1",
                                dataset_id,
                            )
                            dirty = True
                if dirty:
                    break
        return total_deleted

    async def _mark_source_changed(self, claim: JobClaim) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE public.archive_dataset SET data_revision=data_revision+1,state='dirty',"
                "last_error_code='archive_source_changed',updated_at=clock_timestamp() "
                "WHERE dataset_id=$1",
                claim.fence.dataset_id,
            )


def _component_manifest(
    artifact: ArchiveManifest, dataset_id: UUID, revision: int,
) -> Mapping[str, Any]:
    manifest = artifact.data
    if isinstance(manifest.get("components"), (list, tuple)):
        try:
            from .annual_codec import annual_component

            manifest, _prefix = annual_component(artifact, dataset_id)
        except Exception as error:
            raise ArchiveError("archive_integrity_failed") from error
    if str(manifest.get("dataset_id")) != str(dataset_id) or manifest.get("revision") != revision:
        raise ArchiveError("archive_integrity_failed")
    return manifest


def _manifest_windows(manifest: Mapping[str, Any]) -> tuple[CaptureWindow, ...]:
    raw = manifest.get("scope_windows")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ArchiveError("archive_integrity_failed")
    windows: list[CaptureWindow] = []
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("room"), str):
            raise ArchiveError("archive_integrity_failed")
        start = _instant(item.get("start"))
        end = _instant(item.get("end"))
        if start >= end:
            raise ArchiveError("archive_integrity_failed")
        windows.append(CaptureWindow(item["room"], start, end))
    return tuple(windows)


def _instant(value: object) -> datetime:
    try:
        instant = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ArchiveError("archive_integrity_failed") from error
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ArchiveError("archive_integrity_failed")
    return instant.astimezone(UTC)


def _source_payload_counts(
    path: Path, stream: SourceStream, checkpoint: Callable[[], None],
) -> Counter[bytes]:
    counts: Counter[bytes] = Counter()
    field_count = len(stream.columns)
    with path.open("rb") as source:
        for index, fields in enumerate(rows(source), start=1):
            if len(fields) != field_count + 4:
                raise ArchiveError("archive_integrity_failed")
            if decode(fields[field_count + 3], "text") != "observation":
                raise ArchiveError("archive_integrity_failed")
            counts[canonical(fields[:field_count])] += 1
            if index % 1024 == 0:
                checkpoint()
    checkpoint()
    return counts


def _archive_payload_counts(
    path: Path,
    artifact: ArchiveManifest,
    stream: SourceStream,
    intervals: tuple[tuple[datetime, datetime], ...],
    component_dataset_id: UUID,
    checkpoint: Callable[[], None],
) -> dict[tuple[datetime, datetime], Counter[bytes]]:
    """Index the verified ZIP rows for only the rebuilt time windows."""
    ordered = tuple(sorted(set(intervals)))
    counts = {interval: Counter() for interval in ordered}
    if not ordered:
        return counts
    starts = [start for start, _end in ordered]
    field_count = len(stream.columns)
    time_index = next(
        (index for index, column in enumerate(stream.columns) if column.name == stream.time_column),
        None,
    )
    if time_index is None:
        raise ArchiveError("schema_descriptor_unavailable")
    time_type = stream.columns[time_index].type
    try:
        for batch in iter_archive_binary_rows(
            path,
            artifact,
            stream.name,
            component_dataset_id=component_dataset_id,
            checkpoint=checkpoint,
        ):
            for fields in batch:
                if len(fields) != field_count + 4:
                    raise ArchiveError("archive_integrity_failed")
                if decode(fields[field_count + 3], "text") != "observation":
                    continue
                observed = decode(fields[time_index], time_type)
                if not isinstance(observed, datetime):
                    raise ArchiveError("archive_integrity_failed")
                index = bisect_right(starts, observed) - 1
                if index < 0:
                    continue
                interval = ordered[index]
                if observed < interval[1]:
                    counts[interval][canonical(fields[:field_count])] += 1
            checkpoint()
    except ArchiveIntegrityError as error:
        raise ArchiveError("archive_integrity_failed") from error
    return counts
