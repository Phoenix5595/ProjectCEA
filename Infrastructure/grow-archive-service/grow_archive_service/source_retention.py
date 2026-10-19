"""Operational anchors that must survive archive movement and age expiry."""
from __future__ import annotations

from .models import ArchiveError
from .schema import SourceStream

# The only source tables any archive path may ever delete from: timestamped
# time-series observations. Every source DELETE goes through
# removable_observation_sql, so registries, ids, setpoint/schedule config,
# Calendar periods and context streams (config_versions,
# calendar_mode_application) are structurally undeletable.
# Match operational reader partition keys. Keeping all timestamp ties is a
# deliberate conservative superset: an ambiguous predecessor must not vanish.
ANCHOR_COLUMNS = {
    'measurement': ('sensor_id',),
    'automation_state': ('location', 'cluster', 'device_name'),
    'effective_setpoints': ('location', 'cluster', 'device_name', 'mode'),
    'control_history': ('location', 'cluster', 'device_name', 'channel'),
    'relay_observation': ('channel',),
    'monitoring_room_photoperiod': ('location', 'cluster', 'source'),
    'monitoring_photoperiod_coverage': ('location', 'cluster'),
    'mode_transition_history': ('location', 'cluster'),
    'pid_parameter_history': ('location', 'cluster', 'device_type'),
}


def _retention_keys(stream: SourceStream) -> tuple[str, ...]:
    if (stream.name not in ANCHOR_COLUMNS or stream.time_column is None
            or stream.default_record_role != 'observation'):
        raise ArchiveError('archive_source_protected')
    names = {column.name for column in stream.columns}
    columns = ANCHOR_COLUMNS[stream.name]
    if any(name not in names for name in columns):
        raise ArchiveError('schema_descriptor_unavailable')
    return columns


async def prepare_retention_anchors(conn, stream: SourceStream, cutoff):
    """Materialize each same-key predecessor once for a fenced delete run."""
    columns = _retention_keys(stream)
    table = f'public."{stream.name}"'
    time = f'm."{stream.time_column}"'
    keys = ','.join(f'm."{name}"' for name in columns)
    relation_name = f'archive_retention_anchor_{stream.name}'
    await conn.execute(
        f'CREATE TEMP TABLE "{relation_name}" ON COMMIT DROP AS '
        f'SELECT record_send(ROW({keys})) AS anchor_key,max({time}) AS predecessor '
        f'FROM {table} m WHERE {time}<$1::timestamptz GROUP BY {keys}',
        cutoff,
    )
    await conn.execute(f'CREATE UNIQUE INDEX ON pg_temp."{relation_name}" (anchor_key)')
    return f'pg_temp."{relation_name}"'


def removable_observation_sql(
    stream: SourceStream, *, alias: str = 's', cutoff: str = '$1',
    anchor_relation: str | None = None,
) -> str:
    """SQL predicate for old observations, never static/global/null-time context.

    A caller processing slices passes a run-local predecessor relation so the
    source history is grouped once rather than rescanned for every candidate.
    Without it, the correlated form is retained for small standalone checks.
    """
    columns = _retention_keys(stream)
    time = f'{alias}."{stream.time_column}"'
    if anchor_relation is None:
        same = ' AND '.join(f'a."{name}" IS NOT DISTINCT FROM {alias}."{name}"' for name in columns)
        relation = f'public."{stream.name}"'
        latest = f'(SELECT max(a."{stream.time_column}") FROM {relation} a WHERE {same})'
        predecessor = (f'(SELECT max(a."{stream.time_column}") FROM {relation} a '
                       f'WHERE {same} AND a."{stream.time_column}"<{cutoff}::timestamptz)')
        anchor_rule = f'{time}<{latest} AND {time}<{predecessor}'
    else:
        expected = f'pg_temp."archive_retention_anchor_{stream.name}"'
        if anchor_relation != expected:
            raise ArchiveError('schema_descriptor_unavailable')
        row_key = ','.join(f'{alias}."{name}"' for name in columns)
        predecessor = (
            f'(SELECT p.predecessor FROM {anchor_relation} p '
            f'WHERE p.anchor_key=record_send(ROW({row_key})))'
        )
        # A predecessor is itself a source row, so it is necessarily no later
        # than the key's latest row.
        anchor_rule = f'{time}<{predecessor}'
    names = {column.name for column in stream.columns}
    predicates = [f'{time} IS NOT NULL', f'{time}<{cutoff}::timestamptz', anchor_rule]
    if 'location' in names:
        predicates.append(f'{alias}.location IS NOT NULL')
    if stream.name in ('effective_setpoints', 'monitoring_room_photoperiod'):
        predicates.append(f"{time}<{cutoff}::timestamptz-interval '60 seconds'")
    if stream.name == 'control_history':
        # Due-but-not-cleared rows still belong to the manual expiry consumer.
        predicates.append(f'{alias}.manual_expires_at IS NULL')
    if stream.name == 'relay_observation':
        predicates.append(f'{alias}.channel IS NOT NULL')
        predicates.append(f"{alias}.reason NOT IN ('heartbeat','session_started','session_ended')")
    return '(' + ' AND '.join(predicates) + ')'
