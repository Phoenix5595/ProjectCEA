"""Explicit archive graph reads from verified ZIP parts."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import zipfile
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg

from grow_archive_service.annual_codec import annual_component
from grow_archive_service.api_models import (
    ArchiveGap, ArchiveSelectionMeta, PhotoperiodCoverageFact, PhotoperiodFact,
)
from grow_archive_service.arrow_fields import arrow_schema, binary_rows_from_batch
from grow_archive_service.binary_codec import canonical, write_header, write_row, write_trailer
from grow_archive_service.graph_builder import _create_source_page, sensor_identity_rows
from grow_archive_service.models import ArchiveError
from grow_archive_service.parquet_codec import _arrow, _columns, _safe_member, _validate_row
from grow_archive_service.pg_fields import decode
from monitoring_service.history_buckets import (
    choose_history_bucket, history_bucket_bounds, validate_history_budget,
)
from monitoring_service.history_overview_models import (
    HistoryOverviewControlSeries, HistoryOverviewMetadata, HistoryOverviewPoint,
    HistoryOverviewRange, HistoryOverviewSensorResponse, HistoryOverviewSensorSeries,
    HistoryOverviewSensorStatistics,
)
from monitoring_service.sensor_models import (
    MonitoringMetadata, MonitoringRange, MonitoringResponse, MonitoringValidationError, Node, SensorSeries,
    SensorStatistics, SeriesPoint, StddevQuality, Tier, resolve_interval_seconds,
    resolve_room_metadata, resolve_tier,
)
from monitoring_service.sensor_rows import unit_families
from shared.history_summary import SummaryState, float_or_none

TORONTO = ZoneInfo('America/Toronto')
MAX_ROWS = 4096
MAX_COPY_BYTES = 8 * 1024 * 1024




def _instant(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        raise ArchiveError('archive_integrity_failed')
    if result.tzinfo is None or result.utcoffset() is None:
        raise ArchiveError('archive_integrity_failed')
    return result.astimezone(UTC)


def _zip_component_manifests(pin: Any) -> tuple[dict[str, Any], tuple[tuple[dict[str, Any], str, UUID, int], ...], Any]:
    verified = getattr(pin, 'manifest', None)
    if verified is None or not isinstance(getattr(verified, 'data', None), Mapping):
        raise ArchiveError('archive_integrity_failed')
    container = dict(verified.data)
    requested_prefix = getattr(pin, 'member_prefix', None) or ''
    try:
        container_id = UUID(str(container['dataset_id']))
        container_revision = int(container['revision'])
        if container.get('kind') == 'veg_year':
            if requested_prefix:
                child, prefix = annual_component(verified, pin.dataset_id)
                if (container_id == pin.dataset_id or prefix != requested_prefix
                        or int(child['revision']) != int(pin.revision)):
                    raise ArchiveError('archive_integrity_failed')
                child = dict(child)
                return child, ((child, prefix, pin.dataset_id, int(pin.revision)),), verified
            if container_id != pin.dataset_id or container_revision != int(pin.revision):
                raise ArchiveError('archive_integrity_failed')
            components = container.get('components')
            if not isinstance(components, list) or len(components) != 4:
                raise ArchiveError('archive_integrity_failed')
            result = []
            for component in components:
                child_id = UUID(str(component['dataset_id']))
                child, prefix = annual_component(verified, child_id)
                result.append((dict(child), prefix, child_id, int(child['revision'])))
            return container, tuple(result), verified
        if (requested_prefix or container_id != pin.dataset_id
                or container_revision != int(pin.revision)):
            raise ArchiveError('archive_integrity_failed')
        return container, ((container, '', container_id, container_revision),), verified
    except (KeyError, ValueError, TypeError) as error:
        raise ArchiveError('archive_integrity_failed') from error


def _epoch_floor(value: datetime, seconds: int) -> datetime:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    micros = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    step = seconds * 1_000_000
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=(micros // step) * step)


def _epoch_ceil(value: datetime, seconds: int) -> datetime:
    floor = _epoch_floor(value, seconds)
    return floor if floor == value else floor + timedelta(seconds=seconds)


def _targets(start: datetime, end: datetime, *, overview: bool, max_points: int) -> tuple[
        tuple[tuple[datetime, datetime, bool], ...], int, int, Any, Any]:
    duration = end - start
    if overview:
        budget = validate_history_budget(max_points)
        history_range = HistoryOverviewRange(start=start, end=end)
        bucket = choose_history_bucket(start, end, budget)
        return history_bucket_bounds(start, end, bucket), 3600, bucket.seconds, history_range, bucket
    monitoring_range = MonitoringRange(start=start, end=end)
    tier = resolve_tier(duration, max_points)
    base_seconds = {Tier.RAW: 0, Tier.ONE_MINUTE: 60, Tier.FIVE_MINUTES: 300}[tier]
    interval = resolve_interval_seconds(math.ceil(duration.total_seconds()), max_points,
                                        max(base_seconds, 1))
    bounds: list[tuple[datetime, datetime, bool]] = []
    offset = 0
    while start + timedelta(seconds=offset) < end:
        left = start + timedelta(seconds=offset)
        right = min(end, left + timedelta(seconds=interval))
        bounds.append((left, right, right - left < timedelta(seconds=interval)))
        offset += interval
    return tuple(bounds), base_seconds, interval, monitoring_range, tier


def _edge_spans(bounds: Sequence[tuple[datetime, datetime, bool]], width: int) -> tuple[
        tuple[int, datetime, datetime], ...]:
    if width <= 0:
        return tuple((idx, start, end) for idx, (start, end, _) in enumerate(bounds))
    spans: list[tuple[int, datetime, datetime]] = []
    for index, (start, end, _) in enumerate(bounds):
        full_start = _epoch_ceil(start, width)
        full_end = _epoch_floor(end, width)
        if full_start >= full_end:
            spans.append((index, start, end))
            continue
        if start < full_start:
            spans.append((index, start, full_start))
        if full_end < end:
            spans.append((index, full_end, end))
    return tuple(spans)


def _full_spans(bounds: Sequence[tuple[datetime, datetime, bool]], width: int) -> tuple[
        tuple[int, datetime, datetime], ...]:
    if width <= 0:
        return ()
    spans = []
    for index, (start, end, _) in enumerate(bounds):
        left, right = _epoch_ceil(start, width), _epoch_floor(end, width)
        if left < right:
            spans.append((index, left, right))
    return tuple(spans)


def _bucket_index(instant: datetime, bounds: Sequence[tuple[datetime, datetime, bool]],
                  starts: Sequence[datetime]) -> int | None:
    index = bisect_right(starts, instant) - 1
    if index >= 0 and bounds[index][0] <= instant < bounds[index][1]:
        return index
    return None


def _add_state(states: dict[tuple[str, str, int], SummaryState], kind: str,
               series_id: str, index: int, row: Mapping[str, Any]) -> None:
    state = states.setdefault((kind, series_id, index), SummaryState())
    state.apply(row, minimum_key='minimum', maximum_key='maximum')


def _json_series(value: str) -> list[Any]:
    try:
        decoded = json.loads(value)
    except (ValueError, TypeError) as error:
        raise ArchiveError('archive_integrity_failed') from error
    if not isinstance(decoded, list):
        raise ArchiveError('archive_integrity_failed')
    return decoded


def _gap_values(manifest: Mapping[str, Any]) -> tuple[ArchiveGap, ...]:
    result = []
    for gap in manifest.get('gaps', ()):
        if not isinstance(gap, Mapping):
            continue
        lo, hi = gap.get('start', gap.get('start_at')), gap.get('end', gap.get('end_at'))
        if lo is None or hi is None:
            continue
        try:
            start, end = _instant(lo), _instant(hi)
        except (ValueError, TypeError, ArchiveError):
            continue
        if start < end:
            tables = gap.get('source_tables', gap.get('source_table', ()))
            if isinstance(tables, str):
                tables = (tables,)
            result.append(ArchiveGap(start=start, end=end,
                reason=str(gap.get('reason', gap.get('code', 'recorded_gap'))),
                source_tables=tuple(str(value) for value in tables) if isinstance(tables, (list, tuple)) else ()))
    return tuple(result)


def _coverage(manifest: Mapping[str, Any]) -> str:
    value = manifest.get('coverage')
    return value if value in ('complete', 'gapped') else 'unknown'


def _summary_record(fields: tuple[bytes | None, ...], descriptor: Mapping[str, Any]) -> dict[str, Any]:
    return {column['name']: decode(fields[index], column['type'])
            for index, column in enumerate(descriptor['columns'])}


def _safe_child_member(prefix: str, path: str) -> str:
    if not _safe_member(path) or (prefix and not re.fullmatch(r'Q[1-4]/', prefix)):
        raise ArchiveError('archive_integrity_failed')
    return prefix + path


def _typed_member_rows(archive: zipfile.ZipFile, member_name: str, entry: Mapping[str, Any],
                       descriptor: Mapping[str, Any], dataset_id: UUID, revision: int,
                       control: Any):
    pa, pq = _arrow()
    role = entry['record_role']
    info = archive.getinfo(member_name)
    if info.file_size != int(entry['byte_size']) or info.compress_type != zipfile.ZIP_STORED:
        raise ArchiveError('archive_integrity_failed')
    digest = hashlib.sha256()
    with archive.open(member_name, 'r') as raw_member:
        while block := raw_member.read(1024 * 1024):
            control.checkpoint()
            digest.update(block)
    if digest.hexdigest() != entry['sha256']:
        raise ArchiveError('archive_integrity_failed')
    columns = _columns(descriptor, bool(entry.get('partition_field')))
    expected = arrow_schema(pa, columns, descriptor, dataset_id=dataset_id,
        revision=revision, source_table=str(entry['source_table']), record_role=role)
    canonical_digest = hashlib.sha256()
    decoded_bytes = row_count = 0
    # ZIP_STORED members support seek; no container extraction or bulk restore.
    with archive.open(member_name, 'r') as parquet_member:
        parquet = pq.ParquetFile(parquet_member)
        if (not parquet.schema_arrow.equals(expected, check_metadata=True)
                or hashlib.sha256(parquet.schema_arrow.serialize().to_pybytes()).hexdigest() != entry['schema_sha256']
                or parquet.metadata.num_rows != int(entry['row_count'])):
            raise ArchiveError('archive_integrity_failed')
        for row_group in range(parquet.num_row_groups):
            group_meta = parquet.metadata.row_group(row_group)
            if group_meta.num_rows > 65536 or group_meta.total_byte_size > 64 * 1024 * 1024:
                raise ArchiveError('archive_integrity_failed')
        for batch in parquet.iter_batches(batch_size=MAX_ROWS):
            control.checkpoint()
            for fields in binary_rows_from_batch(batch, columns):
                _validate_row(fields, columns, descriptor, dataset_id, revision,
                              role, entry.get('toronto_date'))
                encoded = canonical(fields)
                if len(encoded) > MAX_COPY_BYTES:
                    raise ArchiveError('archive_integrity_failed')
                canonical_digest.update(encoded)
                decoded_bytes += len(encoded)
                row_count += 1
                yield fields
    if (row_count != int(entry['row_count']) or decoded_bytes != int(entry['decoded_bytes'])
            or canonical_digest.hexdigest() != entry['canonical_sha256']):
        raise ArchiveError('archive_integrity_failed')


def _scan_zip_graph(path: Path, child: Mapping[str, Any], prefix: str,
                    dataset_id: UUID, revision: int,
                    full_spans: Sequence[tuple[int, datetime, datetime]],
                    edge_spans: Sequence[tuple[int, datetime, datetime]],
                    bounds: Sequence[tuple[datetime, datetime, bool]], base_width: int,
                    start: datetime, end: datetime, room: str, scratch: Path,
                    control: Any, page_start: int = 0) -> tuple[
                        dict[tuple[str, str, int], SummaryState], list[tuple[str, bool, Path]],
                        list[dict[str, Any]], list[dict[str, Any]]]:
    graph_schema = child.get('graph_schema', {})
    if not isinstance(graph_schema, Mapping):
        raise ArchiveError('archive_integrity_failed')
    summary_states: dict[tuple[str, str, int], SummaryState] = {}
    bound_starts = [bound[0] for bound in bounds]
    full_span_by_index = {index: (left, right) for index, left, right in full_spans}
    photoperiod: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    with zipfile.ZipFile(path, 'r') as archive:
        inventory = child.get('members')
        if not isinstance(inventory, (list, tuple)):
            raise ArchiveError('archive_integrity_failed')
        for entry in inventory:
            control.checkpoint()
            if not isinstance(entry, Mapping) or entry.get('record_role') != 'graph':
                continue
            relpath = entry.get('path')
            if not isinstance(relpath, str):
                raise ArchiveError('archive_integrity_failed')
            member_name = _safe_child_member(prefix, relpath)
            parts = relpath.split('/')
            if len(parts) != 3 or parts[0] != 'graph':
                raise ArchiveError('archive_integrity_failed')
            graph_kind = parts[1]
            table = entry.get('source_table')
            descriptor = graph_schema.get(table) if isinstance(table, str) else None
            if not isinstance(descriptor, Mapping):
                raise ArchiveError('archive_integrity_failed')
            if graph_kind in ('sensor', 'light') and table == 'graph_numeric':
                if base_width <= 0 or not full_spans:
                    continue
                for fields in _typed_member_rows(archive, member_name, entry, descriptor,
                                                 dataset_id, revision, control):
                    record = _summary_record(fields, descriptor)
                    if int(record['bucket_width_seconds']) != base_width:
                        continue
                    bucket_start = _instant(record['bucket_start'])
                    idx = _bucket_index(bucket_start, bounds, bound_starts)
                    full_span = full_span_by_index.get(idx) if idx is not None else None
                    if (full_span is None or bucket_start < full_span[0]
                            or bucket_start + timedelta(seconds=base_width) > full_span[1]):
                        continue
                    kind = str(record['kind'])
                    if kind != graph_kind:
                        raise ArchiveError('archive_integrity_failed')
                    _add_state(summary_states, kind, str(record['series_id']), idx, record)
            elif graph_kind in ('photoperiod', 'coverage'):
                expected_table = ('monitoring_room_photoperiod' if graph_kind == 'photoperiod'
                                  else 'monitoring_photoperiod_coverage')
                if table != expected_table:
                    raise ArchiveError('archive_integrity_failed')
                for fields in _typed_member_rows(archive, member_name, entry, descriptor,
                                                 dataset_id, revision, control):
                    record = _summary_record(fields, descriptor)
                    instant = _instant(record['observed_at'])
                    if not start <= instant < end or record.get('location') != room or record.get('cluster') != 'main':
                        continue
                    if graph_kind == 'photoperiod':
                        photoperiod.append(record)
                    else:
                        coverage.append(record)
            else:
                raise ArchiveError('archive_integrity_failed')
        raw_pages = _scan_zip_raw(archive, child, prefix, dataset_id, revision,
                                  edge_spans, scratch, page_start, control)
    return summary_states, raw_pages, photoperiod, coverage


def _day_bounds(day: str) -> tuple[datetime, datetime]:
    current = date.fromisoformat(day)
    left = datetime.combine(current, time.min, TORONTO).astimezone(UTC)
    right = datetime.combine(current + timedelta(days=1), time.min, TORONTO).astimezone(UTC)
    return left, right


def _raw_part_selected(day: Any, spans: Sequence[tuple[int, datetime, datetime]]) -> bool:
    if not spans:
        return False
    if day is None:
        return True
    left, right = _day_bounds(str(day))
    return any(left < hi and right > lo for _, lo, hi in spans)


def _write_filtered_rows(path: Path, buffered: Sequence[tuple[bytes | None, ...]], control: Any) -> None:
    with path.open('wb') as stream:
        write_header(stream)
        for fields in buffered:
            control.checkpoint()
            write_row(stream, fields)
        write_trailer(stream)


def _scan_zip_raw(archive: zipfile.ZipFile, child: Mapping[str, Any], prefix: str,
                  dataset_id: UUID, revision: int, spans: Sequence[tuple[int, datetime, datetime]],
                  scratch: Path, page_start: int, control: Any) -> list[tuple[str, bool, Path]]:
    if not spans:
        return []
    schema = child.get('source_schema', {})
    if not isinstance(schema, Mapping):
        raise ArchiveError('archive_integrity_failed')
    files: list[tuple[str, bool, Path]] = []
    page_number = page_start
    for entry in child.get('members', ()):
        control.checkpoint()
        if not isinstance(entry, Mapping) or entry.get('record_role') != 'observation':
            continue
        table = entry.get('source_table')
        if table not in ('measurement', 'effective_setpoints'):
            continue
        descriptor = schema.get(table)
        if not isinstance(descriptor, Mapping):
            raise ArchiveError('archive_integrity_failed')
        if not _raw_part_selected(entry.get('toronto_date'), spans):
            continue
        relpath = entry.get('path')
        if not isinstance(relpath, str) or not relpath.startswith(f'raw/{table}/'):
            raise ArchiveError('archive_integrity_failed')
        member_name = _safe_child_member(prefix, relpath)
        partition = bool(entry.get('partition_field'))
        time_index = next((i for i, column in enumerate(descriptor['columns'])
                           if column['name'] == descriptor.get('time_column')), None)
        if time_index is None:
            raise ArchiveError('archive_integrity_failed')
        buffered: list[tuple[bytes | None, ...]] = []
        buffered_size = 0
        for fields in _typed_member_rows(archive, member_name, entry, descriptor,
                                         dataset_id, revision, control):
            observed = decode(fields[time_index], descriptor['columns'][time_index]['type'])
            if not any(lo <= observed < hi for _, lo, hi in spans):
                continue
            encoded_size = len(canonical(fields))
            if buffered and (len(buffered) >= MAX_ROWS or buffered_size + encoded_size > MAX_COPY_BYTES):
                destination = scratch / f'raw_page_{page_number:08d}.copy'
                _write_filtered_rows(destination, buffered, control)
                files.append((table, partition, destination))
                page_number += 1
                buffered = []
                buffered_size = 0
            buffered.append(fields)
            buffered_size += encoded_size
        if buffered:
            destination = scratch / f'raw_page_{page_number:08d}.copy'
            _write_filtered_rows(destination, buffered, control)
            files.append((table, partition, destination))
            page_number += 1
    return files


class ArchiveGraphReader:
    def __init__(self, pool: asyncpg.Pool, heavy_work: Callable, staging_root: Path) -> None:
        self.pool = pool
        self.heavy_work = heavy_work
        self.staging_root = Path(staging_root)

    async def _heavy(self, function: Callable[[Any], Any]) -> Any:
        result = self.heavy_work(function)
        return await result if hasattr(result, '__await__') else result

    async def read(self, pin: Any, *, start: datetime, end: datetime, max_points: int) -> dict[str, Any]:
        start, end = _instant(start), _instant(end)
        if end <= start:
            raise ArchiveError('archive_graph_range_invalid', status_code=400)
        if type(max_points) is not int or not 10 <= max_points <= 1000:
            raise ArchiveError('archive_graph_budget_invalid', status_code=400)
        context = await self.pool.fetchrow(
            'SELECT d.room,d.available_from,d.available_to '
            'FROM public.archive_dataset d WHERE d.dataset_id=$1', pin.dataset_id)
        if context is None:
            raise ArchiveError('archive_not_found', status_code=404)
        room = str(context['room'])
        if room not in ('Flower Room', 'Veg Room'):
            raise ArchiveError('archive_scope_unresolved')
        zip_components: tuple[tuple[dict[str, Any], str, UUID, int], ...] = ()
        if pin.source_kind != 'zip':
            raise ArchiveError('archive_load_required')
        if pin.local_path is None:
            raise ArchiveError('archive_load_required')
        zip_path = Path(pin.local_path)
        if zip_path.is_symlink() or not zip_path.is_file():
            raise ArchiveError('archive_integrity_failed')
        manifest, zip_components, verified = _zip_component_manifests(pin)
        if verified.byte_size <= 0 or zip_path.stat().st_size != verified.byte_size:
            raise ArchiveError('archive_integrity_failed')
        lower = manifest.get('available_from') or context['available_from']
        upper = manifest.get('available_to') or context['available_to']
        if ((lower is not None and start < _instant(lower))
                or (upper is not None and end > _instant(upper))):
            raise ArchiveError('archive_graph_range_invalid', status_code=400)
        overview = end - start > timedelta(days=7)
        try:
            bounds, base_width, interval, range_model, selection = _targets(
                start, end, overview=overview, max_points=max_points)
        except (ValueError, TypeError, StopIteration, MonitoringValidationError) as error:
            raise ArchiveError('archive_graph_range_invalid', status_code=400) from error
        full_spans = _full_spans(bounds, base_width)
        edge_spans = _edge_spans(bounds, base_width)
        # Preserve each physical child's identity and interval independently.
        # Adjacent quarters may legitimately freeze different names for one ID.
        identity_sources = zip_components
        identities = [(dataset_id, revision, *row)
                      for child, _, dataset_id, revision in identity_sources
                      for row in sensor_identity_rows(child)]
        scratch = self.staging_root / 'staging' / 'graph' / str(pin.load_id) / uuid4().hex
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw_pages: list[tuple[str, bool, Path]] = []
        try:
            summary_states: dict[tuple[str, str, int], SummaryState] = {}
            photoperiod: list[dict[str, Any]] = []
            photo_coverage: list[dict[str, Any]] = []
            for child, prefix, child_id, child_revision in zip_components:
                page_start = len(raw_pages)
                child_states, child_pages, child_photo, child_coverage = await self._heavy(
                    lambda control, c=child, p=prefix, cid=child_id, rev=child_revision, page=page_start: _scan_zip_graph(
                        Path(pin.local_path), c, p, cid, rev, full_spans, edge_spans,
                        bounds, base_width, start, end, room, scratch, control, page))
                for key, state in child_states.items():
                    summary_states.setdefault(key, SummaryState()).absorb(state)
                raw_pages.extend(child_pages)
                photoperiod.extend(child_photo)
                photo_coverage.extend(child_coverage)
            async with self.pool.acquire() as conn, conn.transaction(isolation='repeatable_read'):
                await conn.execute(
                    'CREATE TEMP TABLE archive_graph_sensor_identity(dataset_id UUID NOT NULL,'
                    'storage_revision BIGINT NOT NULL,sensor_id INTEGER NOT NULL,start_at TIMESTAMPTZ NOT NULL,'
                    'end_at TIMESTAMPTZ NOT NULL,series_id TEXT) ON COMMIT DROP')
                if identities:
                    await conn.executemany('INSERT INTO archive_graph_sensor_identity VALUES($1,$2,$3,$4,$5,$6)', identities)
                await self._stage_zip_pages(conn, raw_pages, manifest)
                if edge_spans:
                    await self._raw_edges(conn, edge_spans, summary_states)
            return self._response(pin, manifest, room, max_points, bounds, interval,
                                  range_model, selection, overview, summary_states, photoperiod, photo_coverage)
        except ArchiveError:
            raise
        except asyncpg.PostgresError as error:
            raise ArchiveError('archive_load_unavailable', status_code=503) from error
        except (OSError, ValueError, zipfile.BadZipFile, KeyError, TypeError, OverflowError) as error:
            raise ArchiveError('archive_integrity_failed') from error
        except Exception as error:
            if type(error).__module__.startswith('pyarrow'):
                raise ArchiveError('archive_integrity_failed') from error
            raise
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    async def _stage_zip_pages(self, conn: asyncpg.Connection, pages: Sequence[tuple[str, bool, Path]],
                               manifest: Mapping[str, Any]) -> None:
        schema = manifest.get('source_schema', {})
        if not isinstance(schema, Mapping):
            raise ArchiveError('archive_integrity_failed')
        partitioned: dict[str, bool] = {}
        for table in ('measurement', 'effective_setpoints'):
            descriptor = schema.get(table)
            if not isinstance(descriptor, Mapping):
                raise ArchiveError('archive_integrity_failed')
            partition = bool(descriptor.get('source_time_nullable'))
            await _create_source_page(conn, table, descriptor, partition)
            partitioned[table] = partition
        for table, partition, path in pages:
            if table not in partitioned or partitioned[table] != partition:
                raise ArchiveError('archive_integrity_failed')
            columns = [column['name'] for column in _columns(schema[table], partition)]
            await conn.copy_to_table(f'archive_graph_{table}', source=path, columns=columns, format='binary')


    async def _raw_edges(self, conn: asyncpg.Connection,
                         spans: Sequence[tuple[int, datetime, datetime]],
                         states: dict[tuple[str, str, int], SummaryState]) -> None:
        if not spans:
            return
        indexes, starts, ends = zip(*spans)
        args: tuple[Any, ...] = (list(indexes), list(starts), list(ends))
        measurement_from = 'archive_graph_measurement'
        effective_from = 'archive_graph_effective_setpoints'
        sample_filter = "m.record_role='observation'"
        edges = ("WITH edges AS (SELECT * FROM unnest($1::integer[],$2::timestamptz[],$3::timestamptz[]) "
                 "AS e(idx,start_at,end_at)) ")
        identity_match = ("i.dataset_id=m.dataset_id AND i.storage_revision=m.storage_revision "
                          "AND i.sensor_id=m.sensor_id AND m.time>=i.start_at AND m.time<i.end_at")
        # A known non-native sensor has an authoritative row with NULL series_id.
        # It is excluded from graphs; a genuinely absent identity is an error.
        unmatched = await conn.fetchval(
            edges + "SELECT EXISTS(SELECT 1 FROM " + measurement_from + " m "
            "JOIN edges e ON m.time>=e.start_at AND m.time<e.end_at WHERE " + sample_filter +
            " AND NOT EXISTS(SELECT 1 FROM archive_graph_sensor_identity i WHERE " + identity_match + "))",
            *args)
        if unmatched:
            raise ArchiveError('grow_scope_unresolved')
        sensor_rows = await conn.fetch(
            edges + "SELECT 'sensor'::text AS kind,i.series_id,e.idx,count(m.value)::bigint AS sample_count,"
            "sum(m.value::numeric) AS value_sum,sum(m.value::numeric*m.value::numeric) AS value_sum_squares,"
            "min(m.value::numeric) AS minimum,max(m.value::numeric) AS maximum FROM " + measurement_from + " m "
            "JOIN edges e ON m.time>=e.start_at AND m.time<e.end_at "
            "JOIN archive_graph_sensor_identity i ON " + identity_match +
            " WHERE " + sample_filter + " AND i.series_id IS NOT NULL GROUP BY i.series_id,e.idx", *args)
        light_rows = await conn.fetch(
            edges + "SELECT 'light'::text AS kind,jsonb_build_array(m.location,m.cluster,m.device_name)::text AS series_id,"
            "e.idx,count(m.effective_light_intensity)::bigint AS sample_count,"
            "sum(m.effective_light_intensity::numeric) AS value_sum,"
            "sum(m.effective_light_intensity::numeric*m.effective_light_intensity::numeric) AS value_sum_squares,"
            "min(m.effective_light_intensity::numeric) AS minimum,max(m.effective_light_intensity::numeric) AS maximum "
            "FROM " + effective_from + " m JOIN edges e ON m.timestamp>=e.start_at AND m.timestamp<e.end_at "
            "WHERE " + sample_filter + " AND m.effective_light_intensity IS NOT NULL AND m.device_name IS NOT NULL "
            "GROUP BY m.location,m.cluster,m.device_name,e.idx", *args)
        for row in (*sensor_rows, *light_rows):
            _add_state(states, row['kind'], row['series_id'], int(row['idx']), row)


    def _response(self, pin: Any, manifest: Mapping[str, Any], room: str, max_points: int,
                  bounds: Sequence[tuple[datetime, datetime, bool]], interval: int,
                  range_model: Any, selection: Any, overview: bool,
                  states: Mapping[tuple[str, str, int], SummaryState],
                  photo_records: Sequence[Mapping[str, Any]],
                  coverage_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        metadata_room = resolve_room_metadata(room)
        families = unit_families()
        sensor_ids: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
        light_ids: dict[tuple[str, str, str], str] = {}
        for kind, series_id, _ in states:
            if kind == 'sensor':
                try:
                    item = json.loads(series_id)
                except (ValueError, TypeError) as error:
                    raise ArchiveError('archive_integrity_failed') from error
                if not isinstance(item, Mapping):
                    raise ArchiveError('archive_integrity_failed')
                sensor_ids[(str(item.get('node')), str(item.get('sensor')), str(item.get('unit')),
                            str(item.get('data_type')))].add(series_id)
            elif kind == 'light':
                value = _json_series(series_id)
                if len(value) != 3:
                    raise ArchiveError('archive_integrity_failed')
                light_ids[(str(value[0]), str(value[1]), str(value[2]))] = series_id
            else:
                raise ArchiveError('archive_integrity_failed')
        ordered_sensors = sorted(sensor_ids.items(),
            key=lambda item: (metadata_room.nodes.index(Node(item[0][0])), item[0][1:]))
        if overview:
            sensor_series = []
            sensor_statistics = []
            for (node_text, sensor, unit, data_type), series_group in ordered_sensors:
                if data_type not in families:
                    continue
                node = Node(node_text)
                points = []
                whole = SummaryState()
                for index, bound in enumerate(bounds):
                    state = SummaryState()
                    for series_id in series_group:
                        contribution = states.get(('sensor', series_id, index))
                        if contribution is not None:
                            state.absorb(contribution)
                    if state.count:
                        points.append(HistoryOverviewPoint(start=bound[0], end=bound[1],
                            average=float_or_none(state.average_decimal),
                            minimum=float_or_none(state.minimum), maximum=float_or_none(state.maximum),
                            sample_count=state.count, partial=bound[2]))
                    whole.absorb(state)
                sensor_series.append(HistoryOverviewSensorSeries(sensor=sensor, node=node, unit=unit,
                    unit_family=families[data_type], points=tuple(points)))
                sensor_statistics.append(self._overview_sensor_stats(sensor, node, whole))
            sensors = HistoryOverviewSensorResponse(
                metadata=HistoryOverviewMetadata(generated_at=datetime.now(UTC), range=range_model,
                    room=metadata_room, requested_max_points=max_points, bucket=selection,
                    source_coverage='ready'), series=tuple(sensor_series), statistics=tuple(sensor_statistics))
        else:
            sensor_series = []
            statistics = []
            for (node_text, sensor, unit, data_type), series_group in ordered_sensors:
                if data_type not in families:
                    continue
                node = Node(node_text)
                points = []
                whole = SummaryState()
                for index, (left, right, partial) in enumerate(bounds):
                    state = SummaryState()
                    for series_id in series_group:
                        contribution = states.get(('sensor', series_id, index))
                        if contribution is not None:
                            state.absorb(contribution)
                    if state.count:
                        points.append(SeriesPoint(timestamp=left,
                            average=float(state.average_decimal), minimum=float(state.minimum),
                            maximum=float(state.maximum), sample_count=state.count))
                    whole.absorb(state)
                if whole.count:
                    deviation = whole.sample_stddev_decimal()
                    statistics.append(SensorStatistics(sensor=sensor, node=node,
                        minimum=float(whole.minimum), maximum=float(whole.maximum),
                        average=float(whole.average_decimal), stddev_samp=float(deviation or Decimal(0)),
                        sample_count=whole.count, stddev_quality=StddevQuality.EXACT))
                sensor_series.append(SensorSeries(sensor=sensor, node=node,
                    unit_family=families[data_type], unit=unit, points=tuple(points)))
            sensors = MonitoringResponse(metadata=MonitoringMetadata(generated_at=datetime.now(UTC),
                tier=selection, range=range_model, room=metadata_room, requested_max_points=max_points,
                interval_seconds=interval), series=tuple(sensor_series), statistics=tuple(statistics))
        # Only gaps recorded in the verified ZIP manifest are reported.
        gaps = _gap_values(manifest)
        coverage = 'gapped' if gaps else _coverage(manifest)
        photo_models = tuple(PhotoperiodFact.model_validate(
            {key: item[key] for key in PhotoperiodFact.model_fields if key in item})
            for item in sorted(photo_records, key=lambda row: (row['observed_at'], row['id'])))
        coverage_models = tuple(PhotoperiodCoverageFact.model_validate(
            {key: item[key] for key in PhotoperiodCoverageFact.model_fields if key in item})
            for item in sorted(coverage_records, key=lambda row: (row['observed_at'], row['id'])))
        return {
            'archive': ArchiveSelectionMeta(dataset_id=pin.dataset_id, revision=pin.revision,
                                            coverage=coverage, gaps=gaps),
            'tier': 'overview' if overview else 'detailed', 'sensors': sensors,
            'lights': self._light_series(light_ids, states, bounds),
            'photoperiod': photo_models, 'photoperiod_coverage': coverage_models,
        }

    @staticmethod
    def _overview_sensor_stats(sensor: str, node: Node, state: SummaryState) -> HistoryOverviewSensorStatistics:
        if state.count == 0:
            return HistoryOverviewSensorStatistics(sensor=sensor, node=node, sample_count=0,
                                                   stddev_quality=StddevQuality.EXACT)
        return HistoryOverviewSensorStatistics(sensor=sensor, node=node, sample_count=state.count,
            minimum=float_or_none(state.minimum), maximum=float_or_none(state.maximum),
            average=float_or_none(state.average_decimal),
            stddev_samp=float_or_none(state.sample_stddev_decimal()), stddev_quality=StddevQuality.EXACT)

    @staticmethod
    def _light_series(identities: Mapping[tuple[str, str, str], str],
                      states: Mapping[tuple[str, str, int], SummaryState],
                      bounds: Sequence[tuple[datetime, datetime, bool]]
                      ) -> tuple[HistoryOverviewControlSeries, ...]:
        result = []
        for (location, cluster, device_name), series_id in sorted(identities.items()):
            points = []
            for index, (left, right, partial) in enumerate(bounds):
                state = states.get(('light', series_id, index))
                points.append(HistoryOverviewPoint(start=left, end=right,
                    average=None if state is None else float_or_none(state.average_decimal),
                    minimum=None if state is None else float_or_none(state.minimum),
                    maximum=None if state is None else float_or_none(state.maximum),
                    sample_count=0 if state is None else state.count, partial=partial))
            result.append(HistoryOverviewControlSeries(location=location, cluster=cluster,
                device_name=device_name, points=tuple(points)))
        return tuple(result)
