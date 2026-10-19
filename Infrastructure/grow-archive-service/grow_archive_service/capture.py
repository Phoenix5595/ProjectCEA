"""Fenced, lossless source-table capture for archive exports."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
from shared.grow_lifecycle import calendar_windows

from .binary_codec import canonical, rows, write_header, write_row, write_trailer
from .execution import HeavyWorkExecutor
from .jobs import ArchiveJobQueue
from .manifest import ArchiveManifest
from .membership import (
    CaptureWindow,
    identity_snapshots,
    load_windows,
    merge_windows,
    require_resolved_scope,
    selection_sql,
    window_arrays,
)
from .models import ArchiveError, JobClaim
from .pg_fields import decode, encode
from .schema import DEFAULT_CATALOG_PATH, SourceStream, assert_schema_preflight, load_source_catalog
from .source_retention import removable_observation_sql
from .streams import time_nullable

TORONTO = ZoneInfo("America/Toronto")
UTC_HOUR = timedelta(hours=1)
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_COPY_ROLE_INDEX_OFFSET = 3


@dataclass(frozen=True, slots=True)
class CaptureResult:
    dataset_id: UUID
    revision: int
    manifest: Mapping[str, Any]
    capture_dir: Path


@dataclass(frozen=True, slots=True)
class PriorArchive:
    path: Path
    manifest: ArchiveManifest
    component_dataset_id: UUID | None


@dataclass(slots=True)
class _ObservationSlice:
    start: datetime
    end: datetime
    toronto_date: str
    path: Path
    relative_path: str
    source_path: Path | None
    use_prior: bool
    prior_path: Path | None = None


def part_metrics(path: Path, checkpoint: Callable[[], None]) -> tuple[int, int, str]:
    """Return row count, decoded binary bytes and digest without a page limit."""
    digest = hashlib.sha256()
    count = 0
    decoded_bytes = 0
    with Path(path).open("rb") as stream:
        for fields in rows(stream):
            encoded = canonical(fields)
            digest.update(encoded)
            decoded_bytes += len(encoded)
            count += 1
            if count % 1024 == 0:
                checkpoint()
    checkpoint()
    return count, decoded_bytes, digest.hexdigest()


def part_query(
    stream: SourceStream,
    windows: Sequence[CaptureWindow],
    dataset_id: UUID,
    revision: int,
    start: datetime,
    end: datetime,
    *,
    operation: Literal["copy", "delete", "exists"] = "copy",
    cutoff: datetime | None = None,
    record_role: Literal["observation", "context"] | None = None,
    anchor_relation: str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """Build the identical membership-qualified source query used for capture and deletion."""
    if stream.time_column is None or not windows or start >= end:
        raise ArchiveError("schema_descriptor_unavailable")
    if operation not in ("copy", "delete", "exists"):
        raise ValueError("unsupported archive part query operation")
    if record_role not in (None, "observation", "context"):
        raise ValueError("unsupported archive record role")
    if operation in ("delete", "exists") and (cutoff is None or record_role != "observation"):
        raise ValueError("deletion queries require an observation role and cutoff")

    table = _identifier(stream.name)
    time_column = _identifier(stream.time_column)
    columns = ",".join(f'm."{_identifier(column.name)}"' for column in stream.columns)
    row_record = f"ROW({columns})"
    predicate, role = selection_sql(stream)
    rooms, starts, ends = window_arrays(windows)
    time_sql = f'm."{time_column}"'

    if operation == "copy":
        role_filter = f" AND ({role})='{record_role}'" if record_role else ""
        sql = (
            f"SELECT {columns},$4::uuid,$5::bigint,"
            f"(row_number() OVER (PARTITION BY record_send({row_record}) "
            f"ORDER BY {time_sql})-1)::integer,{role} "
            f'FROM public."{table}" m WHERE {predicate} '
            f"AND {time_sql}>=$6::timestamptz AND {time_sql}<$7::timestamptz"
            f"{role_filter} ORDER BY {time_sql},record_send({row_record})"
        )
        return sql, (rooms, starts, ends, dataset_id, revision, start, end)

    assert cutoff is not None
    removable = removable_observation_sql(
        stream, alias="m", cutoff="$6", anchor_relation=anchor_relation,
    )
    filter_sql = (
        f"{predicate} AND {time_sql}>=$4::timestamptz AND {time_sql}<$5::timestamptz "
        f"AND ({role})='observation' AND {removable}"
    )
    args = (rooms, starts, ends, start, end, cutoff)
    if operation == "delete":
        return f'DELETE FROM public."{table}" AS m WHERE {filter_sql}', args
    return f'SELECT EXISTS(SELECT 1 FROM public."{table}" m WHERE {filter_sql})', args


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ArchiveError("schema_descriptor_unavailable")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ArchiveError("archive_integrity_failed")
    return value.astimezone(UTC)


def _instant(value: object) -> datetime:
    try:
        instant = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ArchiveError("archive_integrity_failed") from error
    return _utc(instant)


def _split_copy_by_role(
    source_path: Path,
    capture_dir: Path,
    stream: SourceStream,
    toronto_date: str,
    observation_indices: dict[str, int],
    context_index: list[int],
    checkpoint: Callable[[], None],
) -> dict[str, tuple[Path, str]]:
    """Split one query COPY into observation/context members without decoding source values."""
    source_count = len(stream.columns)
    role_position = source_count + _COPY_ROLE_INDEX_OFFSET
    handles: dict[str, Any] = {}
    targets: dict[str, tuple[Path, str]] = {}
    try:
        with source_path.open("rb") as source:
            for index, fields in enumerate(rows(source), start=1):
                if len(fields) != source_count + 4:
                    raise ArchiveError("archive_integrity_failed")
                role = decode(fields[role_position], "text")
                if role not in ("observation", "context"):
                    raise ArchiveError("archive_integrity_failed")
                if role not in handles:
                    if role == "observation":
                        part_number = observation_indices.get(toronto_date, 0)
                        observation_indices[toronto_date] = part_number + 1
                        relative = Path(stream.name) / toronto_date / f"part-{part_number:05d}.copy"
                    else:
                        part_number = context_index[0]
                        context_index[0] += 1
                        relative = Path(stream.name) / "context" / f"part-{part_number:05d}.copy"
                    target = capture_dir / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    handle = target.open("wb")
                    write_header(handle)
                    handles[role] = handle
                    targets[role] = (target, relative.as_posix())
                write_row(handles[role], fields)
                if index % 1024 == 0:
                    checkpoint()
        for handle in handles.values():
            write_trailer(handle)
            handle.close()
        checkpoint()
        return targets
    except Exception:
        for handle in handles.values():
            if not handle.closed:
                handle.close()
        for path, _relative in targets.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        source_path.unlink(missing_ok=True)


def _null_context_query(
    stream: SourceStream,
    windows: Sequence[CaptureWindow],
    dataset_id: UUID,
    revision: int,
    partition_at: datetime,
) -> tuple[str, tuple[Any, ...]]:
    if stream.time_column is None:
        raise ArchiveError("schema_descriptor_unavailable")
    columns = ",".join(f'm."{_identifier(column.name)}"' for column in stream.columns)
    row_record = f"ROW({columns})"
    predicate, role = selection_sql(stream, include_null_time=True)
    rooms, _starts, _ends = window_arrays(windows)
    sql = (
        f"SELECT {columns},$2::uuid,$3::bigint,"
        f"(row_number() OVER (PARTITION BY record_send({row_record}) "
        f"ORDER BY m.\"{_identifier(stream.time_column)}\")-1)::integer,{role},"
        "$4::timestamptz "
        f'FROM public."{_identifier(stream.name)}" m WHERE {predicate} '
        f"ORDER BY record_send({row_record})"
    )
    return sql, (rooms, dataset_id, revision, partition_at)


def _slice_has_removed_rows(
    availability: Sequence[Mapping[str, Any]], stream_name: str, window: CaptureWindow,
    start: datetime, end: datetime,
) -> bool:
    return any(
        row["source_table"] == stream_name
        and (row["room"] is None or row["room"] == window.room)
        and row["start_at"] < end
        and row["end_at"] > start
        for row in availability
    )


def _scope_gaps(
    expiry_rows: Sequence[Mapping[str, Any]], windows: Sequence[CaptureWindow],
) -> list[dict[str, Any]]:
    gaps: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in expiry_rows:
        for window in windows:
            if row["room"] not in (None, window.room):
                continue
            start = max(row["start_at"], window.start)
            end = min(row["end_at"], window.end)
            if start >= end:
                continue
            table = str(row["source_table"])
            key = (start.isoformat(), end.isoformat(), table)
            gaps[key] = {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "reason": "source_expired",
                "source_tables": [table],
            }
    return [gaps[key] for key in sorted(gaps)]


def _route_prior_rows(
    prior: PriorArchive,
    stream: SourceStream,
    slices: Sequence[_ObservationSlice],
    capture_dir: Path,
    checkpoint: Callable[[], None],
) -> None:
    if not slices or not any(item.use_prior for item in slices):
        return
    from .parquet_codec import iter_archive_binary_rows

    starts = [item.start for item in slices]
    time_position = next(
        (index for index, column in enumerate(stream.columns) if column.name == stream.time_column),
        None,
    )
    if time_position is None:
        raise ArchiveError("schema_descriptor_unavailable")
    role_position = len(stream.columns) + _COPY_ROLE_INDEX_OFFSET
    current_index = -1
    output = None
    last_time: datetime | None = None
    routed = 0
    try:
        for batch in iter_archive_binary_rows(
            prior.path,
            prior.manifest,
            stream.name,
            component_dataset_id=prior.component_dataset_id,
            checkpoint=checkpoint,
        ):
            for archived in batch:
                role = decode(archived[role_position], "text")
                if role != "observation":
                    continue
                instant = decode(archived[time_position], next(
                    column.type for column in stream.columns if column.name == stream.time_column
                ))
                if not isinstance(instant, datetime):
                    raise ArchiveError("archive_integrity_failed")
                instant = _utc(instant)
                if last_time is not None and instant < last_time:
                    raise ArchiveError("archive_integrity_failed")
                last_time = instant
                index = bisect_right(starts, instant) - 1
                if index < 0:
                    continue
                item = slices[index]
                if not item.use_prior or not item.start <= instant < item.end:
                    continue
                if index < current_index:
                    raise ArchiveError("archive_integrity_failed")
                if index != current_index:
                    if output is not None:
                        write_trailer(output)
                        output.close()
                    current_index = index
                    item.prior_path = capture_dir / ".prior" / stream.name / f"slice-{index:07d}.copy"
                    item.prior_path.parent.mkdir(parents=True, exist_ok=True)
                    output = item.prior_path.open("wb")
                    write_header(output)
                write_row(output, tuple(archived[:len(stream.columns)]))
                routed += 1
                if routed % 1024 == 0:
                    checkpoint()
        if output is not None:
            write_trailer(output)
            output.close()
            output = None
        checkpoint()
    except Exception:
        if output is not None and not output.closed:
            output.close()
        for item in slices:
            if item.prior_path is not None:
                item.prior_path.unlink(missing_ok=True)
        raise


def _time_key(fields: tuple[bytes | None, ...], stream: SourceStream) -> datetime:
    index = next(
        (position for position, column in enumerate(stream.columns) if column.name == stream.time_column),
        None,
    )
    if index is None:
        raise ArchiveError("schema_descriptor_unavailable")
    value = decode(fields[index], stream.columns[index].type)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ArchiveError("archive_integrity_failed")
    return value.astimezone(UTC)


def _rewrite_observation_slice(
    item: _ObservationSlice,
    stream: SourceStream,
    dataset_id: UUID,
    revision: int,
    capture_dir: Path,
    checkpoint: Callable[[], None],
) -> bool:
    """Rebuild one partially removed slice from prior ZIP rows plus source-minus-ZIP."""
    previous_rows: list[tuple[bytes | None, ...]] = []
    previous_counts: Counter[bytes] = Counter()
    if item.prior_path is not None:
        with item.prior_path.open("rb") as source:
            for archived in rows(source):
                if len(archived) != len(stream.columns):
                    raise ArchiveError("archive_integrity_failed")
                previous_rows.append(archived)
                previous_counts[canonical(archived)] += 1
                if len(previous_rows) % 1024 == 0:
                    checkpoint()

    current_rows: list[tuple[bytes | None, ...]] = []
    if item.source_path is not None:
        with item.source_path.open("rb") as source:
            for fields in rows(source):
                if len(fields) != len(stream.columns) + 4:
                    raise ArchiveError("archive_integrity_failed")
                if decode(fields[len(stream.columns) + _COPY_ROLE_INDEX_OFFSET], "text") != "observation":
                    raise ArchiveError("archive_integrity_failed")
                current_rows.append(tuple(fields[:len(stream.columns)]))
                if len(current_rows) % 1024 == 0:
                    checkpoint()

    rebuilt = list(previous_rows)
    for source_row in current_rows:
        payload = canonical(source_row)
        if previous_counts[payload]:
            previous_counts[payload] -= 1
        else:
            rebuilt.append(source_row)

    if not rebuilt:
        item.path.unlink(missing_ok=True)
        if item.source_path is not None and item.source_path != item.path:
            item.source_path.unlink(missing_ok=True)
        if item.prior_path is not None:
            item.prior_path.unlink(missing_ok=True)
        return False

    rebuilt.sort(key=lambda fields: (_time_key(fields, stream), canonical(fields)))
    temporary = capture_dir / ".rebuild" / f"{stream.name}-{item.path.stem}.copy"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    occurrences: Counter[bytes] = Counter()
    try:
        with temporary.open("wb") as target:
            write_header(target)
            for index, source_row in enumerate(rebuilt, start=1):
                payload = canonical(source_row)
                occurrence = occurrences[payload]
                occurrences[payload] += 1
                metadata = (
                    encode(dataset_id, "uuid"),
                    encode(revision, "bigint"),
                    encode(occurrence, "integer"),
                    encode("observation", "text"),
                )
                write_row(target, source_row + metadata)
                if index % 1024 == 0:
                    checkpoint()
            write_trailer(target)
        temporary.replace(item.path)
    finally:
        temporary.unlink(missing_ok=True)
        if item.source_path is not None and item.source_path != item.path:
            item.source_path.unlink(missing_ok=True)
        if item.prior_path is not None:
            item.prior_path.unlink(missing_ok=True)
    checkpoint()
    return True


class SourceCapture:
    def __init__(self, pool: asyncpg.Pool, heavy: HeavyWorkExecutor) -> None:
        self.pool = pool
        self.heavy = heavy
        self.queue = ArchiveJobQueue(pool)

    async def capture(
        self,
        claim: JobClaim,
        directory: Path,
        *,
        prior: PriorArchive | None,
    ) -> CaptureResult:
        dataset_id = claim.fence.dataset_id
        revision = claim.fence.data_revision
        directory = Path(directory)
        capture_dir = directory / "capture"
        capture_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        catalog = load_source_catalog()
        raw_catalog = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
        descriptors = {item["name"]: item for item in raw_catalog["streams"]}

        async with self.pool.acquire() as conn:
            report = await assert_schema_preflight(conn, catalog=catalog)
            streams = tuple(
                stream for stream in catalog.defined_streams()
                if report.stream(stream.name).status == "ok"
            )
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                await self.queue.check_fence(conn, claim)
                row = await conn.fetchrow(
                    "SELECT d.*,g.state AS lifecycle_state FROM archive_dataset d "
                    "LEFT JOIN grow_lifecycle g USING(grow_plan_id) WHERE dataset_id=$1",
                    dataset_id,
                )
                if row is None:
                    raise ArchiveError("archive_job_superseded")
                dataset = dict(row)
                if dataset.get("data_revision") != revision:
                    raise ArchiveError("archive_job_superseded")
                windows = merge_windows(await load_windows(conn, dataset))
                await require_resolved_scope(conn, streams, windows)
                snapshots = await identity_snapshots(conn, streams, windows)
                events = await conn.fetch(
                    "SELECT id,grow_plan_id,event_type,location,start_date,end_date,deleted_at "
                    "FROM calendar_event WHERE grow_plan_id=$1 ORDER BY start_date,id",
                    dataset["grow_plan_id"],
                ) if dataset["grow_plan_id"] else []
                event_ids = {event["id"] for event in events}
                overrides = [dict(item) for item in await conn.fetch(
                    "SELECT source_table,source_identity,room,start_at,end_at "
                    "FROM archive_scope_override"
                )]
                removed_availability = [dict(item) for item in await conn.fetch(
                    "SELECT source_table,room,start_at,end_at FROM archive_availability "
                    "WHERE dataset_id=$1 AND source_rows_removed",
                    dataset_id,
                )]
                expired_rows = await conn.fetch(
                    "SELECT source_table,room,start_at,end_at FROM archive_availability "
                    "WHERE state='expired' AND start_at<$2 AND end_at>$1 "
                    "AND (room IS NULL OR room=ANY($3::text[]))",
                    min(window.start for window in windows),
                    max(window.end for window in windows),
                    sorted({window.room for window in windows}),
                )
                gaps = _scope_gaps([dict(item) for item in expired_rows], windows)
                if prior is not None:
                    _resolve_prior_manifest(prior, dataset_id, dataset.get("published_revision"))

                parts: list[dict[str, Any]] = []
                query_index = 0
                for stream in streams:
                    if stream.name not in descriptors:
                        raise ArchiveError("schema_descriptor_unavailable")
                    if stream.time_column is None:
                        raise ArchiveError("schema_descriptor_unavailable")
                    is_time_nullable = await time_nullable(conn, stream.name, stream.time_column)
                    observation_indices: dict[str, int] = {}
                    context_index = [0]
                    observation_slices: list[_ObservationSlice] = []
                    table = _identifier(stream.name)
                    time_column = _identifier(stream.time_column)
                    predicate, _role = selection_sql(stream)

                    for window in windows:
                        rooms, starts, ends = window_arrays((window,))
                        source_bounds = await conn.fetchrow(
                            f'SELECT min(m."{time_column}") AS first_at,'
                            f'max(m."{time_column}") AS last_at '
                            f'FROM public."{table}" m WHERE {predicate}',
                            rooms, starts, ends,
                        )
                        hour_bounds: list[tuple[datetime, datetime]] = []
                        if source_bounds["first_at"] is not None:
                            hour_bounds.append((source_bounds["first_at"], source_bounds["last_at"]))
                        if prior is not None:
                            for availability in removed_availability:
                                if (availability["source_table"] != stream.name
                                        or availability["room"] not in (None, window.room)):
                                    continue
                                first_at = max(availability["start_at"], window.start)
                                last_at = min(availability["end_at"], window.end)
                                if first_at < last_at:
                                    hour_bounds.append((first_at, last_at - timedelta(microseconds=1)))
                        if not hour_bounds:
                            continue
                        first_at = min(bound[0] for bound in hour_bounds)
                        last_at = max(bound[1] for bound in hour_bounds)
                        cursor = first_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
                        last_hour = last_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
                        while cursor <= last_hour:
                            slice_start = max(cursor, window.start.astimezone(UTC))
                            slice_end = min(cursor + UTC_HOUR, window.end.astimezone(UTC))
                            if slice_start >= slice_end:
                                cursor += UTC_HOUR
                                continue
                            toronto_date = slice_start.astimezone(TORONTO).date().isoformat()
                            relative = Path(stream.name) / toronto_date / "part-00000.copy"
                            final_path = capture_dir / relative
                            sql_path = capture_dir / ".queries" / f"{stream.name}-{query_index:07d}.copy"
                            sql_path.parent.mkdir(parents=True, exist_ok=True)
                            query_index += 1
                            sql, args = part_query(
                                stream, (window,), dataset_id, revision, slice_start, slice_end,
                            )
                            started = time.monotonic()
                            await conn.copy_from_query(sql, *args, output=str(sql_path), format="binary")
                            await self.heavy.throttle_async(time.monotonic() - started)
                            role_files = await self.heavy.run(lambda control, p=sql_path, d=toronto_date:
                                _split_copy_by_role(
                                    p, capture_dir, stream, d, observation_indices,
                                    context_index, control.checkpoint,
                                ))
                            observation_path = role_files.get("observation", (None, ""))[0]
                            observation_relative = role_files.get("observation", (None, ""))[1]
                            if observation_path is None:
                                part_number = observation_indices.get(toronto_date, 0)
                                observation_indices[toronto_date] = part_number + 1
                                relative = Path(stream.name) / toronto_date / f"part-{part_number:05d}.copy"
                                final_path = capture_dir / relative
                                source_path = None
                            else:
                                final_path = observation_path
                                relative = Path(observation_relative)
                                source_path = observation_path
                            slice_item = _ObservationSlice(
                                slice_start,
                                slice_end,
                                toronto_date,
                                final_path,
                                relative.as_posix(),
                                source_path,
                                prior is not None and _slice_has_removed_rows(
                                    removed_availability, stream.name, window, slice_start, slice_end,
                                ),
                            )
                            observation_slices.append(slice_item)
                            if prior is None:
                                if source_path is not None:
                                    count, _decoded_bytes, digest = await self.heavy.run(
                                        lambda control, p=source_path: part_metrics(p, control.checkpoint)
                                    )
                                    if count:
                                        parts.append({
                                            "path": slice_item.relative_path,
                                            "source_table": stream.name,
                                            "record_role": "observation",
                                            "toronto_date": toronto_date,
                                            "row_count": count,
                                            "canonical_sha256": digest,
                                            "partition_field": False,
                                            "window_start": slice_start.isoformat(),
                                            "window_end": slice_end.isoformat(),
                                        })
                                else:
                                    observation_slices.pop()

                            for role, (part_path, part_relative) in role_files.items():
                                if role != "context":
                                    continue
                                count, _decoded_bytes, digest = await self.heavy.run(
                                    lambda control, p=part_path: part_metrics(p, control.checkpoint)
                                )
                                if count:
                                    parts.append({
                                        "path": part_relative,
                                        "source_table": stream.name,
                                        "record_role": "context",
                                        "toronto_date": None,
                                        "row_count": count,
                                        "canonical_sha256": digest,
                                        "partition_field": False,
                                        "window_start": None,
                                        "window_end": None,
                                    })
                                else:
                                    part_path.unlink(missing_ok=True)
                            cursor += UTC_HOUR

                    if prior is not None and observation_slices:
                        await self.heavy.run(lambda control, s=stream, seq=tuple(observation_slices):
                            _route_prior_rows(prior, s, seq, capture_dir, control.checkpoint))
                        for slice_item in observation_slices:
                            if not slice_item.use_prior:
                                if slice_item.source_path is not None:
                                    count, _decoded_bytes, digest = await self.heavy.run(
                                        lambda control, p=slice_item.source_path: part_metrics(p, control.checkpoint)
                                    )
                                    if count:
                                        parts.append({
                                            "path": slice_item.relative_path,
                                            "source_table": stream.name,
                                            "record_role": "observation",
                                            "toronto_date": slice_item.toronto_date,
                                            "row_count": count,
                                            "canonical_sha256": digest,
                                            "partition_field": False,
                                            "window_start": slice_item.start.isoformat(),
                                            "window_end": slice_item.end.isoformat(),
                                        })
                                    else:
                                        slice_item.source_path.unlink(missing_ok=True)
                                continue
                            has_rows = await self.heavy.run(lambda control, item=slice_item, s=stream:
                                _rewrite_observation_slice(
                                    item, s, dataset_id, revision, capture_dir, control.checkpoint,
                                ))
                            if not has_rows:
                                continue
                            count, _decoded_bytes, digest = await self.heavy.run(
                                lambda control, p=slice_item.path: part_metrics(p, control.checkpoint)
                            )
                            if count:
                                parts.append({
                                    "path": slice_item.relative_path,
                                    "source_table": stream.name,
                                    "record_role": "observation",
                                    "toronto_date": slice_item.toronto_date,
                                    "row_count": count,
                                    "canonical_sha256": digest,
                                    "partition_field": False,
                                    "window_start": slice_item.start.isoformat(),
                                    "window_end": slice_item.end.isoformat(),
                                })

                    if is_time_nullable:
                        null_sql_path = capture_dir / ".queries" / f"{stream.name}-null.copy"
                        sql, args = _null_context_query(
                            stream, windows, dataset_id, revision,
                            min(window.start for window in windows),
                        )
                        started = time.monotonic()
                        await conn.copy_from_query(sql, *args, output=str(null_sql_path), format="binary")
                        await self.heavy.throttle_async(time.monotonic() - started)
                        count, _decoded_bytes, digest = await self.heavy.run(
                            lambda control, p=null_sql_path: part_metrics(p, control.checkpoint)
                        )
                        if count:
                            part_number = context_index[0]
                            context_index[0] += 1
                            relative = Path(stream.name) / "context" / f"part-{part_number:05d}.copy"
                            final_path = capture_dir / relative
                            final_path.parent.mkdir(parents=True, exist_ok=True)
                            null_sql_path.replace(final_path)
                            parts.append({
                                "path": relative.as_posix(),
                                "source_table": stream.name,
                                "record_role": "context",
                                "toronto_date": None,
                                "row_count": count,
                                "canonical_sha256": digest,
                                "partition_field": True,
                                "window_start": None,
                                "window_end": None,
                            })
                        else:
                            null_sql_path.unlink(missing_ok=True)

                observations: dict[str, int] = {}
                contexts: dict[str, int] = {}
                per_day: dict[str, dict[str, int]] = {}
                for part in parts:
                    table = part["source_table"]
                    if part["record_role"] == "observation":
                        observations[table] = observations.get(table, 0) + part["row_count"]
                        days = per_day.setdefault(table, {})
                        day = part["toronto_date"]
                        days[day] = days.get(day, 0) + part["row_count"]
                    elif part["record_role"] == "context":
                        contexts[table] = contexts.get(table, 0) + part["row_count"]

                phases = [
                    {
                        "event_id": event.event_id,
                        "phase_type": event.phase_type,
                        "room": event.room,
                        "start_at": event.start_at.isoformat(),
                        "end_at": event.end_at.isoformat(),
                        "start_date": event.start_date.isoformat(),
                        "end_date": event.end_date.isoformat() if event.end_date else None,
                    }
                    for event in calendar_windows([dict(item) for item in events if item["deleted_at"] is None])
                ]
                manifest: dict[str, Any] = {
                    "format": "sj-cea-history/1",
                    "dataset_id": str(dataset_id),
                    "revision": revision,
                    "calendar_revision": dataset["calendar_revision"],
                    "kind": dataset["kind"],
                    "grow_plan_id": str(dataset["grow_plan_id"]) if dataset["grow_plan_id"] else None,
                    "room": dataset["room"],
                    "timezone": "America/Toronto",
                    "available_from": min(window.start for window in windows).isoformat(),
                    "available_to": max(window.end for window in windows).isoformat(),
                    "original_anniversary_at": (
                        dataset["anniversary_at"].isoformat() if dataset["anniversary_at"] else None
                    ),
                    "phases": phases,
                    "identity_snapshots": snapshots,
                    "scope_windows": [
                        {"room": window.room, "start": window.start.isoformat(), "end": window.end.isoformat()}
                        for window in windows
                    ],
                    "scope_overrides": [
                        item for item in overrides
                        if any(
                            item["start_at"] < window.end and item["end_at"] > window.start
                            for window in windows
                        )
                    ],
                    "scope_event_ids": sorted(event_ids),
                    "source_schema": {stream.name: descriptors[stream.name] for stream in streams},
                    "absent_streams": [
                        item.name for item in catalog.streams
                        if report.stream(item.name).status == "absent"
                    ],
                    "graph_schema": {},
                    "coverage": "gapped" if gaps else "complete",
                    "gaps": gaps,
                    "parts": parts,
                    "observation_counts": observations,
                    "context_counts": contexts,
                    "per_day_counts": per_day,
                }

        from .graph_builder import build_graph_parts

        graph = await build_graph_parts(self.pool, self.heavy.run, capture_dir, manifest)
        manifest["graph_schema"] = graph["graph_schema"]
        manifest["parts"].extend(graph["parts"])
        _write_metadata(capture_dir, manifest)
        return CaptureResult(dataset_id, revision, manifest, capture_dir)


def _resolve_prior_manifest(
    prior: PriorArchive, dataset_id: UUID, published_revision: int | None,
) -> Mapping[str, Any]:
    if prior.component_dataset_id is None:
        child = prior.manifest.data
    else:
        from .annual_codec import annual_component

        child, _prefix = annual_component(prior.manifest, prior.component_dataset_id)
    if (
        str(child.get("dataset_id")) != str(dataset_id)
        or child.get("revision") != published_revision
        or prior.component_dataset_id not in (None, dataset_id)
    ):
        raise ArchiveError("archive_integrity_failed")
    return child


def _write_metadata(capture_dir: Path, manifest: Mapping[str, Any]) -> None:
    path = capture_dir / "metadata.json"
    encoded = json.dumps(
        manifest,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
    )
    path.write_text(encoded, encoding="utf-8")
