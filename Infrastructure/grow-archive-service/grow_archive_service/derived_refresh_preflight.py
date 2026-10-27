"""Read-only derived-history preflight: catalog, permission and hierarchy checks that must pass before any source DELETE."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import asyncpg

from .derived_refresh_support import (
    AggregateDependency,
    KNOWN,
    NAME,
    RELAY,
    RELAY_HOURLY,
    RELAY_LOCK,
    _sources,
    _try_lock,
    _unlock,
)
from .models import ArchiveError
from .schema import load_source_catalog


# Read original/direct view dependencies, not the rewritten user view. Keeping
# all catalog rows visible lets the traversal reject unknown descendants and
# CAGGs joining a source as a secondary relation as well as raw-ID descendants.
_CATALOG: Final = """
SELECT c.user_view_schema::text AS view_schema, c.user_view_name::text AS view_name,
       c.raw_hypertable_id, c.mat_hypertable_id, c.parent_mat_hypertable_id,
       c.materialized_only,
       v.oid::bigint AS view_oid, m.oid::bigint AS materialization_oid,
       r.oid::bigint AS raw_oid, direct.oid::bigint AS direct_oid,
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



@dataclass(frozen=True, slots=True)
class DerivedPreflight:
    source_tables: tuple[str, ...]
    dependencies: tuple[AggregateDependency, ...]
    absent_sources: tuple[str, ...]
    refresh_procedure_schema: str | None


class PreflightMixin:
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
                            raise ArchiveError("archive_derived_refresh_busy", status_code=503)
                    async with conn.transaction(isolation="repeatable_read", readonly=True):
                        return await self._inspect(conn, sources)
                finally:
                    if locked:
                        await _unlock(conn, RELAY_LOCK)
        except ArchiveError:
            raise
        except Exception as error:
            raise ArchiveError(
                "archive_derived_preflight_blocked",
                detail={
                    "source_tables": list(sources),
                    "reason": "archive_derived_catalog_unavailable",
                },
            ) from error

    async def _inspect(
        self, conn: asyncpg.Connection, sources: tuple[str, ...]
    ) -> DerivedPreflight:
        if not sources:
            return DerivedPreflight((), (), (), None)
        superuser = await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
        if superuser is not False:
            raise ArchiveError(
                "archive_derived_preflight_blocked",
                detail={
                    "reason": "archive_service_must_not_be_superuser",
                },
            )
        descriptors = load_source_catalog().by_name()
        if any(name not in descriptors for name in sources):
            raise ArchiveError("archive_derived_dependency_unknown")
        version = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname='timescaledb'"
        )
        if not isinstance(version, str) or not version.startswith("2.28."):
            raise ArchiveError("archive_derived_catalog_unsupported")
        relation_names = list(
            dict.fromkeys((*sources, *((RELAY_HOURLY,) if RELAY in sources else ())))
        )
        relation_rows = await conn.fetch(
            "SELECT name, to_regclass(format('%I.%I','public',name))::oid::bigint AS oid "
            "FROM unnest($1::text[]) AS requested(name)",
            relation_names,
        )
        relations = {r["name"]: r["oid"] for r in relation_rows}
        absent = tuple(name for name in sources if relations[name] is None)
        if any(descriptors[name].core for name in absent):
            raise ArchiveError(
                "archive_derived_source_missing", detail={"source_tables": list(absent)}
            )
        if RELAY in sources:
            if relations[RELAY_HOURLY] is None:
                raise ArchiveError("archive_derived_relay_hourly_missing")
            await self._relay_permissions(conn)
        rows = await conn.fetch(_CATALOG)
        by_name = {(r["view_schema"], r["view_name"]): r for r in rows}
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
                    if row["raw_oid"] != relation_oid and relation_oid not in row["references"]:
                        continue
                    materialization_id = row["mat_hypertable_id"]
                    if materialization_id in seen_aggregates:
                        continue
                    dependency = await self._dependency(
                        conn, row, source, depth + 1, relations, by_name
                    )
                    dependencies.append(dependency)
                    seen_aggregates.add(materialization_id)
                    frontier.append((row["view_oid"], depth + 1))
                    frontier.append((row["materialization_oid"], depth + 1))
            required = {name for name, (root, _, _) in KNOWN.items() if root == source}
            found = {d.view_name for d in dependencies if d.source_table == source}
            if required - found:
                raise ArchiveError(
                    "archive_derived_dependency_missing",
                    detail={
                        "source_table": source,
                        "aggregates": sorted(required - found),
                    },
                )
        schema = None
        if dependencies:
            procedures = await conn.fetch(_REFRESH_PROCEDURE)
            if (
                len(procedures) != 1
                or not procedures[0]["executable"]
                or not procedures[0]["schema_usage"]
                or not NAME.fullmatch(procedures[0]["schema_name"])
            ):
                raise ArchiveError("archive_derived_refresh_permission_missing")
            schema = procedures[0]["schema_name"]
        if any(
            d.view_name.endswith(("_1hour", "_1day")) and d.view_name.startswith("monitoring_")
            for d in dependencies
        ):
            # Never seed or advance these verified-contiguous coverage rows.
            await conn.fetch(
                "SELECT model_name,covered_from,covered_through "
                "FROM public.monitoring_overview_coverage WHERE model_name=ANY($1::text[])",
                [d.view_name for d in dependencies],
            )
        return DerivedPreflight(
            sources,
            tuple(
                sorted(
                    dependencies,
                    key=lambda d: (d.source_table, d.depth, d.bucket_width, d.view_name),
                )
            ),
            absent,
            schema,
        )

    async def _dependency(
        self,
        conn: asyncpg.Connection,
        row: asyncpg.Record,
        source: str,
        depth: int,
        relations: dict[str, int | None],
        by_name: dict[tuple[str, str], asyncpg.Record],
    ) -> AggregateDependency:
        name = row["view_name"]
        spec = KNOWN.get(name) if row["view_schema"] == "public" else None
        if spec is None or spec[0] != source:
            raise ArchiveError(
                "archive_derived_dependency_unknown",
                detail={
                    "source_table": source,
                    "aggregate": f"{row['view_schema']}.{name}",
                },
            )
        _, parent_name, width = spec
        if parent_name in KNOWN:
            parent = by_name.get(("public", parent_name))
            if parent is None:
                raise ArchiveError("archive_derived_dependency_missing")
            raw_oid = parent["materialization_oid"]
            parent_id = parent["mat_hypertable_id"]
            allowed_references = {raw_oid, parent["view_oid"]}
        else:
            raw_oid = relations.get(parent_name)
            parent_id = None
            allowed_references = {raw_oid}
        if (
            raw_oid is None
            or row["raw_oid"] != raw_oid
            or row["parent_mat_hypertable_id"] != parent_id
            or row["view_oid"] is None
            or row["materialization_oid"] is None
            or row["direct_oid"] is None
            or not row["references"]
            or not set(row["references"]) <= allowed_references
            or not row["timestamptz_source"]
            or (name.startswith("monitoring_") and not row["materialized_only"])
        ):
            raise ArchiveError("archive_derived_hierarchy_drift", detail={"aggregate": name})
        if (
            not row["known_bucket_function"]
            or row["bucket_fixed_width"] is not True
            or row["bucket_width"] is None
            or any(
                row[k] is not None for k in ("bucket_origin", "bucket_offset", "bucket_timezone")
            )
        ):
            raise ArchiveError("archive_derived_bucket_shape_unknown", detail={"aggregate": name})
        actual_width = await conn.fetchval("SELECT $1::text::interval", row["bucket_width"])
        if actual_width != width:
            raise ArchiveError("archive_derived_bucket_width_drift", detail={"aggregate": name})
        if not row["owns_view"] or not row["owns_materialization"]:
            raise ArchiveError(
                "archive_derived_owner_required",
                detail={
                    "aggregate": name,
                    "view_owner": row["view_owner"],
                    "materialization_owner": row["materialization_owner"],
                },
            )
        if not all(
            row[k]
            for k in (
                "reads_raw",
                "reads_view",
                "reads_materialization",
                "uses_raw_schema",
                "uses_materialization_schema",
            )
        ):
            raise ArchiveError(
                "archive_derived_refresh_permission_missing", detail={"aggregate": name}
            )
        return AggregateDependency(source, row["view_schema"], name, depth, width)

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
        if row is None or not all(
            row[k]
            for k in (
                "owns_hourly",
                "source_grants",
                "progress_grants",
                "coverage_grants",
                "temp_grants",
                "hypertable",
            )
        ):
            raise ArchiveError("archive_derived_relay_permission_missing")
        # has_table_privilege with a comma list means ANY, not ALL.
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            if not await conn.fetchval(
                "SELECT has_table_privilege(current_user,$1::oid,$2::text)",
                row["oid"],
                privilege,
            ):
                raise ArchiveError("archive_derived_relay_permission_missing")
        for signature in (
            "public.monitoring_relay_duration_slices(timestamptz,timestamptz,bigint)",
        ):
            oid = await conn.fetchval("SELECT to_regprocedure($1::text)::oid", signature)
            if oid is None or not await conn.fetchval(
                "SELECT has_function_privilege(current_user,$1::oid,'EXECUTE')",
                oid,
            ):
                raise ArchiveError("archive_derived_relay_permission_missing")
        progress = await conn.fetchrow(
            "SELECT claimed_from,claimed_through FROM public.monitoring_relay_overview_progress WHERE id",
        )
        if progress is None or any(
            progress[k] is not None for k in ("claimed_from", "claimed_through")
        ):
            raise ArchiveError("archive_derived_relay_checkpoint_busy")
