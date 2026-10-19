"""Archive catalog reads and scope corrections under Calendar room fences."""
from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re
from typing import Any, Literal
from uuid import UUID
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg

from grow_archive_service.models import ArchiveError

GROW_PHASES = ('clone_window', 'pot_veg', 'bed_veg', 'flip_to_flower',
               'flower_stretch', 'flower_bulk', 'flower_ripen', 'drying', 'harvest')
_ROOMS = frozenset(('Flower Room', 'Veg Room'))
_PRIVATE_PROGRESS_KEY = re.compile(r'(?:secret|password|credential|authorization|lease.?token|token|url|path|dsn|raw|payload|manifest)', re.I)


@dataclass(frozen=True, slots=True)
class ScopeCorrection:
    override_id: int
    source_table: Literal['measurement']
    source_identity: Mapping[str, int]
    start: datetime
    end: datetime
    room: str
    confirmed_at: datetime
    affected_dataset_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ArchivePage:
    items: tuple[dict[str, Any], ...]
    next_cursor: str | None


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


def _public_progress(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        return {}
    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): clean(child) for key, child in item.items() if not _PRIVATE_PROGRESS_KEY.search(str(key))}
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        return item
    return clean(value)


def _cursor_encode(sort_at: datetime, dataset_id: UUID) -> str:
    payload = json.dumps({'end': sort_at.isoformat(), 'id': str(dataset_id)},
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(payload).rstrip(b'=').decode('ascii')


def _cursor_decode(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    if cursor is None:
        return None, None
    if len(cursor) > 512 or not re.fullmatch(r'[A-Za-z0-9_-]+', cursor):
        raise ArchiveError('archive_cursor_invalid', status_code=400)
    try:
        raw = base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4))
        value = json.loads(raw)
        if set(value) != {'end', 'id'}:
            raise ValueError
        instant = datetime.fromisoformat(value['end'].replace('Z', '+00:00'))
        dataset_id = UUID(value['id'])
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError
        return instant, dataset_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ArchiveError('archive_cursor_invalid', status_code=400) from error


def _manifest_gaps(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = manifest.get('gaps')
    if not isinstance(raw, (list, tuple)):
        return ()
    result: list[dict[str, Any]] = []
    for gap in raw:
        if not isinstance(gap, Mapping):
            continue
        start = gap.get('start', gap.get('start_at'))
        end = gap.get('end', gap.get('end_at'))
        if not start or not end:
            continue
        try:
            lo = start if isinstance(start, datetime) else datetime.fromisoformat(str(start).replace('Z', '+00:00'))
            hi = end if isinstance(end, datetime) else datetime.fromisoformat(str(end).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            continue
        if lo.tzinfo is None or hi.tzinfo is None or lo >= hi:
            continue
        tables = gap.get('source_tables', gap.get('source_table', ()))
        if isinstance(tables, str):
            tables = (tables,)
        result.append({'start': lo, 'end': hi, 'reason': str(gap.get('reason', gap.get('code', 'recorded_gap'))),
                       'source_tables': tuple(str(table) for table in tables) if isinstance(tables, (list, tuple)) else ()})
    return tuple(result)


def _remote_file_url(root: object, remote_path: object) -> str | None:
    if not isinstance(root, str) or not isinstance(remote_path, str) or not remote_path:
        return None
    try:
        parsed = urlsplit(root)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None
        segments = remote_path.split('/')
        if any(segment in ('', '.', '..') or '\\' in segment or '\x00' in segment for segment in segments):
            return None
        path = parsed.path.rstrip('/') + '/' + '/'.join(quote(segment, safe='') for segment in segments)
        return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))
    except (ValueError, UnicodeError):
        return None


_CATALOG_SQL = """
WITH catalog AS (
    SELECT d.*, g.state AS lifecycle_state,
           COALESCE(NULLIF(d.manifest->>'available_from','')::timestamptz,d.available_from,
             (SELECT min(w.start_at) FROM public.archive_window w
              WHERE w.dataset_id=d.dataset_id AND w.calendar_revision=d.calendar_revision)) AS entry_start,
           COALESCE(NULLIF(d.manifest->>'available_to','')::timestamptz,d.available_to,
             (SELECT max(w.end_at) FROM public.archive_window w
              WHERE w.dataset_id=d.dataset_id AND w.calendar_revision=d.calendar_revision)) AS entry_end,
           COALESCE(NULLIF(d.manifest->>'available_to','')::timestamptz,d.available_to,
                    NULLIF(d.manifest->>'available_from','')::timestamptz,d.available_from,d.created_at) AS sort_at
    FROM public.archive_dataset d
    LEFT JOIN public.grow_lifecycle g USING (grow_plan_id)
    WHERE d.room=$1
), selected AS (
    SELECT c.* FROM catalog c
    WHERE ($2::timestamptz IS NULL OR (c.sort_at,c.dataset_id)<($2::timestamptz,$3::uuid))
    ORDER BY c.sort_at DESC,c.dataset_id DESC
    LIMIT $4
)
SELECT d.*,
       lj.job_id AS latest_job_id, lj.kind AS latest_job_kind, lj.requested_revision AS latest_job_revision,
       lj.state AS latest_job_state, lj.checkpoint AS latest_job_checkpoint,
       lj.last_error_code AS latest_job_error, lj.attempts AS latest_job_attempts,
       lj.next_attempt_at AS latest_job_next_attempt, lj.created_at AS latest_job_created,
       lj.updated_at AS latest_job_updated,
       ej.state AS export_job_state,
       own.artifact_id AS own_artifact_id, own.revision AS own_artifact_revision,
       own.remote_path AS own_remote_path, own.remote_instance_id AS own_instance_id,
       own.byte_size AS own_byte_size, own.local_path AS own_local_path,
       own.local_verified_at AS own_local_verified_at,
       parent.dataset_id AS container_dataset_id, parent.calendar_year AS container_year,
       parent.artifact_id AS container_artifact_id, parent.revision AS container_revision,
       parent.remote_path AS container_remote_path, parent.remote_instance_id AS container_instance_id,
       parent.byte_size AS container_byte_size, parent.local_path AS container_local_path,
       parent.local_verified_at AS container_local_verified_at, parent.member_prefix AS container_member_prefix,
       parent.child_revision AS container_child_revision,
       own_conn.files_root_url AS own_files_root_url, parent_conn.files_root_url AS parent_files_root_url,
       EXISTS (SELECT 1 FROM public.archive_availability av
               WHERE av.dataset_id=d.dataset_id AND av.state='captured'
                 AND av.source_rows_removed) AS source_rows_removed
FROM selected d
LEFT JOIN LATERAL (
    SELECT j.* FROM public.archive_job j WHERE j.dataset_id=d.dataset_id
    ORDER BY CASE WHEN j.state IN ('pending','running','blocked') THEN 0 ELSE 1 END,
             j.updated_at DESC,j.job_id DESC LIMIT 1
) lj ON TRUE
LEFT JOIN LATERAL (
    SELECT j.state FROM public.archive_job j
    WHERE j.dataset_id=d.dataset_id AND j.kind IN ('export','annual_merge')
      AND j.requested_revision=d.data_revision
    ORDER BY CASE WHEN j.state IN ('pending','running','blocked') THEN 0 ELSE 1 END,
             j.updated_at DESC,j.job_id DESC LIMIT 1
) ej ON TRUE
LEFT JOIN LATERAL (
    SELECT a.* FROM public.archive_artifact a
    WHERE a.dataset_id=d.dataset_id AND a.revision=COALESCE(d.published_revision,d.data_revision)
      AND a.state='verified' ORDER BY a.verified_at DESC,a.artifact_id LIMIT 1
) own ON TRUE
LEFT JOIN LATERAL (
    SELECT pa.*, pd.dataset_id AS container_dataset_id, pd.calendar_year,
           ac.member_prefix, ac.child_revision
    FROM public.archive_component ac
    JOIN public.archive_artifact pa ON pa.artifact_id=ac.parent_artifact_id AND pa.state='verified'
    JOIN public.archive_dataset pd ON pd.dataset_id=pa.dataset_id AND pd.kind='veg_year'
    WHERE ac.child_dataset_id=d.dataset_id
      AND ac.child_revision=COALESCE(d.published_revision,d.data_revision)
    ORDER BY pa.verified_at DESC,pa.artifact_id LIMIT 1
) parent ON TRUE
LEFT JOIN public.calendar_sync_connection own_conn ON own_conn.instance_id=own.remote_instance_id
LEFT JOIN public.calendar_sync_connection parent_conn ON parent_conn.instance_id=parent.remote_instance_id
ORDER BY d.sort_at DESC,d.dataset_id DESC
"""


class ArchiveRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def list_entries(self, room: str, cursor: str | None, limit: int) -> ArchivePage:
        if room not in _ROOMS:
            raise ArchiveError('archive_room_invalid', status_code=400)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ArchiveError('archive_limit_invalid', status_code=400)
        sort_at, dataset_id = _cursor_decode(cursor)
        rows = await self.pool.fetch(_CATALOG_SQL, room, sort_at, dataset_id, limit + 1)
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = tuple(self._entry(dict(row)) for row in selected)
        next_cursor = _cursor_encode(selected[-1]['sort_at'], selected[-1]['dataset_id']) if has_more and selected else None
        return ArchivePage(items, next_cursor)

    async def entry(self, dataset_id: UUID) -> dict[str, Any]:
        row = await self._entry_row(dataset_id)
        return self._entry(dict(row))

    async def load_download_metadata(
        self, artifact_id: UUID | None, dataset_id: UUID, revision: int,
    ) -> dict[str, Any]:
        """Describe the pinned artifact, not a later catalog publication."""
        if artifact_id is None:
            return {'remote_download_url': None, 'download_label': None}
        row = await self.pool.fetchrow(
            "SELECT a.remote_path,d.kind,d.calendar_year,conn.files_root_url "
            "FROM public.archive_artifact a "
            "JOIN public.archive_dataset d ON d.dataset_id=a.dataset_id "
            "LEFT JOIN public.archive_component c ON c.parent_artifact_id=a.artifact_id "
            "AND c.child_dataset_id=$2 AND c.child_revision=$3 "
            "LEFT JOIN public.calendar_sync_connection conn ON conn.instance_id=a.remote_instance_id "
            "WHERE a.artifact_id=$1 AND a.verified_at IS NOT NULL "
            "AND a.state IN ('verified','superseded') "
            "AND ((a.dataset_id=$2 AND a.revision=$3) OR c.child_dataset_id IS NOT NULL) LIMIT 1",
            artifact_id, dataset_id, revision,
        )
        if row is None:
            raise ArchiveError('archive_integrity_failed')
        label = (f"Download {row['calendar_year']} ZIP (all four quarters)"
                 if row['kind'] == 'veg_year' else f'Download verified ZIP (r{revision})')
        return {
            'remote_download_url': _remote_file_url(row['files_root_url'], row['remote_path']),
            'download_label': label,
        }

    async def detail(self, dataset_id: UUID) -> dict[str, Any]:
        row = await self._entry_row(dataset_id)
        item = self._entry(dict(row))
        manifest = _json_object(row['manifest'])
        availability = await self.pool.fetch(
            'SELECT source_table,room,start_at,end_at,state,source_rows_removed,aggregates_refreshed '
            'FROM public.archive_availability WHERE dataset_id=$1 ORDER BY start_at,source_table,room', dataset_id)
        phases = manifest.get('phases', ())
        if not phases and row['grow_plan_id'] is not None:
            phases = await self.pool.fetch(
                "SELECT e.id AS event_id,e.event_type AS phase_type,e.location AS room,"
                "(e.start_date::timestamp AT TIME ZONE 'America/Toronto') AS start_at,"
                "((COALESCE(e.end_date,e.start_date)+1)::timestamp AT TIME ZONE 'America/Toronto') AS end_at,"
                "e.start_date::text AS start_date,e.end_date::text AS end_date "
                "FROM public.calendar_event e WHERE e.grow_plan_id=$1 AND e.deleted_at IS NULL "
                "AND e.event_type=ANY($2::text[]) ORDER BY e.start_date,e.id",
                row['grow_plan_id'],list(GROW_PHASES))
        snapshots = manifest.get('identity_snapshots', ())
        schema = {'source_schema': manifest.get('source_schema', {}),
                  'graph_schema': manifest.get('graph_schema', {})}
        return {**item, 'phases': tuple(phases) if isinstance(phases, (tuple, list)) else (),
                'schema': schema,
                'availability': tuple(dict(record) for record in availability),
                'identity_snapshots': tuple(snapshots) if isinstance(snapshots, (tuple, list)) else ()}

    async def export_preflight(self, dataset_id: UUID, expected_revision: int) -> Mapping[str, Any]:
        row = await self.pool.fetchrow(
            'SELECT d.*,g.state AS lifecycle_state FROM public.archive_dataset d '
            'LEFT JOIN public.grow_lifecycle g USING(grow_plan_id) WHERE d.dataset_id=$1', dataset_id)
        if row is None:
            raise ArchiveError('archive_not_found', status_code=404)
        if row['data_revision'] != expected_revision:
            raise ArchiveError('archive_revision_conflict')
        if row['kind'] == 'grow' and row['lifecycle_state'] != 'closed':
            raise ArchiveError('grow_not_closed')
        return row

    async def graph_context(self, dataset_id: UUID) -> dict[str, Any]:
        row = await self.pool.fetchrow(
            'SELECT d.*,g.state AS lifecycle_state FROM public.archive_dataset d '
            'LEFT JOIN public.grow_lifecycle g USING(grow_plan_id) WHERE d.dataset_id=$1', dataset_id)
        if row is None:
            raise ArchiveError('archive_not_found', status_code=404)
        item = dict(row)
        item['manifest'] = _json_object(item.get('manifest'))
        return item

    async def download_name(self, artifact_id: UUID, dataset_id: UUID, revision: int) -> str:
        row = await self.pool.fetchrow(
            "SELECT a.remote_path,a.revision,a.state,a.dataset_id,d.kind,d.calendar_year,d.quarter "
            "FROM public.archive_artifact a JOIN public.archive_dataset d USING(dataset_id) "
            "WHERE a.artifact_id=$1 AND a.state='verified'", artifact_id)
        if row is None:
            raise ArchiveError('archive_integrity_failed')
        if row['dataset_id'] == dataset_id:
            if row['revision'] != revision:
                raise ArchiveError('archive_integrity_failed')
        else:
            component = await self.pool.fetchval(
                'SELECT EXISTS(SELECT 1 FROM public.archive_component WHERE parent_artifact_id=$1 '
                'AND child_dataset_id=$2 AND child_revision=$3)', artifact_id, dataset_id, revision)
            if row['kind'] != 'veg_year' or not component:
                raise ArchiveError('archive_integrity_failed')
        name = str(row['remote_path']).rsplit('/', 1)[-1]
        if not re.fullmatch(r'[A-Za-z0-9._-]+\.zip', name):
            raise ArchiveError('archive_integrity_failed')
        return name

    async def _entry_row(self, dataset_id: UUID):
        row = await self.pool.fetchrow(_CATALOG_SQL.replace('WHERE d.room=$1', 'WHERE d.dataset_id=$1'),
                                       dataset_id, None, None, 1)
        if row is None:
            raise ArchiveError('archive_not_found', status_code=404)
        return row

    @staticmethod
    def _entry(row: Mapping[str, Any]) -> dict[str, Any]:
        manifest = _json_object(row.get('manifest'))
        job = None
        if row.get('latest_job_id') is not None:
            checkpoint = _json_object(row.get('latest_job_checkpoint'))
            job = {
                'job_id': row['latest_job_id'], 'dataset_id': row['dataset_id'],
                'kind': row['latest_job_kind'], 'requested_revision': row['latest_job_revision'],
                'state': row['latest_job_state'], 'progress': _public_progress(checkpoint.get('progress')),
                'last_error_code': row.get('latest_job_error'), 'attempts': row['latest_job_attempts'],
                'next_attempt_at': row['latest_job_next_attempt'], 'created_at': row['latest_job_created'],
                'updated_at': row['latest_job_updated'],
            }
        own_url = _remote_file_url(row.get('own_files_root_url'), row.get('own_remote_path'))
        parent_url = _remote_file_url(row.get('parent_files_root_url'), row.get('container_remote_path'))
        use_parent = row.get('container_artifact_id') is not None
        remote_url = parent_url if use_parent else own_url
        byte_size = row.get('container_byte_size') if use_parent else row.get('own_byte_size')
        local_path = row.get('container_local_path') if use_parent else row.get('own_local_path')
        local_verified = row.get('container_local_verified_at') if use_parent else row.get('own_local_verified_at')
        if not row.get('source_rows_removed'):
            local_tier = 'hot'
        elif local_path and local_verified and Path(str(local_path)).is_file():
            local_tier = 'zip_cache'
        elif remote_url:
            local_tier = 'remote_zip'
        else:
            local_tier = 'none'
        verified_remote_path = row.get('container_remote_path') if use_parent else row.get('own_remote_path')
        current_export_revision = row.get('container_child_revision') if use_parent else row.get('own_artifact_revision')
        export_status = ('verified' if verified_remote_path and byte_size is not None
                         and current_export_revision == row['data_revision'] else 'not_exported')
        export_job_state = row.get('export_job_state')
        if export_status != 'verified' and export_job_state in ('pending', 'running'):
            export_status = 'pending'
        elif export_status != 'verified' and export_job_state in ('blocked', 'failed'):
            export_status = 'blocked'
        coverage = manifest.get('coverage')
        if coverage not in ('complete', 'gapped'):
            coverage = 'unknown'
        gaps = list(_manifest_gaps(manifest))
        if gaps:
            coverage = 'gapped'
        kind = row['kind']
        if kind == 'grow':
            name = f"Grow {row.get('grow_plan_id')}"
        elif kind == 'veg_quarter':
            name = f"Veg {row.get('calendar_year')} Q{row.get('quarter')}"
        else:
            name = f"Veg {row.get('calendar_year')}"
        container = None
        download_label = 'Download verified ZIP' if remote_url else None
        if use_parent:
            container = {'dataset_id': row['container_dataset_id'], 'revision': row['container_revision'],
                         'kind': 'veg_year', 'remote_download_url': parent_url}
            download_label = f"Download {row['container_year']} ZIP (all four quarters)"
        return {
            'dataset_id': row['dataset_id'], 'kind': kind, 'name': name, 'room': row['room'],
            'start': row.get('entry_start'), 'end': row.get('entry_end'), 'state': row['state'],
            'revision': row['data_revision'], 'published_revision': row.get('published_revision'),
            'lifecycle_state': row.get('lifecycle_state'), 'anniversary_at': row.get('anniversary_at'),
            'local_tier': local_tier, 'export_status': export_status, 'zip_bytes': byte_size,
            'coverage': coverage, 'gap_intervals': tuple(gaps), 'job': job,
            'remote_download_url': remote_url, 'download_label': download_label, 'container': container,
        }


    async def confirm_scope_assignment(
        self, dataset_id: UUID | None, expected_revision: int | None,
        source_table: Literal['measurement'],
        source_identity: Mapping[str, int], start: datetime, end: datetime, room: str,
    ) -> ScopeCorrection:
        if source_table != 'measurement':
            raise ArchiveError('archive_scope_correction_invalid', status_code=400)
        key = 'sensor_id'
        if (set(source_identity) != {key} or type(source_identity[key]) is not int
                or source_identity[key] <= 0 or start.tzinfo is None or end.tzinfo is None
                or start.utcoffset() is None or end.utcoffset() is None or start >= end
                or (dataset_id is None and expected_revision is not None)):
            raise ArchiveError('archive_scope_correction_invalid', status_code=400)
        identity = json.dumps(dict(source_identity), sort_keys=True, separators=(',', ':'))
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('sj-history-source:'||$1||':'||$2,0))",
                source_table, identity)
            removed = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.archive_availability "
                "WHERE source_table=$1 AND source_rows_removed AND start_at<$3 AND end_at>$2)",
                source_table, start, end)
            if removed:
                raise ArchiveError('archive_correction_requires_source', status_code=409)
            if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM room WHERE name=$1)',room):
                raise ArchiveError('archive_scope_room_invalid',status_code=400)
            placement = await conn.fetchrow(
                'SELECT s.sensor_id,d.device_id,r.name AS room FROM sensor s '
                'JOIN device d USING(device_id) LEFT JOIN rack k USING(rack_id) '
                'LEFT JOIN room r USING(room_id) WHERE s.sensor_id=$1', source_identity[key])
            if placement is None:
                raise ArchiveError('archive_scope_identity_invalid',status_code=400)
            rooms = {room}
            if placement['room'] is not None:
                rooms.add(placement['room'])
            else:
                rooms.update(await conn.fetchval('SELECT array_agg(name ORDER BY name) FROM room') or [])
            if await conn.fetchval("SELECT to_regclass('public.sensor_registry') IS NOT NULL"):
                registry_room = await conn.fetchval(
                    'SELECT r.name FROM sensor_registry s LEFT JOIN room r USING(room_id) WHERE s.device_id=$1',
                    placement['device_id'])
                if registry_room:
                    rooms.add(registry_room)
            previous = await conn.fetch(
                'SELECT * FROM archive_scope_override WHERE source_table=$1 AND source_identity=$2::jsonb '
                'AND start_at<$4 AND end_at>$3 ORDER BY start_at',source_table,identity,start,end)
            rooms.update(row['room'] for row in previous)
            frozen = await conn.fetch(
                "SELECT DISTINCT snapshot->>'room' AS room FROM archive_dataset d "
                "CROSS JOIN LATERAL jsonb_array_elements(COALESCE(d.manifest->'identity_snapshots','[]'::jsonb)) snapshot "
                "WHERE snapshot->>'source_table'=$1 AND snapshot->'source_identity'=$2::jsonb "
                "AND (snapshot->>'start')::timestamptz<$4 AND (snapshot->>'end')::timestamptz>$3",
                source_table,identity,start,end)
            rooms.update(row['room'] for row in frozen if row['room'])
            for locked_room in sorted(rooms):
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('sj-history-room:'||$1,0))",locked_room)
            # Top-level UUIDs only. A null caller dataset cannot bypass any
            # overlapping closed grow, including its archived old membership.
            grow_ids = await conn.fetchval(
                "SELECT array_agg(DISTINCT grow_plan_id) FROM ("
                "SELECT e.grow_plan_id FROM calendar_event e WHERE e.deleted_at IS NULL AND e.grow_plan_id IS NOT NULL "
                "AND e.event_type=ANY($4::text[]) AND e.location=ANY($1::text[]) "
                "AND (e.start_date::timestamp AT TIME ZONE 'America/Toronto')<$3 "
                "AND ((COALESCE(e.end_date,e.start_date)+1)::timestamp AT TIME ZONE 'America/Toronto')>$2 "
                "UNION SELECT d.grow_plan_id FROM archive_dataset d JOIN archive_window w USING(dataset_id) "
                "WHERE d.grow_plan_id IS NOT NULL AND w.room=ANY($1::text[]) AND w.start_at<$3 AND w.end_at>$2 "
                "UNION SELECT grow_plan_id FROM archive_dataset WHERE dataset_id=$5 AND grow_plan_id IS NOT NULL) g",
                sorted(rooms),start,end,list(GROW_PHASES),dataset_id) or []
            grows = await conn.fetch('SELECT * FROM grow_lifecycle WHERE grow_plan_id=ANY($1::uuid[]) ORDER BY grow_plan_id FOR UPDATE',grow_ids)
            if any(grow['state']=='closed' for grow in grows):
                raise ArchiveError('grow_closed')
            datasets = await conn.fetch(
                "WITH affected AS (SELECT d.dataset_id FROM archive_dataset d "
                "WHERE d.dataset_id=$1 OR d.grow_plan_id=ANY($2::uuid[]) "
                "OR EXISTS(SELECT 1 FROM archive_availability a WHERE a.dataset_id=d.dataset_id "
                "AND a.source_table=$3 AND (a.room IS NULL OR a.room=ANY($4::text[])) AND a.start_at<$6 AND a.end_at>$5)) "
                "SELECT d.* FROM archive_dataset d WHERE dataset_id IN ("
                "SELECT dataset_id FROM affected UNION SELECT ar.dataset_id FROM archive_component c "
                "JOIN archive_artifact ar ON ar.artifact_id=c.parent_artifact_id "
                "WHERE c.child_dataset_id IN (SELECT dataset_id FROM affected) AND ar.state='verified') "
                "ORDER BY d.dataset_id FOR UPDATE", dataset_id,grow_ids,source_table,sorted(rooms),start,end)
            if dataset_id is not None:
                selected = next((d for d in datasets if d['dataset_id']==dataset_id),None)
                if selected is None:
                    raise ArchiveError('archive_not_found',status_code=404)
                if expected_revision is None or selected['data_revision'] != expected_revision:
                    raise ArchiveError('archive_revision_conflict')
            for old in previous:
                if old['start_at'] != start or old['end_at'] != end:
                    raise ArchiveError('archive_scope_overlap')
            affected = tuple(d['dataset_id'] for d in datasets)
            if previous and previous[0]['room']==room:
                row = previous[0]
            else:
                if previous:
                    row = await conn.fetchrow(
                        'UPDATE archive_scope_override SET room=$2,confirmed_at=clock_timestamp() '
                        'WHERE override_id=$1 RETURNING *',previous[0]['override_id'],room)
                else:
                    row = await conn.fetchrow(
                        'INSERT INTO archive_scope_override(source_table,source_identity,start_at,end_at,room) '
                        'VALUES($1,$2::jsonb,$3,$4,$5) RETURNING *',source_table,identity,start,end,room)
                await conn.execute(
                    "UPDATE archive_dataset SET data_revision=data_revision+1,state='dirty',updated_at=clock_timestamp() "
                    'WHERE dataset_id=ANY($1::uuid[])',list(affected))
                await conn.execute(
                    "UPDATE archive_job SET state='failed',last_error_code='archive_job_superseded',"
                    "lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() "
                    "WHERE dataset_id=ANY($1::uuid[]) AND state IN ('pending','running','blocked')",list(affected))
            return ScopeCorrection(row['override_id'],source_table,dict(source_identity),start,end,room,
                                   row['confirmed_at'],affected)
