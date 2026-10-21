"""Canonical catalog, row-digest and Timescale job snapshot of one database.

Shared by the guarded disposable proofs; callers own connection guards and
any fixture-specific assertions.
"""

from __future__ import annotations

import json

import asyncpg

VOLATILE_JOB_FIELDS = (
    "next_start",
    "last_run_started_at",
    "last_successful_finish",
    "last_run_status",
    "last_run_duration",
)

_EXCLUDED_SCHEMAS = (
    "n.nspname NOT IN ('pg_catalog','information_schema','timescaledb_information','timescaledb_experimental') "
    "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE '_timescaledb%' "
    "AND n.nspname NOT LIKE 'pg\\_temp\\_%' "
)


def json_object(value: object) -> dict[str, object]:
    return json.loads(value) if isinstance(value, str) else dict(value)


async def collect_schema_snapshot(conn: asyncpg.Connection) -> dict[str, object]:
    database_owner = dict(
        await conn.fetchrow(
            "SELECT pg_get_userbyid(datdba) AS owner, datacl::text AS acl, "
            "datallowconn AS allow_connections "
            "FROM pg_database WHERE datname = current_database()"
        )
    )
    database_settings = [
        dict(row)
        for row in await conn.fetch(
            "SELECT r.rolname AS role_name, s.setconfig "
            "FROM pg_db_role_setting s LEFT JOIN pg_roles r ON r.oid = s.setrole "
            "WHERE s.setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "ORDER BY r.rolname NULLS FIRST, s.setconfig::text"
        )
    ]
    schemas = [
        dict(row)
        for row in await conn.fetch(
            "SELECT nspname AS schema, pg_get_userbyid(nspowner) AS owner, nspacl::text AS acl "
            "FROM pg_namespace n "
            f"WHERE {_EXCLUDED_SCHEMAS}"
            "ORDER BY nspname"
        )
    ]
    extensions = [
        dict(row)
        for row in await conn.fetch(
            "SELECT e.extname AS name, e.extversion AS version, "
            "pg_get_userbyid(e.extowner) AS owner, n.nspname AS schema "
            "FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY e.extname"
        )
    ]

    hypertable_rows = await conn.fetch(
        "SELECT to_jsonb(h) AS metadata, c.reloptions AS relation_options "
        "FROM timescaledb_information.hypertables h "
        "LEFT JOIN pg_namespace n ON n.nspname = h.hypertable_schema "
        "LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = h.hypertable_name "
        "ORDER BY h.hypertable_schema, h.hypertable_name"
    )
    hypertables: list[dict[str, object]] = []
    compression_settings: list[dict[str, object]] = []
    for row in hypertable_rows:
        metadata = json_object(row["metadata"])
        hypertables.append(metadata)
        compression_settings.append(
            {
                "hypertable_schema": metadata.get("hypertable_schema"),
                "hypertable_name": metadata.get("hypertable_name"),
                "compression_enabled": metadata.get("compression_enabled"),
                "relation_options": row["relation_options"] or [],
            }
        )

    continuous_aggregates: list[dict[str, object]] = []
    continuous_aggregate_row_counts: list[dict[str, object]] = []
    for row in await conn.fetch(
        "SELECT to_jsonb(c) AS metadata "
        "FROM timescaledb_information.continuous_aggregates c "
        "ORDER BY c.view_schema, c.view_name"
    ):
        metadata = json_object(row["metadata"])
        view_schema = str(metadata["view_schema"])
        view_name = str(metadata["view_name"])
        row_count = await conn.fetchval(
            f"SELECT count(*)::bigint FROM {_quote(view_schema)}.{_quote(view_name)}"
        )
        continuous_aggregates.append(metadata)
        continuous_aggregate_row_counts.append(
            {
                "view_schema": view_schema,
                "view_name": view_name,
                "row_count": int(row_count),
            }
        )

    relation_rows = await conn.fetch(
        "SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS kind, "
        "pg_get_userbyid(c.relowner) AS owner, c.relacl::text AS acl, c.reloptions, "
        "CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid, true) END AS view_definition "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind IN ('r','p','v','m','S') "
        f"AND {_EXCLUDED_SCHEMAS}"
        "ORDER BY n.nspname,c.relname"
    )
    relations = [dict(row) for row in relation_rows]
    columns = [
        dict(row)
        for row in await conn.fetch(
            "SELECT n.nspname AS schema, c.relname AS relation, a.attnum, a.attname AS name, "
            "format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull AS not_null, "
            "a.attidentity::text AS identity, a.attgenerated::text AS generated, "
            "pg_get_expr(d.adbin,d.adrelid) AS default_expr "
            "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
            "WHERE a.attnum > 0 AND NOT a.attisdropped "
            "AND c.relkind IN ('r','p','v','m','S') "
            f"AND {_EXCLUDED_SCHEMAS}"
            "ORDER BY n.nspname,c.relname,a.attnum"
        )
    ]
    constraints = [
        dict(row)
        for row in await conn.fetch(
            "SELECT conrelid::regclass::text AS relation, conname AS name, contype::text AS kind, "
            "pg_get_constraintdef(oid,true) AS definition "
            "FROM pg_constraint WHERE connamespace NOT IN ("
            "SELECT oid FROM pg_namespace WHERE nspname IN ('pg_catalog','information_schema') "
            "OR nspname LIKE 'pg_toast%' OR nspname LIKE '_timescaledb%') "
            "ORDER BY conrelid::regclass::text,conname"
        )
    ]
    indexes = [
        dict(row)
        for row in await conn.fetch(
            "SELECT schemaname AS schema, tablename AS relation, indexname AS name, indexdef AS definition "
            "FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema','timescaledb_information','timescaledb_experimental') "
            "AND schemaname NOT LIKE 'pg_toast%' AND schemaname NOT LIKE '_timescaledb%' "
            "ORDER BY schemaname,tablename,indexname"
        )
    ]

    table_rows: list[dict[str, object]] = []
    for relation in relation_rows:
        if relation["kind"] not in ("r", "p", "m"):
            continue
        row = await conn.fetchrow(
            "SELECT count(*)::bigint AS row_count, "
            "COALESCE(md5(string_agg(encode(record_send(t),'hex'), chr(10) "
            "ORDER BY encode(record_send(t),'hex'))), md5('')) AS row_digest "
            f"FROM {_quote(str(relation['schema']))}.{_quote(str(relation['name']))} AS t"
        )
        table_rows.append(
            {
                "schema": relation["schema"],
                "relation": relation["name"],
                "row_count": int(row["row_count"]),
                "row_digest": str(row["row_digest"]),
            }
        )

    jobs: list[dict[str, object]] = []
    for row in await conn.fetch(
        "SELECT to_jsonb(j) AS job FROM timescaledb_information.jobs AS j ORDER BY j.job_id"
    ):
        job = json_object(row["job"])
        for volatile in VOLATILE_JOB_FIELDS:
            job.pop(volatile, None)
        jobs.append(job)

    return {
        "database_owner": database_owner,
        "database_settings": database_settings,
        "schemas": schemas,
        "extensions": extensions,
        "hypertables": hypertables,
        "compression_settings": compression_settings,
        "continuous_aggregates": continuous_aggregates,
        "continuous_aggregate_row_counts": continuous_aggregate_row_counts,
        "relations": relations,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "table_rows_record_send_md5": table_rows,
        "timescale_jobs": jobs,
    }


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
