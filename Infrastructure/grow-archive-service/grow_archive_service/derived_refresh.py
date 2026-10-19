"""Repair source-dependent summaries after verified source mutation.

preflight is read-only apart from temporary session advisory locks. It fails
closed on unknown dependency edges, missing expected readers, non-default
buckets, missing owners or invoker privileges. It never enables a job or
alters grants.

Relay hourly rows are a procedural dependency, not a CAGG. Archive DELETEs
do not dirty the existing global rebuild job. Repair their affected hours
explicitly using that job's exact slice and recorded-owner SELECT, then
refresh native CAGGs in dependency order with standalone top-level CALLs.
Coverage and archive_availability are not advanced by this module.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import asyncpg

from .execution import HeavyWorkCancelled, HeavyWorkExecutor
from .models import ArchiveError
from .schema import load_source_catalog


NAME: Final = re.compile(r'^[a-z][a-z0-9_]*$')
# Timescale 2.28 cannot refresh ROW()-ordered aggregates across source chunks.
ROW_CONSTRUCTOR: Final = re.compile(r'\brow\s*\(', re.IGNORECASE)
HOUR: Final = timedelta(hours=1)
DAY: Final = timedelta(days=1)
RELAY: Final = 'relay_observation'
RELAY_HOURLY: Final = 'monitoring_relay_duration_1hour'
MAINTENANCE_LOCK: Final = 'sj-cea-derived-history-refresh'
RELAY_LOCK: Final = 'projectcea.relay-overview-rebuild'
# name: (original source stream, immediate parent relation, fixed bucket width)
KNOWN: Final = {
    'monitoring_measurement_1min': ('measurement', 'measurement', timedelta(minutes=1)),
    'monitoring_measurement_5min': ('measurement', 'measurement', timedelta(minutes=5)),
    'monitoring_effective_setpoints_1min': ('effective_setpoints', 'effective_setpoints', timedelta(minutes=1)),
    'monitoring_effective_setpoints_5min': ('effective_setpoints', 'effective_setpoints', timedelta(minutes=5)),
    'monitoring_automation_state_1min': ('automation_state', 'automation_state', timedelta(minutes=1)),
    'monitoring_automation_state_5min': ('automation_state', 'automation_state', timedelta(minutes=5)),
    'monitoring_measurement_1hour': ('measurement', 'measurement', HOUR),
    'monitoring_measurement_1day': ('measurement', 'monitoring_measurement_1hour', DAY),
    'monitoring_light_intensity_1hour': ('effective_setpoints', 'effective_setpoints', HOUR),
    'monitoring_light_intensity_1day': ('effective_setpoints', 'monitoring_light_intensity_1hour', DAY),
    'monitoring_requested_pid_1hour': ('automation_state', 'automation_state', HOUR),
    'monitoring_requested_pid_1day': ('automation_state', 'monitoring_requested_pid_1hour', DAY),
    'monitoring_relay_duration_1day': (RELAY, RELAY_HOURLY, DAY),
}

# Read original/direct view dependencies, not the rewritten user view. Keeping
# all catalog rows visible lets the traversal reject unknown descendants and
# CAGGs joining a source as a secondary relation as well as raw-ID descendants.
_CATALOG: Final = """
SELECT c.user_view_schema::text AS view_schema, c.user_view_name::text AS view_name,
       c.raw_hypertable_id, c.mat_hypertable_id, c.parent_mat_hypertable_id,
       c.materialized_only,
       v.oid::bigint AS view_oid, m.oid::bigint AS materialization_oid,
       r.oid::bigint AS raw_oid, direct.oid::bigint AS direct_oid,
       pg_get_viewdef(direct.oid, true) AS direct_definition,
       pg_get_userbyid(v.relowner) AS view_owner,
       pg_get_userbyid(m.relowner) AS materialization_owner,
       pg_has_role(current_user, v.relowner, 'USAGE') AS owns_view,
       pg_has_role(current_user, m.relowner, 'USAGE') AS owns_materialization,
       has_table_privilege(current_user, r.oid, 'SELECT') AS reads_raw,
       has_table_privilege(current_user, v.oid, 'SELECT') AS reads_view,
       has_table_privilege(current_user, m.oid, 'SELECT') AS reads_materialization,
       has_schema_privilege(current_user, r.relnamespace, 'USAGE') AS uses_raw_schema,
       has_schema_privilege(current_user, m.relnamespace, 'USAGE') AS uses_materialization_schema,
       b.bucket_width, b.bucket_fixed_width, b.bucket_origin, b.bucket_offset,
       b.bucket_timezone,
       (bf.proname = 'time_bucket' AND bf.pronargs = 2
        AND bf.proargtypes[0] = 'interval'::regtype
        AND bf.proargtypes[1] = 'timestamptz'::regtype
        AND EXISTS (SELECT 1 FROM pg_depend ed JOIN pg_extension e
                    ON e.oid = ed.refobjid
                    WHERE ed.classid = 'pg_proc'::regclass AND ed.objid = bf.oid
                      AND ed.refclassid = 'pg_extension'::regclass
                      AND ed.deptype = 'e' AND e.extname = 'timescaledb')) AS known_bucket_function,
       (SELECT count(*) = 1 AND bool_and(d.column_type = 'timestamptz'::regtype)
          FROM _timescaledb_catalog.dimension d
         WHERE d.hypertable_id = c.raw_hypertable_id
           AND d.interval_length IS NOT NULL) AS timestamptz_source,
       ARRAY(SELECT DISTINCT ref.refobjid::bigint
               FROM pg_rewrite rule JOIN pg_depend ref
                 ON ref.classid = 'pg_rewrite'::regclass AND ref.objid = rule.oid
              WHERE rule.ev_class = direct.oid
                AND ref.refclassid = 'pg_class'::regclass
                AND ref.deptype = 'n' AND ref.refobjid <> direct.oid
              ORDER BY ref.refobjid::bigint) AS references
  FROM _timescaledb_catalog.continuous_agg c
  LEFT JOIN _timescaledb_catalog.hypertable raw ON raw.id = c.raw_hypertable_id
  LEFT JOIN _timescaledb_catalog.hypertable mat ON mat.id = c.mat_hypertable_id
  LEFT JOIN pg_class v ON v.oid = to_regclass(format('%I.%I', c.user_view_schema, c.user_view_name))
  LEFT JOIN pg_class r ON r.oid = to_regclass(format('%I.%I', raw.schema_name, raw.table_name))
  LEFT JOIN pg_class m ON m.oid = to_regclass(format('%I.%I', mat.schema_name, mat.table_name))
  LEFT JOIN pg_class direct ON direct.oid = to_regclass(format('%I.%I', c.direct_view_schema, c.direct_view_name))
  LEFT JOIN _timescaledb_catalog.continuous_aggs_bucket_function b
    ON b.mat_hypertable_id = c.mat_hypertable_id
  LEFT JOIN pg_proc bf ON bf.oid = to_regprocedure(b.bucket_func)
 ORDER BY c.user_view_schema, c.user_view_name
"""

_REFRESH_PROCEDURE: Final = """
SELECT n.nspname::text AS schema_name,
       has_function_privilege(current_user, p.oid, 'EXECUTE') AS executable,
       has_schema_privilege(current_user, n.oid, 'USAGE') AS schema_usage
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  JOIN pg_depend d ON d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                  AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
  JOIN pg_extension e ON e.oid = d.refobjid AND e.extname = 'timescaledb'
 WHERE p.proname = 'refresh_continuous_aggregate' AND p.prokind = 'p'
   AND p.pronargs = 5 AND p.pronargdefaults = 2
   AND p.proargtypes[0] = 'regclass'::regtype
   AND p.proargtypes[1] = '"any"'::regtype
   AND p.proargtypes[2] = '"any"'::regtype
   AND p.proargtypes[3] = 'boolean'::regtype
   AND p.proargtypes[4] = 'jsonb'::regtype
"""

_SOURCE_CHUNK_RANGES: Final = """
SELECT c.range_start, c.range_end
  FROM timescaledb_information.chunks AS c
  JOIN _timescaledb_catalog.hypertable AS h
    ON h.schema_name = c.hypertable_schema AND h.table_name = c.hypertable_name
 WHERE h.id = $1
   AND c.range_end > $2::timestamptz
   AND c.range_start < $3::timestamptz
 ORDER BY c.range_start
"""
_FRESHNESS: Final = """
WITH c AS (
    SELECT mat_hypertable_id, raw_hypertable_id
      FROM _timescaledb_catalog.continuous_agg
     WHERE user_view_schema = $1 AND user_view_name = $2
), invalidations AS (
    SELECT l.lowest_modified_value AS lo, l.greatest_modified_value AS hi
      FROM c JOIN _timescaledb_catalog.continuous_aggs_materialization_invalidation_log l
        ON l.materialization_id = c.mat_hypertable_id
    UNION ALL
    SELECT l.lowest_modified_value, l.greatest_modified_value
      FROM c JOIN _timescaledb_catalog.continuous_aggs_hypertable_invalidation_log l
        ON l.hypertable_id = c.raw_hypertable_id
), spans AS (
    SELECT CASE WHEN lo < -210866803200000000::bigint THEN '-infinity'::timestamptz
                ELSE _timescaledb_functions.to_timestamp(lo) END AS lo,
           CASE WHEN hi < -210866803200000000::bigint THEN '-infinity'::timestamptz
                ELSE _timescaledb_functions.to_timestamp(hi) END AS hi
      FROM invalidations
), watermark AS (
    -- The empty-CAGG sentinel and far-future values are not representable
    -- in Python; compare in SQL and return only the finite clamped tail start.
    SELECT CASE WHEN _timescaledb_functions.cagg_watermark(c.mat_hypertable_id) < -210866803200000000::bigint
                THEN '-infinity'::timestamptz
                ELSE _timescaledb_functions.to_timestamp(_timescaledb_functions.cagg_watermark(c.mat_hypertable_id))
           END AS at
      FROM c
)
SELECT w.at >= $4::timestamptz AS covered,
       greatest($3::timestamptz, least($4::timestamptz, w.at)) AS tail_start,
       EXISTS (SELECT 1 FROM spans WHERE lo < $4::timestamptz AND hi >= $3::timestamptz) AS pending
  FROM watermark w
"""

_RELAY_COLUMNS: Final = (
    'bucket,location,cluster,channel,device_id,device_name,device_type,'
    'on_microseconds,off_microseconds,last_observed_at,source_watermark,active'
)
# This is the per-hour build SELECT in monitoring_rebuild_relay_overview,
# including its unknown-only lane and last-recorded-owner qualification.
_RELAY_BUILD: Final = """
INSERT INTO pg_temp.archive_relay_hour_repair
    (bucket,location,cluster,channel,device_id,device_name,device_type,
     on_microseconds,off_microseconds,last_observed_at,source_watermark,active)
SELECT $1::timestamptz, d.location,d.cluster,d.channel,d.device_id,d.device_name,d.device_type,
       d.on_us,d.off_us,owner_at.last_observed_at,$3::bigint,true
  FROM (
      SELECT s.location,s.cluster,s.channel,s.device_id,s.device_name,s.device_type,
             coalesce(sum((extract(epoch FROM s.end_at-s.start_at)*1000000)::bigint)
                      FILTER (WHERE s.state='on'),0)::bigint AS on_us,
             coalesce(sum((extract(epoch FROM s.end_at-s.start_at)*1000000)::bigint)
                      FILTER (WHERE s.state='off'),0)::bigint AS off_us
        FROM public.monitoring_relay_duration_slices($1::timestamptz,$2::timestamptz,$3::bigint) s
       GROUP BY s.location,s.cluster,s.channel,s.device_id,s.device_name,s.device_type
  ) d JOIN LATERAL (
      SELECT max(ro.observed_at) AS last_observed_at
        FROM public.relay_observation ro
       WHERE ro.observation_id <= $3::bigint AND ro.observed_at < $2::timestamptz
         AND ro.location=d.location AND ro.cluster=d.cluster AND ro.channel=d.channel
         AND ro.device_id=d.device_id AND ro.device_name=d.device_name AND ro.device_type=d.device_type
  ) owner_at ON true
 WHERE d.on_us>0 OR d.off_us>0 OR owner_at.last_observed_at >= $1::timestamptz
"""
_RELAY_UPSERT: Final = (
    f'INSERT INTO public.monitoring_relay_duration_1hour ({_RELAY_COLUMNS}) '
    f'SELECT {_RELAY_COLUMNS} FROM pg_temp.archive_relay_hour_repair '
    'ON CONFLICT (bucket,location,cluster,channel,device_id,device_name,device_type) '
    'DO UPDATE SET on_microseconds=EXCLUDED.on_microseconds,'
    'off_microseconds=EXCLUDED.off_microseconds,last_observed_at=EXCLUDED.last_observed_at,'
    'source_watermark=EXCLUDED.source_watermark,active=true'
)
_RELAY_PARITY: Final = (
    'SELECT NOT EXISTS ('
    f'(SELECT {_RELAY_COLUMNS} FROM pg_temp.archive_relay_hour_repair EXCEPT ALL '
    f'SELECT {_RELAY_COLUMNS} FROM public.monitoring_relay_duration_1hour '
    'WHERE bucket=$1::timestamptz AND active) UNION ALL '
    f'(SELECT {_RELAY_COLUMNS} FROM public.monitoring_relay_duration_1hour '
    'WHERE bucket=$1::timestamptz AND active EXCEPT ALL '
    f'SELECT {_RELAY_COLUMNS} FROM pg_temp.archive_relay_hour_repair))'
)


@dataclass(frozen=True, slots=True)
class AggregateDependency:
    source_table: str
    view_schema: str
    view_name: str
    raw_hypertable_id: int
    materialization_hypertable_id: int
    parent_materialization_id: int | None
    depth: int
    bucket_width: timedelta
    view_owner: str
    materialization_owner: str
    uses_row_constructor: bool


@dataclass(frozen=True, slots=True)
class DerivedPreflight:
    source_tables: tuple[str, ...]
    dependencies: tuple[AggregateDependency, ...]
    absent_sources: tuple[str, ...]
    refresh_procedure_schema: str | None


class DerivedHistoryMaintenance:
    def __init__(self, pool: asyncpg.Pool, heavy_work: HeavyWorkExecutor) -> None:
        self.pool = pool
        self.heavy_work = heavy_work
        self._lock = asyncio.Lock()

    async def preflight(self, source_tables: Sequence[str]) -> DerivedPreflight:
        """Call before any source DELETE; permissions/catalog failures block it."""
        sources = _sources(source_tables)
        try:
            async with self.pool.acquire() as conn:
                locked = False
                try:
                    if RELAY in sources:
                        locked = await _try_lock(conn, RELAY_LOCK)
                        if not locked:
                            raise ArchiveError('archive_derived_refresh_busy', status_code=503)
                    async with conn.transaction(isolation='repeatable_read', readonly=True):
                        return await self._inspect(conn, sources)
                finally:
                    if locked:
                        await _unlock(conn, RELAY_LOCK)
        except ArchiveError:
            raise
        except Exception as error:
            raise ArchiveError('archive_derived_preflight_blocked', detail={
                'source_tables': list(sources), 'reason': 'archive_derived_catalog_unavailable',
            }) from error

    async def _inspect(self, conn: asyncpg.Connection, sources: tuple[str, ...]) -> DerivedPreflight:
        if not sources:
            return DerivedPreflight((), (), (), None)
        superuser = await conn.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
        if superuser is not False:
            raise ArchiveError('archive_derived_preflight_blocked', detail={
                'reason': 'archive_service_must_not_be_superuser',
            })
        descriptors = load_source_catalog().by_name()
        if any(name not in descriptors for name in sources):
            raise ArchiveError('archive_derived_dependency_unknown')
        version = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname='timescaledb'")
        if not isinstance(version, str) or not version.startswith('2.28.'):
            raise ArchiveError('archive_derived_catalog_unsupported')
        relation_names = list(dict.fromkeys((*sources, *((RELAY_HOURLY,) if RELAY in sources else ()))))
        relation_rows = await conn.fetch(
            "SELECT name, to_regclass(format('%I.%I','public',name))::oid::bigint AS oid "
            "FROM unnest($1::text[]) AS requested(name)", relation_names,
        )
        relations = {r['name']: r['oid'] for r in relation_rows}
        absent = tuple(name for name in sources if relations[name] is None)
        if any(descriptors[name].core for name in absent):
            raise ArchiveError('archive_derived_source_missing', detail={'source_tables': list(absent)})
        if RELAY in sources:
            if relations[RELAY_HOURLY] is None:
                raise ArchiveError('archive_derived_relay_hourly_missing')
            await self._relay_permissions(conn)
        rows = await conn.fetch(_CATALOG)
        by_name = {(r['view_schema'], r['view_name']): r for r in rows}
        dependencies: list[AggregateDependency] = []
        for source in sources:
            if source in absent:
                continue
            frontier = [(relations[source], 0)]
            if source == RELAY:
                frontier.append((relations[RELAY_HOURLY], 1))
            seen_relations: set[int] = set()
            seen_aggregates: set[int] = set()
            index = 0
            while index < len(frontier):
                relation_oid, depth = frontier[index]
                index += 1
                if relation_oid in seen_relations:
                    continue
                seen_relations.add(relation_oid)
                for row in rows:
                    if row['raw_oid'] != relation_oid and relation_oid not in row['references']:
                        continue
                    materialization_id = row['mat_hypertable_id']
                    if materialization_id in seen_aggregates:
                        continue
                    dependency = await self._dependency(conn, row, source, depth + 1, relations, by_name)
                    dependencies.append(dependency)
                    seen_aggregates.add(materialization_id)
                    frontier.append((row['view_oid'], depth + 1))
                    frontier.append((row['materialization_oid'], depth + 1))
            required = {name for name, (root, _, _) in KNOWN.items() if root == source}
            found = {d.view_name for d in dependencies if d.source_table == source}
            if required - found:
                raise ArchiveError('archive_derived_dependency_missing', detail={
                    'source_table': source, 'aggregates': sorted(required - found),
                })
        schema = None
        if dependencies:
            procedures = await conn.fetch(_REFRESH_PROCEDURE)
            if (len(procedures) != 1 or not procedures[0]['executable']
                    or not procedures[0]['schema_usage'] or not NAME.fullmatch(procedures[0]['schema_name'])):
                raise ArchiveError('archive_derived_refresh_permission_missing')
            schema = procedures[0]['schema_name']
        if any(d.view_name.endswith(('_1hour', '_1day')) and d.view_name.startswith('monitoring_')
               for d in dependencies):
            # Never seed or advance these verified-contiguous coverage rows.
            await conn.fetch(
                'SELECT model_name,covered_from,covered_through '
                'FROM public.monitoring_overview_coverage WHERE model_name=ANY($1::text[])',
                [d.view_name for d in dependencies],
            )
        return DerivedPreflight(sources, tuple(sorted(
            dependencies, key=lambda d: (d.source_table, d.depth, d.view_name),
        )), absent, schema)

    async def _dependency(
        self, conn: asyncpg.Connection, row: asyncpg.Record, source: str, depth: int,
        relations: dict[str, int | None], by_name: dict[tuple[str, str], asyncpg.Record],
    ) -> AggregateDependency:
        name = row['view_name']
        spec = KNOWN.get(name) if row['view_schema'] == 'public' else None
        if spec is None or spec[0] != source:
            raise ArchiveError('archive_derived_dependency_unknown', detail={
                'source_table': source, 'aggregate': f"{row['view_schema']}.{name}",
            })
        _, parent_name, width = spec
        if parent_name in KNOWN:
            parent = by_name.get(('public', parent_name))
            if parent is None:
                raise ArchiveError('archive_derived_dependency_missing')
            raw_oid = parent['materialization_oid']
            parent_id = parent['mat_hypertable_id']
            allowed_references = {raw_oid, parent['view_oid']}
        else:
            raw_oid = relations.get(parent_name)
            parent_id = None
            allowed_references = {raw_oid}
        if (raw_oid is None or row['raw_oid'] != raw_oid
                or row['parent_mat_hypertable_id'] != parent_id
                or row['view_oid'] is None or row['materialization_oid'] is None
                or row['direct_oid'] is None or not row['direct_definition']
                or not row['references'] or not set(row['references']) <= allowed_references
                or not row['timestamptz_source']
                or (name.startswith('monitoring_') and not row['materialized_only'])):
            raise ArchiveError('archive_derived_hierarchy_drift', detail={'aggregate': name})
        if (not row['known_bucket_function'] or row['bucket_fixed_width'] is not True
                or row['bucket_width'] is None or any(row[k] is not None for k in (
                    'bucket_origin', 'bucket_offset', 'bucket_timezone'))):
            raise ArchiveError('archive_derived_bucket_shape_unknown', detail={'aggregate': name})
        actual_width = await conn.fetchval('SELECT $1::text::interval', row['bucket_width'])
        if actual_width != width:
            raise ArchiveError('archive_derived_bucket_width_drift', detail={'aggregate': name})
        if not row['owns_view'] or not row['owns_materialization']:
            raise ArchiveError('archive_derived_owner_required', detail={
                'aggregate': name, 'view_owner': row['view_owner'],
                'materialization_owner': row['materialization_owner'],
            })
        if not all(row[k] for k in ('reads_raw', 'reads_view', 'reads_materialization',
                                   'uses_raw_schema', 'uses_materialization_schema')):
            raise ArchiveError('archive_derived_refresh_permission_missing', detail={'aggregate': name})
        return AggregateDependency(
            source, row['view_schema'], name, row['raw_hypertable_id'],
            row['mat_hypertable_id'], row['parent_mat_hypertable_id'], depth, width,
            row['view_owner'], row['materialization_owner'],
            bool(ROW_CONSTRUCTOR.search(row['direct_definition'])),
        )

    async def _relay_permissions(self, conn: asyncpg.Connection) -> None:
        row = await conn.fetchrow("""
            SELECT c.oid::bigint AS oid,
                   pg_has_role(current_user,c.relowner,'USAGE') AS owns_hourly,
                   has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE') AS hourly_grants,
                   has_table_privilege(current_user,'public.relay_observation','SELECT') AS source_grants,
                   has_table_privilege(current_user,'public.monitoring_relay_overview_progress','SELECT') AS progress_grants,
                   has_table_privilege(current_user,'public.monitoring_overview_coverage','SELECT') AS coverage_grants,
                   has_database_privilege(current_user,current_database(),'TEMP') AS temp_grants,
                   EXISTS (SELECT 1 FROM _timescaledb_catalog.hypertable h
                           WHERE h.schema_name='public' AND h.table_name='monitoring_relay_duration_1hour') AS hypertable
              FROM pg_class c WHERE c.oid=to_regclass('public.monitoring_relay_duration_1hour')
        """)
        if row is None or not all(row[k] for k in (
            'owns_hourly','source_grants','progress_grants','coverage_grants','temp_grants','hypertable',
        )):
            raise ArchiveError('archive_derived_relay_permission_missing')
        # has_table_privilege with a comma list means ANY, not ALL.
        for privilege in ('SELECT', 'INSERT', 'UPDATE'):
            if not await conn.fetchval(
                'SELECT has_table_privilege(current_user,$1::oid,$2::text)', row['oid'], privilege,
            ):
                raise ArchiveError('archive_derived_relay_permission_missing')
        for signature in (
            'public.monitoring_relay_duration_slices(timestamptz,timestamptz,bigint)',
        ):
            oid = await conn.fetchval('SELECT to_regprocedure($1::text)::oid', signature)
            if oid is None or not await conn.fetchval(
                "SELECT has_function_privilege(current_user,$1::oid,'EXECUTE')", oid,
            ):
                raise ArchiveError('archive_derived_relay_permission_missing')
        progress = await conn.fetchrow(
            'SELECT claimed_from,claimed_through FROM public.monitoring_relay_overview_progress WHERE id',
        )
        if progress is None or any(progress[k] is not None for k in ('claimed_from','claimed_through')):
            raise ArchiveError('archive_derived_relay_checkpoint_busy')

    async def refresh(
        self, source_table: str, room: str | None, start: datetime, end: datetime,
    ) -> None:
        """Repair committed changes; leave availability closed on every failure.

        Bounds are half-open actual changed observation instants. CAGGs are
        not room filtered. Relay replacement is also global per hour because
        channel ownership and heartbeat support can cross rooms.
        """
        sources = _sources((source_table,))
        if any(v.tzinfo is None or v.utcoffset() is None for v in (start,end)) or end <= start:
            raise ArchiveError('archive_derived_invalid_window', status_code=400)
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        _ = room
        try:
            await self._checkpoint()
            async with self._lock, self.pool.acquire() as conn:
                if not await _try_lock(conn, MAINTENANCE_LOCK):
                    raise ArchiveError('archive_derived_refresh_busy', status_code=503)
                relay_locked = False
                try:
                    if source_table == RELAY:
                        relay_locked = await _try_lock(conn, RELAY_LOCK)
                        if not relay_locked:
                            raise ArchiveError('archive_derived_refresh_busy', status_code=503)
                    async with conn.transaction(isolation='repeatable_read', readonly=True):
                        plan = await self._inspect(conn, sources)
                    if source_table in plan.absent_sources:
                        raise ArchiveError('archive_derived_source_missing')
                    lower, upper = start, end
                    if source_table == RELAY:
                        lower, upper = await self._relay_bounds(conn,start,end)
                        await self._relay_quiet(conn,lower,upper)
                        for lo, hi in _chunks(lower,upper,HOUR,maximum_span=HOUR):
                            await self._repair_relay_hour(conn,lo,hi)
                    if plan.dependencies:
                        assert plan.refresh_procedure_schema is not None
                        schema = plan.refresh_procedure_schema
                        # Timescale 2.28 refreshes may internally commit. No
                        # explicit transaction and no command bundled with CALL.
                        # Disable native batching for this already-bounded span
                        # so empty source slices cannot be skipped by batching.
                        call = (f'CALL "{schema}".refresh_continuous_aggregate('
                                '$1::regclass,$2::timestamptz,$3::timestamptz,true,'
                                '\'{"buckets_per_batch":0}\'::jsonb)')
                        for dependency in plan.dependencies:
                            for lo, hi in _chunks(lower,upper,dependency.bucket_width):
                                await self._refresh_dependency_window(conn,call,dependency,lo,hi)
                        for dependency in plan.dependencies:
                            lo, hi = _aligned(lower,upper,dependency.bucket_width)
                            await self._verify(conn,dependency,lo,hi)
                    if source_table == RELAY:
                        await self._relay_quiet(conn,lower,upper)
                finally:
                    if relay_locked:
                        await _unlock(conn,RELAY_LOCK)
                    await _unlock(conn,MAINTENANCE_LOCK)
        except ArchiveError:
            raise
        except asyncio.CancelledError:
            raise
        except HeavyWorkCancelled as error:
            raise ArchiveError('archive_job_cancelled',status_code=503) from error
        except Exception as error:
            raise ArchiveError('archive_derived_refresh_failed',status_code=503,detail={
                'source_table': source_table,
            }) from error

    async def _relay_bounds(
        self,conn: asyncpg.Connection,start: datetime,end: datetime,
    ) -> tuple[datetime,datetime]:
        # A recorded baseline may survive indefinitely while supported by
        # heartbeats. +/-60 seconds is NOT a safe influence bound. The API
        # supplies no channel list, so conservatively follow all 16 channels
        # to their first retained successor, capped at existing derived
        # history. Channels without a successor must repair to that horizon.
        horizon = await conn.fetchval("""
            SELECT greatest($1::timestamptz,
                (SELECT bucket+interval '1 hour' FROM public.monitoring_relay_duration_1hour
                  ORDER BY bucket DESC LIMIT 1),
                (SELECT max(covered_through) FROM public.monitoring_overview_coverage
                  WHERE model_name IN ('monitoring_relay_duration_1hour','monitoring_relay_duration_1day')))
        """,end)
        if not isinstance(horizon,datetime) or horizon.year == 9999:
            raise ArchiveError('archive_derived_relay_horizon_unknown')
        through = await conn.fetchval("""
            SELECT greatest($1::timestamptz, max(least($2::timestamptz,coalesce(next_change.observed_at,$2::timestamptz))))
              FROM generate_series(0,15) channel(number)
              LEFT JOIN LATERAL (
                  SELECT observed_at FROM public.relay_observation o
                   WHERE o.channel=channel.number AND o.observed_at >= $1::timestamptz
                   ORDER BY observed_at,observation_id LIMIT 1
              ) next_change ON true
        """,end,horizon)
        return _aligned(start,through,HOUR)

    async def _repair_relay_hour(self,conn: asyncpg.Connection,start: datetime,end: datetime) -> None:
        await self._checkpoint()
        async with conn.transaction(isolation='repeatable_read'):
            # The global rebuild's advisory lock is held, but raw writers
            # are not blocked. Slice and owner queries share this snapshot;
            # later overlapping writes remain dirty and block success below.
            watermark = await conn.fetchval(
                'SELECT coalesce(max(observation_id),0)::bigint FROM public.relay_observation')
            await conn.execute(
                'CREATE TEMP TABLE archive_relay_hour_repair '
                '(LIKE public.monitoring_relay_duration_1hour INCLUDING DEFAULTS) ON COMMIT DROP')
            await conn.execute(_RELAY_BUILD,start,end,watermark)
            await conn.execute(
                'UPDATE public.monitoring_relay_duration_1hour SET active=false WHERE bucket=$1',start)
            await conn.execute(_RELAY_UPSERT)
            if not await conn.fetchval(_RELAY_PARITY,start):
                raise ArchiveError('archive_derived_relay_hourly_unverified',status_code=503)
        await self._checkpoint()
        await self._relay_quiet(conn,start,end)

    async def _relay_quiet(self,conn: asyncpg.Connection,start: datetime,end: datetime) -> None:
        row = await conn.fetchrow(
            'SELECT claimed_from,claimed_through,dirty_from,dirty_through '
            'FROM public.monitoring_relay_overview_progress WHERE id')
        if row is None:
            raise ArchiveError('archive_derived_relay_progress_missing')
        if row['claimed_from'] is not None or row['claimed_through'] is not None:
            raise ArchiveError('archive_derived_relay_checkpoint_busy')
        if (row['dirty_from'] is not None and row['dirty_through'] is not None
                and row['dirty_from'] < end and row['dirty_through'] > start):
            # Do not clear a global dirty interval with a bounded repair.
            # The original supervised workflow owns that progress evidence.
            raise ArchiveError('archive_derived_relay_window_dirty')

    async def _refresh_dependency_window(
        self,conn: asyncpg.Connection,call: str,dependency: AggregateDependency,
        start: datetime,end: datetime,
    ) -> None:
        # Isolate anonymous ROW() aggregates to one raw source chunk per CALL.
        if not dependency.uses_row_constructor:
            await self._refresh_chunk(conn,call,dependency,start,end)
            return
        source_chunks = await conn.fetch(
            _SOURCE_CHUNK_RANGES,dependency.raw_hypertable_id,start,end,
        )
        # Refresh chunk-free gaps separately so stale materialized buckets can be cleared.
        cursor = start
        for source_chunk in source_chunks:
            chunk_start = max(start,source_chunk['range_start'])
            chunk_end = min(end,source_chunk['range_end'])
            if chunk_start > cursor:
                await self._refresh_chunk(conn,call,dependency,cursor,chunk_start)
            if chunk_end > chunk_start:
                await self._refresh_chunk(conn,call,dependency,chunk_start,chunk_end)
                cursor = max(cursor,chunk_end)
        if cursor < end:
            await self._refresh_chunk(conn,call,dependency,cursor,end)

    async def _refresh_chunk(
        self,conn: asyncpg.Connection,call: str,dependency: AggregateDependency,
        start: datetime,end: datetime,
    ) -> None:
        await self._checkpoint()
        try:
            await conn.execute(call,f'{dependency.view_schema}.{dependency.view_name}',start,end)
        except asyncpg.PostgresError as error:
            if error.sqlstate != '57014' or end-start <= dependency.bucket_width:
                raise
            count = _micros(end-start)//_micros(dependency.bucket_width)
            midpoint = start+dependency.bucket_width*(count//2)
            await self._refresh_chunk(conn,call,dependency,start,midpoint)
            await self._refresh_chunk(conn,call,dependency,midpoint,end)
            return
        await self._checkpoint()
        await self._verify(conn,dependency,start,end)

    async def _verify(
        self,conn: asyncpg.Connection,dependency: AggregateDependency,start: datetime,end: datetime,
    ) -> None:
        """Accept only no pending invalidation plus materialization through ``end``.

        The native watermark is the end of the last bucket holding data, so a
        quiet tail sits above it legitimately; that tail must be proven empty
        in both the aggregate and its input.
        """
        row = await conn.fetchrow(_FRESHNESS,dependency.view_schema,dependency.view_name,start,end)
        if row is not None and not row['pending']:
            # The quiet tail above the watermark must be empty in the
            # aggregate and its input (whole range when nothing materialized).
            if row['covered'] or await self._empty_bucket(conn, dependency, row['tail_start'], end):
                return
        raise ArchiveError('archive_derived_refresh_unverified',status_code=503,detail={
            'aggregate': dependency.view_name,
        })

    async def _empty_bucket(
        self, conn: asyncpg.Connection, dependency: AggregateDependency,
        start: datetime, end: datetime,
    ) -> bool:
        # A native watermark may retreat when the last materialized row is
        # removed. Prove both the stored bucket and its actual input empty.
        source, parent, _width = KNOWN[dependency.view_name]
        time_column = 'bucket' if parent != source else ('time' if source == 'measurement' else 'timestamp')
        predicate = 'TRUE'
        if dependency.view_name == 'monitoring_light_intensity_1hour':
            predicate = ('device_name IS NOT NULL AND (effective_light_intensity IS NOT NULL '
                         'OR nominal_light_intensity IS NOT NULL OR ramp_progress_light IS NOT NULL)')
        return bool(await conn.fetchval(
            f'SELECT NOT EXISTS(SELECT 1 FROM "{dependency.view_schema}"."{dependency.view_name}" '
            'WHERE bucket>=$1 AND bucket<$2) AND NOT EXISTS('
            f'SELECT 1 FROM public."{parent}" WHERE "{time_column}">=$1 AND "{time_column}"<$2 '
            f'AND ({predicate}))',
            start, end,
        ))

    async def _checkpoint(self) -> None:
        await asyncio.to_thread(self.heavy_work.control.checkpoint)


def _sources(values: Sequence[str]) -> tuple[str,...]:
    if isinstance(values,(str,bytes)) or any(not isinstance(v,str) or not NAME.fullmatch(v) for v in values):
        raise ArchiveError('archive_derived_dependency_unknown')
    return tuple(sorted(set(values)))


async def _try_lock(conn: asyncpg.Connection,key: str) -> bool:
    return bool(await conn.fetchval('SELECT pg_try_advisory_lock(hashtextextended($1::text,0))',key))


async def _unlock(conn: asyncpg.Connection,key: str) -> None:
    await conn.fetchval('SELECT pg_advisory_unlock(hashtextextended($1::text,0))',key)


def _micros(value: timedelta) -> int:
    return (value.days*86400+value.seconds)*1000000+value.microseconds


def _aligned(start: datetime,end: datetime,width: timedelta) -> tuple[datetime,datetime]:
    epoch = datetime(1970,1,1,tzinfo=UTC)
    step = _micros(width)
    lo = _micros(start.astimezone(UTC)-epoch)
    hi = _micros(end.astimezone(UTC)-epoch)
    return (epoch+timedelta(microseconds=(lo//step)*step),
            epoch+timedelta(microseconds=((hi+step-1)//step)*step))


def _chunks(
    start: datetime,end: datetime,width: timedelta,*,maximum_span: timedelta=DAY,
) -> Iterator[tuple[datetime,datetime]]:
    lower,upper = _aligned(start,end,width)
    span = width*max(1,_micros(maximum_span)//_micros(width))
    while lower < upper:
        through = min(upper,lower+span)
        yield lower,through
        lower = through
