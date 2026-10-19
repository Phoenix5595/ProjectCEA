"""Authoritative half-open archive scopes; registry placement never guesses history."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Mapping, Sequence

from .models import ArchiveError
from .schema import SourceStream


@dataclass(frozen=True, slots=True)
class CaptureWindow:
    room: str
    start: datetime
    end: datetime


def merge_windows(windows: Sequence[CaptureWindow]) -> tuple[CaptureWindow, ...]:
    merged: list[CaptureWindow] = []
    for window in sorted(windows, key=lambda w: (w.room, w.start, w.end)):
        if window.start.tzinfo is None or window.end.tzinfo is None or window.start >= window.end:
            raise ArchiveError('grow_dates_invalid')
        if merged and merged[-1].room == window.room and window.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = CaptureWindow(last.room, last.start, max(last.end, window.end))
        else:
            merged.append(window)
    return tuple(merged)


async def load_windows(conn, dataset: Mapping[str, Any]) -> tuple[CaptureWindow, ...]:
    if dataset['kind'] == 'grow':
        rows = await conn.fetch(
            "SELECT room,start_at,end_at FROM archive_window WHERE dataset_id=$1 "
            "AND calendar_revision=$2 AND room='Flower Room' ORDER BY start_at,end_at",
            dataset['dataset_id'], dataset['calendar_revision'],
        )
        windows = tuple(CaptureWindow(row['room'], row['start_at'], row['end_at']) for row in rows)
    else:
        if dataset['room'] != 'Veg Room':
            raise ArchiveError('grow_scope_unresolved')
        windows = (CaptureWindow('Veg Room', dataset['available_from'], dataset['available_to']),)
    if not windows:
        raise ArchiveError('grow_dates_invalid')
    return merge_windows(windows)


def window_arrays(windows: Sequence[CaptureWindow]) -> tuple[list[str], list[datetime], list[datetime]]:
    return ([w.room for w in windows], [w.start for w in windows], [w.end for w in windows])


def time_predicate(time_column: str | None, *, alias: str = 'm') -> str:
    # Column names originate exclusively in the checked-in, preflighted descriptor.
    if time_column is None:
        raise ArchiveError('schema_descriptor_unavailable')
    return ("EXISTS (SELECT 1 FROM unnest($1::text[],$2::timestamptz[],$3::timestamptz[]) "
            f"AS w(room,start_at,end_at) WHERE {alias}.\"{time_column}\">=w.start_at "
            f"AND {alias}.\"{time_column}\"<w.end_at)")


def identity_room_sql(stream: SourceStream, *, alias: str = 'm') -> str:
    """Same override-first/frozen-only qualification used by Calendar close-preview."""
    identity = 'sensor_id' if stream.name == 'measurement' else 'device_id'
    time = f'{alias}."{stream.time_column}"'
    value = f'jsonb_build_object(\'{identity}\',{alias}."{identity}")'
    override = ("SELECT o.room FROM archive_scope_override o WHERE "
                f"o.source_table='{stream.name}' AND o.source_identity={value} "
                f"AND o.start_at<={time} AND o.end_at>{time}")
    frozen = ("SELECT CASE WHEN count(DISTINCT snap->>'room')=1 THEN min(snap->>'room') END "
              "FROM archive_dataset d CROSS JOIN LATERAL "
              "jsonb_array_elements(COALESCE(d.manifest->'identity_snapshots','[]'::jsonb)) snap "
              f"WHERE snap->>'source_table'='{stream.name}' AND snap->'source_identity'={value} "
              f"AND (snap->>'start')::timestamptz<={time} AND (snap->>'end')::timestamptz>{time}")
    return f'(CASE WHEN EXISTS ({override}) THEN ({override}) ELSE ({frozen}) END)'


def ownership_sql(stream: SourceStream, *, alias: str = 'm') -> tuple[str, str]:
    """Return resolved room and raw role expressions without touching field values."""
    columns = {column.name for column in stream.columns}
    if stream.name in ('measurement', 'actuator_events'):
        return identity_room_sql(stream, alias=alias), "'observation'::text"
    if stream.name == 'calendar_mode_application':
        room = f'(SELECT e.location FROM calendar_event e WHERE e.id={alias}.event_id)'
        return room, "'context'::text"
    room = f'{alias}.location' if 'location' in columns else 'NULL::text'
    if stream.default_record_role == 'context':
        role = "'context'::text"
    else:
        role = f"CASE WHEN {room} IS NULL OR {alias}.\"{stream.time_column}\" IS NULL THEN 'context' ELSE 'observation' END"
    return room, role


def selection_sql(stream: SourceStream, *, alias: str = 'm', include_null_time: bool = False) -> tuple[str, str]:
    room, role = ownership_sql(stream, alias=alias)
    time = f'{alias}."{stream.time_column}"'
    room_match = f'({room}=w.room OR {room} IS NULL)'
    if stream.name in ('measurement', 'actuator_events'):
        room_match = f'{room}=w.room'
    if include_null_time:
        return f'{time} IS NULL AND ({room}=ANY($1::text[]) OR {room} IS NULL)', "'context'::text"
    predicate = ("EXISTS (SELECT 1 FROM unnest($1::text[],$2::timestamptz[],$3::timestamptz[]) "
                 f"AS w(room,start_at,end_at) WHERE {time}>=w.start_at AND {time}<w.end_at AND {room_match})")
    return predicate, role


async def require_resolved_scope(conn, streams: Sequence[SourceStream], windows: Sequence[CaptureWindow]) -> None:
    rooms, starts, ends = window_arrays(windows)
    unresolved = []
    for stream in streams:
        if stream.name not in ('measurement', 'actuator_events'):
            continue
        identity = 'sensor_id' if stream.name == 'measurement' else 'device_id'
        rows = await conn.fetch(
            f'SELECT m."{identity}" AS identity,min(m."{stream.time_column}") AS start_at,'
            f'max(m."{stream.time_column}")+interval \'1 microsecond\' AS end_at,count(*) AS rows '
            f'FROM public."{stream.name}" m WHERE {time_predicate(stream.time_column)} '
            f'AND {identity_room_sql(stream)} IS NULL GROUP BY m."{identity}"',
            rooms, starts, ends,
        )
        unresolved.extend({'source_table': stream.name, **dict(row)} for row in rows)
    if unresolved:
        raise ArchiveError('grow_scope_unresolved', detail={'scope_unresolved': unresolved})


async def identity_snapshots(conn, streams: Sequence[SourceStream], windows: Sequence[CaptureWindow]) -> list[dict[str, Any]]:
    """Freeze nonsecret naming/placement metadata for every authoritative interval."""
    snapshots: list[dict[str, Any]] = []
    rooms, starts, ends = window_arrays(windows)
    for stream in streams:
        if stream.name not in ('measurement', 'actuator_events'):
            continue
        identity = 'sensor_id' if stream.name == 'measurement' else 'device_id'
        ids = await conn.fetch(
            f'SELECT DISTINCT m."{identity}" AS identity FROM public."{stream.name}" m '
            f'WHERE {time_predicate(stream.time_column)} AND {identity_room_sql(stream)}=ANY($1::text[])',
            rooms, starts, ends,
        )
        for row in ids:
            value = row['identity']
            if stream.name == 'measurement':
                meta = await conn.fetchrow(
                    'SELECT s.name,s.unit,s.data_type,s.device_id,d.name AS device_name,'
                    'd.type AS device_type,d.rack_id,r.name AS rack_name,rm.name AS registry_room_at_capture '
                    'FROM sensor s JOIN device d USING(device_id) LEFT JOIN rack r USING(rack_id) '
                    'LEFT JOIN room rm USING(room_id) WHERE s.sensor_id=$1', value,
                )
            else:
                meta = await conn.fetchrow(
                    'SELECT d.name AS device_name,d.type AS device_type,d.rack_id,'
                    'r.name AS rack_name,rm.name AS registry_room_at_capture '
                    'FROM device d LEFT JOIN rack r USING(rack_id) LEFT JOIN room rm USING(room_id) '
                    'WHERE d.device_id=$1', value,
                )
            if meta is None:
                raise ArchiveError('grow_scope_unresolved')
            assignments = await conn.fetch(
                'SELECT room,start_at,end_at FROM archive_scope_override '
                'WHERE source_table=$1 AND source_identity=$2::jsonb',
                stream.name, json.dumps({identity: value}),
            )
            for assignment in assignments:
                for window in windows:
                    lo, hi = max(assignment['start_at'], window.start), min(assignment['end_at'], window.end)
                    if assignment['room'] == window.room and lo < hi:
                        snapshots.append({'source_table': stream.name, 'source_identity': {identity: value},
                                          'room': window.room, 'start': lo.isoformat(), 'end': hi.isoformat(), **dict(meta)})
    # Prior immutable snapshots remain truthful even if today's registry moved.
    prior = await conn.fetch("SELECT manifest->'identity_snapshots' AS snapshots FROM archive_dataset WHERE manifest ? 'identity_snapshots'")
    for row in prior:
        items = json.loads(row['snapshots']) if isinstance(row['snapshots'], str) else row['snapshots']
        for item in items or ():
            lo = datetime.fromisoformat(item['start'].replace('Z', '+00:00'))
            hi = datetime.fromisoformat(item['end'].replace('Z', '+00:00'))
            for window in windows:
                start, end = max(lo, window.start), min(hi, window.end)
                if item['room'] == window.room and start < end:
                    snapshots.append({**item, 'start': start.isoformat(), 'end': end.isoformat()})
    unique = {json.dumps(item, sort_keys=True): item for item in snapshots}
    return [unique[key] for key in sorted(unique)]
