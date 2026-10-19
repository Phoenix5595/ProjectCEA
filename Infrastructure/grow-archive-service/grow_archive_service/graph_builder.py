"""Build exact sensor/light rollups and typed phase graph facts from bounded COPY pages."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID

import asyncpg

from grow_archive_service.binary_codec import canonical, rows, write_header, write_row, write_trailer
from grow_archive_service.models import ArchiveError
from monitoring_service.sensor_models import resolve_room_metadata
from shared.cluster_topology import sensor_name_like_pattern

MAX_ROWS = 4096
MAX_COPY_BYTES = 8 * 1024 * 1024
_GRAPH_NUMERIC = {
    'columns': [
        {'name': 'bucket_start', 'type': 'timestamp with time zone', 'not_null': True},
        {'name': 'kind', 'type': 'text', 'not_null': True},
        {'name': 'series_id', 'type': 'text', 'not_null': True},
        {'name': 'bucket_width_seconds', 'type': 'integer', 'not_null': True},
        {'name': 'sample_count', 'type': 'bigint', 'not_null': True},
        {'name': 'value_sum', 'type': 'numeric', 'not_null': True},
        {'name': 'value_sum_squares', 'type': 'numeric', 'not_null': True},
        {'name': 'minimum', 'type': 'numeric', 'not_null': True},
        {'name': 'maximum', 'type': 'numeric', 'not_null': True},
        {'name': 'last_time', 'type': 'timestamp with time zone', 'not_null': True},
        {'name': 'last_value', 'type': 'double precision', 'not_null': True},
    ],
    'time_column': 'bucket_start',
    'stable_ordering': ['kind', 'series_id', 'bucket_width_seconds'],
    'default_record_role': 'graph',
}
_SOURCE_SQL_TYPES = {
    'smallint': 'SMALLINT', 'integer': 'INTEGER', 'bigint': 'BIGINT',
    'real': 'REAL', 'double precision': 'DOUBLE PRECISION', 'numeric': 'NUMERIC',
    'boolean': 'BOOLEAN', 'date': 'DATE', 'timestamp with time zone': 'TIMESTAMPTZ',
    'timestamp without time zone': 'TIMESTAMP WITHOUT TIME ZONE', 'uuid': 'UUID',
    'bytea': 'BYTEA', 'text': 'TEXT', 'json': 'JSON', 'jsonb': 'JSONB',
}


def _identifier(value: str) -> str:
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', value):
        raise ArchiveError('schema_descriptor_unavailable')
    return '"' + value + '"'


def _sql_type(value: str) -> str:
    if value.endswith('[]'):
        return _sql_type(value[:-2]) + '[]'
    if value in _SOURCE_SQL_TYPES:
        return _SOURCE_SQL_TYPES[value]
    if re.fullmatch(r'numeric\([1-9][0-9]{0,2},[0-9]{1,3}\)', value):
        return value.upper()
    raise ArchiveError('schema_descriptor_unavailable')


def _parse_instant(value: Any) -> datetime:
    if isinstance(value, datetime):
        instant = value
    elif isinstance(value, str):
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        raise ArchiveError('grow_scope_unresolved')
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ArchiveError('grow_scope_unresolved')
    return instant


def _like_match(name: str, pattern: str | None) -> bool:
    if pattern is None:
        return True
    expression = ''.join('.*' if char == '%' else '.' if char == '_' else re.escape(char)
                         for char in pattern)
    return re.fullmatch(expression, name, flags=re.IGNORECASE) is not None


def sensor_identity_rows(manifest: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    rows_out: list[tuple[Any, ...]] = []
    for snapshot in manifest.get('identity_snapshots', ()):
        if not isinstance(snapshot, Mapping) or snapshot.get('source_table') != 'measurement':
            continue
        source_identity = snapshot.get('source_identity')
        if not isinstance(source_identity, Mapping) or type(source_identity.get('sensor_id')) is not int:
            raise ArchiveError('grow_scope_unresolved')
        name, unit, data_type, room = (snapshot.get('name'), snapshot.get('unit'),
                                       snapshot.get('data_type'), snapshot.get('room'))
        if (not isinstance(name, str) or not name or not isinstance(unit, str) or not unit
                or not isinstance(data_type, str) or not data_type or not isinstance(room, str) or not room):
            raise ArchiveError('grow_scope_unresolved')
        metadata = resolve_room_metadata(room)
        matching = [node.value for node in metadata.nodes
                    if _like_match(name, sensor_name_like_pattern(room, node.value))]
        if len(matching) > 1:
            raise ArchiveError('grow_scope_unresolved')
        # Match the native graph's fixed node-name filter. An authoritative
        # weather/other sensor remains in raw ML rows, not mislabeled front/back.
        series_id = None
        if matching:
            series_id = json.dumps({'data_type': data_type, 'node': matching[0], 'sensor': name,
                                    'sensor_id': source_identity['sensor_id'], 'unit': unit},
                                   ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        rows_out.append((source_identity['sensor_id'], _parse_instant(snapshot['start']),
                         _parse_instant(snapshot['end']), series_id))
    ordered = sorted(set(rows_out), key=lambda item: (item[0], item[1], item[2], item[3] or ''))
    merged: list[tuple[Any, ...]] = []
    for row in ordered:
        if merged and merged[-1][0] == row[0] and row[1] <= merged[-1][2]:
            previous = merged[-1]
            if previous[3] != row[3]:
                raise ArchiveError('grow_scope_unresolved')
            merged[-1] = (previous[0], previous[1], max(previous[2], row[2]), previous[3])
        else:
            merged.append(row)
    return merged


def _descriptor_columns(descriptor: Mapping[str, Any], partition_field: bool) -> list[dict[str, Any]]:
    originals = descriptor.get('columns')
    if not isinstance(originals, list) or any(not isinstance(column, Mapping) for column in originals):
        raise ArchiveError('schema_descriptor_unavailable')
    names = [column.get('name') for column in originals]
    if len(names) != len(set(names)):
        raise ArchiveError('schema_descriptor_unavailable')
    columns = list(originals) + [
        {'name': 'dataset_id', 'type': 'uuid', 'not_null': True},
        {'name': 'storage_revision', 'type': 'bigint', 'not_null': True},
        {'name': 'source_occurrence', 'type': 'integer', 'not_null': True},
        {'name': 'record_role', 'type': 'text', 'not_null': True},
    ]
    if partition_field:
        columns.append({'name': '_archive_partition_time', 'type': 'timestamp with time zone', 'not_null': True})
    return columns


def _copy_metrics(path: Path, control: Any, *, enforce: bool = True) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    count = byte_count = 0
    with path.open('rb') as stream:
        for fields in rows(stream):
            control.checkpoint()
            encoded = canonical(fields)
            count += 1
            byte_count += len(encoded)
            digest.update(encoded)
            if enforce and (count > MAX_ROWS or byte_count > MAX_COPY_BYTES):
                raise ArchiveError('archive_capture_page_invalid')
    if count == 0:
        raise ArchiveError('archive_capture_page_invalid')
    if enforce and path.stat().st_size > MAX_COPY_BYTES:
        raise ArchiveError('archive_capture_page_invalid')
    return count, byte_count, digest.hexdigest()


def _copy_facts(source: Path, destination: Path, original_columns: int, control: Any) -> dict[str, Any] | None:
    count = byte_count = 0
    digest = hashlib.sha256()
    with source.open('rb') as input_stream, destination.open('wb') as output_stream:
        write_header(output_stream)
        for fields in rows(input_stream):
            control.checkpoint()
            if len(fields) < original_columns + 4:
                raise ArchiveError('archive_integrity_failed')
            if fields[original_columns + 3] != b'observation':
                continue
            output = (*fields[:original_columns + 3], b'graph', *fields[original_columns + 4:])
            encoded = canonical(output)
            count += 1
            byte_count += len(encoded)
            if count > MAX_ROWS or byte_count > MAX_COPY_BYTES:
                raise ArchiveError('archive_capture_page_invalid')
            digest.update(encoded)
            write_row(output_stream, output)
        write_trailer(output_stream)
    if count == 0:
        destination.unlink(missing_ok=True)
        return None
    if destination.stat().st_size > MAX_COPY_BYTES:
        destination.unlink(missing_ok=True)
        raise ArchiveError('archive_capture_page_invalid')
    return {'row_count': count, 'canonical_sha256': digest.hexdigest()}


def _summary_sql(stream: str) -> str:
    if stream == 'measurement':
        selected = """SELECT m.time AS observed_at, i.series_id, m.value AS value,
                          0::numeric AS order_primary, m.source_occurrence AS order_secondary
                   FROM archive_graph_measurement m
                   JOIN archive_graph_sensor_identity i ON i.sensor_id=m.sensor_id
                    AND m.time>=i.start_at AND m.time<i.end_at
                   WHERE m.record_role='observation' AND m.dataset_id=$2::uuid
                     AND m.storage_revision=$3::bigint AND i.series_id IS NOT NULL"""
        graph_kind = 'sensor'
    elif stream == 'effective_setpoints':
        selected = """SELECT m.timestamp AS observed_at,
                          jsonb_build_array(m.location,m.cluster,m.device_name)::text AS series_id,
                          m.effective_light_intensity AS value,
                          COALESCE(m.monitoring_ingest_id,0)::numeric AS order_primary,
                          m.source_occurrence AS order_secondary
                   FROM archive_graph_effective_setpoints m
                   WHERE m.record_role='observation' AND m.dataset_id=$2::uuid
                     AND m.storage_revision=$3::bigint
                     AND m.effective_light_intensity IS NOT NULL AND m.device_name IS NOT NULL"""
        graph_kind = 'light'
    else:
        raise ArchiveError('schema_descriptor_unavailable')
    return f"""
WITH selected AS ({selected}),
aggregates AS (
    SELECT time_bucket(make_interval(secs => $1::double precision),observed_at) AS bucket_start,
           series_id,count(value)::bigint AS sample_count,
           sum(value::numeric) AS value_sum,
           sum(value::numeric*value::numeric) AS value_sum_squares,
           min(value::numeric) AS minimum,max(value::numeric) AS maximum
    FROM selected GROUP BY bucket_start,series_id
), last_rows AS (
    SELECT DISTINCT ON (time_bucket(make_interval(secs => $1::double precision),observed_at),series_id)
           time_bucket(make_interval(secs => $1::double precision),observed_at) AS bucket_start,
           series_id,observed_at AS last_time,value::double precision AS last_value,
           order_primary,order_secondary
    FROM selected WHERE value IS NOT NULL
    ORDER BY bucket_start,series_id,observed_at DESC,order_primary DESC,order_secondary DESC
)
INSERT INTO archive_graph_partial(kind,series_id,bucket_width_seconds,bucket_start,
    sample_count,value_sum,value_sum_squares,minimum,maximum,last_time,last_value,
    last_order_primary,last_order_secondary)
SELECT '{graph_kind}',a.series_id,$1,a.bucket_start,a.sample_count,a.value_sum,a.value_sum_squares,
       a.minimum,a.maximum,l.last_time,l.last_value,l.order_primary,l.order_secondary
FROM aggregates a JOIN last_rows l USING(bucket_start,series_id)
ON CONFLICT(kind,series_id,bucket_width_seconds,bucket_start) DO UPDATE SET
    sample_count=archive_graph_partial.sample_count+EXCLUDED.sample_count,
    value_sum=archive_graph_partial.value_sum+EXCLUDED.value_sum,
    value_sum_squares=archive_graph_partial.value_sum_squares+EXCLUDED.value_sum_squares,
    minimum=LEAST(archive_graph_partial.minimum,EXCLUDED.minimum),
    maximum=GREATEST(archive_graph_partial.maximum,EXCLUDED.maximum),
    last_time=CASE WHEN ROW(EXCLUDED.last_time,EXCLUDED.last_order_primary,EXCLUDED.last_order_secondary)>
                               ROW(archive_graph_partial.last_time,archive_graph_partial.last_order_primary,archive_graph_partial.last_order_secondary)
                   THEN EXCLUDED.last_time ELSE archive_graph_partial.last_time END,
    last_value=CASE WHEN ROW(EXCLUDED.last_time,EXCLUDED.last_order_primary,EXCLUDED.last_order_secondary)>
                                 ROW(archive_graph_partial.last_time,archive_graph_partial.last_order_primary,archive_graph_partial.last_order_secondary)
                     THEN EXCLUDED.last_value ELSE archive_graph_partial.last_value END,
    last_order_primary=CASE WHEN ROW(EXCLUDED.last_time,EXCLUDED.last_order_primary,EXCLUDED.last_order_secondary)>
                                      ROW(archive_graph_partial.last_time,archive_graph_partial.last_order_primary,archive_graph_partial.last_order_secondary)
                            THEN EXCLUDED.last_order_primary ELSE archive_graph_partial.last_order_primary END,
    last_order_secondary=CASE WHEN ROW(EXCLUDED.last_time,EXCLUDED.last_order_primary,EXCLUDED.last_order_secondary)>
                                        ROW(archive_graph_partial.last_time,archive_graph_partial.last_order_primary,archive_graph_partial.last_order_secondary)
                              THEN EXCLUDED.last_order_secondary ELSE archive_graph_partial.last_order_secondary END
"""


async def _create_source_page(conn: asyncpg.Connection, table: str,
                              descriptor: Mapping[str, Any], partition_field: bool) -> None:
    columns = _descriptor_columns(descriptor, partition_field)
    declaration = ','.join(f"{_identifier(column['name'])} {_sql_type(column['type'])}"
                           + (' NOT NULL' if column.get('not_null') else '') for column in columns)
    await conn.execute(f'CREATE TEMP TABLE archive_graph_{table} ({declaration}) ON COMMIT DROP')


async def _stage_sensor_identities(conn: asyncpg.Connection, rows_in: list[tuple[Any, ...]]) -> None:
    await conn.execute('CREATE TEMP TABLE archive_graph_sensor_identity(sensor_id INTEGER NOT NULL,start_at TIMESTAMPTZ NOT NULL,end_at TIMESTAMPTZ NOT NULL,series_id TEXT) ON COMMIT DROP')
    if rows_in:
        await conn.executemany('INSERT INTO archive_graph_sensor_identity VALUES($1,$2,$3,$4)', rows_in)


async def _write_summary_parts(conn: asyncpg.Connection, capture_dir: Path, dataset_id: UUID,
                               revision: int, heavy_work: Callable) -> list[dict[str, Any]]:
    parts_out: list[dict[str, Any]] = []
    totals = {family: int(await conn.fetchval('SELECT count(*) FROM archive_graph_partial WHERE kind=$1', family))
              for family in ('sensor', 'light')}
    index = 0
    for family in ('sensor', 'light'):
        offset, limit = 0, MAX_ROWS
        while offset < totals[family]:
            while True:
                path = capture_dir / f'graph_numeric_{index:07d}.copy'
                query = """
WITH numbered AS (
 SELECT bucket_start,kind,series_id,bucket_width_seconds,sample_count,value_sum,value_sum_squares,
        minimum,maximum,last_time,last_value,
        (row_number() OVER (PARTITION BY bucket_start ORDER BY kind,series_id,bucket_width_seconds)-1)::integer AS source_occurrence
 FROM archive_graph_partial
)
SELECT bucket_start,kind,series_id,bucket_width_seconds,sample_count,value_sum,value_sum_squares,
       minimum,maximum,last_time,last_value,$4::uuid,$5::bigint,source_occurrence,'graph'::text
FROM numbered WHERE kind=$1 ORDER BY bucket_start,series_id,bucket_width_seconds LIMIT $2 OFFSET $3
"""
                await conn.copy_from_query(query, family, limit, offset, dataset_id, revision,
                                           output=str(path), format='binary')
                count, size, digest = await heavy_work(lambda worker: _copy_metrics(path, worker, enforce=False))
                if count > MAX_ROWS or size > MAX_COPY_BYTES or path.stat().st_size > MAX_COPY_BYTES:
                    path.unlink(missing_ok=True)
                    if limit == 1:
                        raise ArchiveError('archive_capture_page_invalid')
                    limit = max(1, limit // 2)
                    continue
                break
            parts_out.append({'path': path.name, 'source_table': 'graph_numeric',
                              'record_role': 'graph', 'graph_kind': family,
                              'row_count': count, 'canonical_sha256': digest,
                              'partition_field': False})
            offset += count
            index += 1
            limit = min(MAX_ROWS, limit * 2)
    return parts_out


async def build_graph_parts(pool: asyncpg.Pool, heavy_work: Callable,
                            capture_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Emit graph-role binary COPY pages; raw ML capture parts stay untouched."""
    capture_dir = Path(capture_dir).resolve(strict=True)
    dataset_id = UUID(str(manifest['dataset_id']))
    revision = int(manifest['revision'])
    if revision < 1:
        raise ArchiveError('archive_revision_conflict')
    descriptors = manifest.get('source_schema')
    source_parts = manifest.get('parts')
    if not isinstance(descriptors, Mapping) or not isinstance(source_parts, list):
        raise ArchiveError('schema_descriptor_unavailable')
    summary_tables = ('measurement', 'effective_setpoints')
    graph_schema: dict[str, Any] = {'graph_numeric': dict(_GRAPH_NUMERIC)}
    for table in ('monitoring_room_photoperiod', 'monitoring_photoperiod_coverage'):
        if table in descriptors:
            graph_schema[table] = dict(descriptors[table])
    identities = sensor_identity_rows(manifest)
    graph_parts: list[dict[str, Any]] = []
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("CREATE TEMP TABLE archive_graph_partial(kind TEXT NOT NULL,series_id TEXT NOT NULL,bucket_width_seconds INTEGER NOT NULL,bucket_start TIMESTAMPTZ NOT NULL,sample_count BIGINT NOT NULL,value_sum NUMERIC NOT NULL,value_sum_squares NUMERIC NOT NULL,minimum NUMERIC NOT NULL,maximum NUMERIC NOT NULL,last_time TIMESTAMPTZ NOT NULL,last_value DOUBLE PRECISION NOT NULL,last_order_primary NUMERIC NOT NULL,last_order_secondary INTEGER NOT NULL,PRIMARY KEY(kind,series_id,bucket_width_seconds,bucket_start)) ON COMMIT DROP")
        await _stage_sensor_identities(conn, identities)
        created: set[str] = set()
        partition_by_table: dict[str, bool] = {}
        for part in source_parts:
            table, role = part.get('source_table'), part.get('record_role')
            if table not in summary_tables or role not in ('observation', 'context'):
                continue
            descriptor = descriptors.get(table)
            if not isinstance(descriptor, Mapping):
                raise ArchiveError('schema_descriptor_unavailable')
            partition_field = bool(part.get('partition_field'))
            if table in partition_by_table and partition_by_table[table] != partition_field:
                raise ArchiveError('archive_integrity_failed')
            partition_by_table[table] = partition_field
            path_text = part.get('path')
            if not isinstance(path_text, str) or not re.fullmatch(
                r'(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.copy', path_text
            ):
                raise ArchiveError('archive_integrity_failed')
            original_path = capture_dir / path_text
            if original_path.is_symlink():
                raise ArchiveError('archive_integrity_failed')
            source_path = original_path.resolve(strict=True)
            if capture_dir not in source_path.parents:
                raise ArchiveError('archive_integrity_failed')
            input_count, _input_size, input_digest = await heavy_work(
                lambda worker, p=source_path: _copy_metrics(p, worker, enforce=False))
            if input_count != int(part.get('row_count', -1)) or input_digest != part.get('canonical_sha256'):
                raise ArchiveError('archive_integrity_failed')
            if table not in created:
                await _create_source_page(conn, table, descriptor, partition_field)
                created.add(table)
            table_name = f'archive_graph_{table}'
            await conn.execute(f'TRUNCATE TABLE {_identifier(table_name)}')
            columns = [column['name'] for column in _descriptor_columns(descriptor, partition_field)]
            await conn.copy_to_table(table_name, source=source_path, columns=columns, format='binary')
            if role != 'observation':
                continue
            if table == 'measurement':
                unmatched = await conn.fetchval(
                    "SELECT count(*) FROM archive_graph_measurement m WHERE m.record_role='observation' "
                    "AND m.dataset_id=$1 AND m.storage_revision=$2 AND NOT EXISTS ("
                    "SELECT 1 FROM archive_graph_sensor_identity i WHERE i.sensor_id=m.sensor_id "
                    "AND m.time>=i.start_at AND m.time<i.end_at)", dataset_id, revision)
                if unmatched:
                    raise ArchiveError('grow_scope_unresolved')
            for width in (60, 300, 3600):
                await conn.execute(_summary_sql(table), width, dataset_id, revision)
        fact_index = 0
        for part in source_parts:
            table = part.get('source_table')
            if table not in ('monitoring_room_photoperiod', 'monitoring_photoperiod_coverage') or part.get('record_role') != 'observation':
                continue
            descriptor = descriptors.get(table)
            if not isinstance(descriptor, Mapping):
                raise ArchiveError('schema_descriptor_unavailable')
            path_text = part.get('path')
            if not isinstance(path_text, str) or not re.fullmatch(
                r'(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.copy', path_text
            ):
                raise ArchiveError('archive_integrity_failed')
            original_path = capture_dir / path_text
            if original_path.is_symlink():
                raise ArchiveError('archive_integrity_failed')
            source_path = original_path.resolve(strict=True)
            if capture_dir not in source_path.parents:
                raise ArchiveError('archive_integrity_failed')
            destination = capture_dir / f'graph_{table}_{fact_index:07d}.copy'
            copied = await heavy_work(lambda worker: _copy_facts(source_path, destination,
                                      len(descriptor['columns']), worker))
            if copied is None:
                continue
            graph_parts.append({'path': destination.name, 'source_table': table,
                                'record_role': 'graph',
                                'graph_kind': 'photoperiod' if table == 'monitoring_room_photoperiod' else 'coverage',
                                'row_count': copied['row_count'],
                                'canonical_sha256': copied['canonical_sha256'],
                                'partition_field': bool(part.get('partition_field'))})
            fact_index += 1
        graph_parts.extend(await _write_summary_parts(conn, capture_dir, dataset_id, revision, heavy_work))
    return {'parts': graph_parts, 'graph_schema': graph_schema}
