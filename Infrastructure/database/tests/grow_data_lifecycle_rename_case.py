"""Guarded real-SQL rename proofs for the owned disposable PostgreSQL database.

This file accepts only the URL and marker produced by with_monitoring_test_db.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
INFRASTRUCTURE = HERE.parents[1]
WORKTREE = HERE.parents[2]
SERVICE_ROOT = INFRASTRUCTURE / "grow-archive-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

import asyncpg
from grow_archive_service.policy_gate import (
    SourceAgePolicyBlocked,
    SourceAgePolicyInspectionFailed,
    assert_source_age_policies_clear,
    inspect_source_age_policies,
)

PROTECTED_NAMES = ("cea_sensors", "sj_cea")
EXPECTED_AUTOMATION_INDEXES = frozenset({
    "automation_state_pkey",
    "automation_state_timestamp_idx",
    "idx_automation_state_location",
    "idx_automation_state_timestamp",
    "idx_automation_state_device",
    "monitoring_automation_state_id_idx",
})


class RenameProofFailure(AssertionError):
    """A guarded disposable rename proof failed."""


def _database_name_from_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "postgresql" or parsed.hostname != "127.0.0.1":
        raise RenameProofFailure("database URL is not a guarded loopback PostgreSQL URL")
    if parsed.username is None or parsed.password is None or parsed.port is None:
        raise RenameProofFailure("database URL does not contain the harness connection fields")
    if parsed.query or parsed.fragment or not parsed.path.startswith("/"):
        raise RenameProofFailure("database URL contains unsupported connection components")
    name = unquote(parsed.path[1:])
    lowered = name.casefold()
    if any(protected in lowered for protected in PROTECTED_NAMES):
        raise RenameProofFailure("refusing protected database-name substring")
    if not re.fullmatch(r"monitoring_test_[a-z0-9_]+", lowered):
        raise RenameProofFailure("database name is outside monitoring_test_ namespace")
    if not 1 <= parsed.port <= 65535:
        raise RenameProofFailure("database port is outside the valid range")
    return name


def _require_harness_marker(url: str) -> str:
    if os.environ.get("MONITORING_HARNESS_INTERNAL") != "1":
        raise RenameProofFailure("monitoring disposable-harness marker is missing")
    if os.environ.get("MONITORING_TEST_DATABASE_URL") != url:
        raise RenameProofFailure("URL does not exactly match the harness-owned database URL")
    return _database_name_from_url(url)


async def _marker(conn: asyncpg.Connection) -> None:
    database_marker = await conn.fetchrow(
        "SELECT fixture_name, fixture_version "
        "FROM public.grow_data_lifecycle_fixture_metadata "
        "WHERE fixture_name = 'grow-data-lifecycle'"
    )
    if database_marker is None or int(database_marker["fixture_version"]) != 1:
        raise RenameProofFailure("owned grow lifecycle fixture marker is missing")

def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _validate_target_name(target_name: str) -> str:
    lowered = target_name.casefold()
    if (
        not re.fullmatch(r"monitoring_test_[a-z0-9_]+", lowered)
        or len(target_name) > 63
        or any(protected in lowered for protected in PROTECTED_NAMES)
    ):
        raise RenameProofFailure("rename target is outside the guarded disposable namespace")
    return target_name


async def _owner_admin_connection(url: str) -> asyncpg.Connection:
    parsed = urlsplit(url)
    return await asyncpg.connect(
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        host=parsed.hostname,
        port=parsed.port,
        database="template1",
        timeout=8,
    )


async def _owner_role_state(
    conn: asyncpg.Connection, database_name: str
) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT current_user AS role_name, r.rolcreatedb AS createdb, "
        "r.rolsuper AS superuser, pg_get_userbyid(d.datdba) AS database_owner "
        "FROM pg_roles r CROSS JOIN pg_database d "
        "WHERE r.rolname = current_user AND d.datname = $1",
        database_name,
    )
    if row is None or row["database_owner"] != row["role_name"]:
        raise RenameProofFailure("connected disposable role does not own the target database")
    if bool(row["superuser"]):
        raise RenameProofFailure("rename proof role must not be a superuser")
    return row


async def _database_setting_probe(url: str) -> str:
    database_name = _require_harness_marker(url)
    conn = await asyncpg.connect(url, timeout=8)
    try:
        if await conn.fetchval("SELECT current_database()") != database_name:
            raise RenameProofFailure("database setting probe connected to the wrong database")
        await _marker(conn)
        await conn.execute(
            f"ALTER DATABASE {_quote_identifier(database_name)} "
            "SET statement_timeout TO '37s'"
        )
        stored = await conn.fetchval(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_db_role_setting s "
            "CROSS JOIN LATERAL unnest(s.setconfig) AS setting "
            "WHERE s.setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "AND setting = 'statement_timeout=37s')"
        )
        if not stored:
            raise RenameProofFailure("database-level statement_timeout was not persisted")
        return "database_setting=statement_timeout:37s"
    finally:
        await conn.close()


async def _owner_role_state_probe(url: str) -> str:
    database_name = _require_harness_marker(url)
    conn = await _owner_admin_connection(url)
    try:
        row = await _owner_role_state(conn, database_name)
        return (
            f"owner_role_state createdb={str(bool(row['createdb'])).lower()} "
            f"superuser={str(bool(row['superuser'])).lower()}"
        )
    finally:
        await conn.close()


async def _owner_rename(url: str, target_name: str) -> str:
    source_name = _require_harness_marker(url)
    target_name = _validate_target_name(target_name)
    if target_name == source_name:
        raise RenameProofFailure("rename source and target names are identical")
    conn = await _owner_admin_connection(url)
    try:
        role = await _owner_role_state(conn, source_name)
        source_state = await conn.fetchrow(
            "SELECT datallowconn FROM pg_database WHERE datname = $1",
            source_name,
        )
        source_exists = source_state is not None
        target_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)",
            target_name,
        )
        open_sessions = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = $1",
            source_name,
        )
        if not source_exists or target_exists:
            raise RenameProofFailure("owner rename source/target database state is unsafe")
        if bool(source_state["datallowconn"]):
            raise RenameProofFailure("owner rename requires new source-database connections to be disabled")
        if int(open_sessions) != 0:
            session_rows = await conn.fetch(
                "SELECT datname, usename, application_name, backend_type, state "
                "FROM pg_stat_activity WHERE datname = $1 ORDER BY pid",
                source_name,
            )
            session_details = [
                {
                    "database": row["datname"],
                    "user": row["usename"],
                    "application": row["application_name"],
                    "backend": row["backend_type"],
                    "state": row["state"],
                }
                for row in session_rows
            ]
            raise RenameProofFailure(
                "owner rename requires zero sessions on the source database; "
                f"found {int(open_sessions)} backend(s): {session_details}"
            )
        try:
            await conn.execute(
                f"ALTER DATABASE {_quote_identifier(source_name)} "
                f"RENAME TO {_quote_identifier(target_name)}"
            )
        except asyncpg.PostgresError as error:
            if error.sqlstate != "42501":
                raise RenameProofFailure(
                    f"owner rename failed with unexpected SQLSTATE {error.sqlstate}"
                ) from error
            return (
                "owner_rename=denied sqlstate=42501 "
                f"createdb={str(bool(role['createdb'])).lower()} superuser=false"
            )
        return (
            "owner_rename=succeeded "
            f"createdb={str(bool(role['createdb'])).lower()} superuser=false"
        )
    finally:
        await conn.close()


async def _locked_rename_probe(url: str, target_name: str) -> str:
    source_name = _require_harness_marker(url)
    target_name = _validate_target_name(target_name)
    if target_name == source_name:
        raise RenameProofFailure("locked rename source and target names are identical")
    blocker = await asyncpg.connect(url, timeout=8)
    admin: asyncpg.Connection | None = None
    try:
        if await blocker.fetchval("SELECT current_database()") != source_name:
            raise RenameProofFailure("session blocker connected to the wrong database")
        await _marker(blocker)
        blocker_pid = blocker.get_server_pid()
        admin = await _owner_admin_connection(url)
        role = await _owner_role_state(admin, source_name)
        if not bool(role["createdb"]):
            raise RenameProofFailure("blocked rename probe requires the owner role's CREATEDB privilege")
        target_exists = await admin.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)",
            target_name,
        )
        blocker_visible = await admin.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = $1 AND pid = $2)",
            source_name,
            blocker_pid,
        )
        if target_exists or not blocker_visible:
            raise RenameProofFailure("blocked rename probe did not establish its guarded session state")
        try:
            await admin.execute(
                f"ALTER DATABASE {_quote_identifier(source_name)} "
                f"RENAME TO {_quote_identifier(target_name)}"
            )
        except asyncpg.PostgresError as error:
            if error.sqlstate != "55006":
                raise RenameProofFailure(
                    f"connected-session rename failed with unexpected SQLSTATE {error.sqlstate}"
                ) from error
        else:
            await blocker.close()
            blocker = None
            await admin.execute(
                f"ALTER DATABASE {_quote_identifier(target_name)} "
                f"RENAME TO {_quote_identifier(source_name)}"
            )
            raise RenameProofFailure("rename unexpectedly succeeded while the blocker session was open")
        source_exists = await admin.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)",
            source_name,
        )
        target_exists = await admin.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)",
            target_name,
        )
        if not source_exists or target_exists:
            raise RenameProofFailure("55006 rename failure changed the database name")
        return "locked_rename=blocked sqlstate=55006 condition=object_in_use"
    finally:
        if blocker is not None:
            await blocker.close()
        if admin is not None:
            await admin.close()


def _json_object(value: object) -> dict[str, object]:
    return json.loads(value) if isinstance(value, str) else dict(value)  # type: ignore[arg-type]


EXPECTED_RENAME_HYPERTABLES = frozenset({
    "automation_state",
    "control_history",
    "effective_setpoints",
    "measurement",
    "relay_observation",
})


EXPECTED_RENAME_CAGG = ("public", "grow_rename_policy_refresh_probe")


async def _canonical_snapshot(url: str) -> dict[str, object]:
    expected_database = _require_harness_marker(url)
    conn = await asyncpg.connect(url, timeout=8)
    try:
        actual_database = await conn.fetchval("SELECT current_database()")
        if actual_database != expected_database:
            raise RenameProofFailure("reconnected to a different database name than the URL requested")
        database_owner = dict(await conn.fetchrow(
            "SELECT pg_get_userbyid(datdba) AS owner, datacl::text AS acl, "
            "datallowconn AS allow_connections "
            "FROM pg_database WHERE datname = current_database()"
        ))
        database_settings = [dict(row) for row in await conn.fetch(
            "SELECT r.rolname AS role_name, s.setconfig "
            "FROM pg_db_role_setting s LEFT JOIN pg_roles r ON r.oid=s.setrole "
            "WHERE s.setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "ORDER BY r.rolname NULLS FIRST, s.setconfig::text"
        )]
        schemas = [dict(row) for row in await conn.fetch(
            "SELECT nspname AS schema, pg_get_userbyid(nspowner) AS owner, nspacl::text AS acl "
            "FROM pg_namespace "
            "WHERE nspname NOT IN ('pg_catalog','information_schema','timescaledb_information','timescaledb_experimental') "
            "AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE '_timescaledb%' "
            "ORDER BY nspname"
        )]
        extensions = [dict(row) for row in await conn.fetch(
            "SELECT e.extname AS name, e.extversion AS version, "
            "pg_get_userbyid(e.extowner) AS owner, n.nspname AS schema "
            "FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY e.extname"
        )]
        if not any(extension["name"] == "timescaledb" for extension in extensions):
            raise RenameProofFailure("TimescaleDB extension is missing from the disposable proof")

        hypertable_rows = await conn.fetch(
            "SELECT to_jsonb(h) AS metadata, c.reloptions AS relation_options "
            "FROM timescaledb_information.hypertables h "
            "LEFT JOIN pg_namespace n ON n.nspname = h.hypertable_schema "
            "LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = h.hypertable_name "
            "ORDER BY h.hypertable_schema, h.hypertable_name"
        )
        hypertables: list[dict[str, object]] = []
        compression_settings: list[dict[str, object]] = []
        compressed_hypertables: set[str] = set()
        for row in hypertable_rows:
            metadata = _json_object(row["metadata"])
            relation_options = row["relation_options"] or []
            hypertables.append(metadata)
            compression_settings.append({
                "hypertable_schema": metadata.get("hypertable_schema"),
                "hypertable_name": metadata.get("hypertable_name"),
                "compression_enabled": metadata.get("compression_enabled"),
                "relation_options": relation_options,
            })
            if bool(metadata.get("compression_enabled")) or any(
                str(option).partition("=")[0] == "timescaledb.compress"
                for option in relation_options
            ):
                compressed_hypertables.add(str(metadata.get("hypertable_name")))
        public_hypertables = {
            str(row.get("hypertable_name"))
            for row in hypertables
            if row.get("hypertable_schema") == "public"
        }
        if not EXPECTED_RENAME_HYPERTABLES <= public_hypertables:
            raise RenameProofFailure("one or more expected source hypertables are absent")
        if not {"measurement", "automation_state"} <= compressed_hypertables:
            raise RenameProofFailure("disposable compression settings are not installed on both probe hypertables")

        cagg_rows = await conn.fetch(
            "SELECT to_jsonb(c) AS metadata "
            "FROM timescaledb_information.continuous_aggregates c "
            "ORDER BY c.view_schema, c.view_name"
        )
        continuous_aggregates: list[dict[str, object]] = []
        continuous_aggregate_row_counts: list[dict[str, object]] = []
        for row in cagg_rows:
            metadata = _json_object(row["metadata"])
            view_schema = str(metadata["view_schema"])
            view_name = str(metadata["view_name"])
            schema_name = view_schema.replace('"', '""')
            relation_name = view_name.replace('"', '""')
            row_count = await conn.fetchval(
                f'SELECT count(*)::bigint FROM "{schema_name}"."{relation_name}"'
            )
            continuous_aggregates.append(metadata)
            continuous_aggregate_row_counts.append({
                "view_schema": view_schema,
                "view_name": view_name,
                "row_count": int(row_count),
            })
        if not any(
            (row.get("view_schema"), row.get("view_name")) == EXPECTED_RENAME_CAGG
            for row in continuous_aggregates
        ):
            raise RenameProofFailure("disposable continuous aggregate is missing from the rename proof")
        relation_rows = await conn.fetch(
            "SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS kind, "
            "pg_get_userbyid(c.relowner) AS owner, c.relacl::text AS acl, c.reloptions, "
            "CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid, true) END AS view_definition "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relkind IN ('r','p','v','m','S') "
            "AND n.nspname NOT IN ('pg_catalog','information_schema','timescaledb_information','timescaledb_experimental') "
            "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE '_timescaledb%' "
            "ORDER BY n.nspname,c.relname"
        )
        relations = [dict(row) for row in relation_rows]
        columns = [dict(row) for row in await conn.fetch(
            "SELECT n.nspname AS schema, c.relname AS relation, a.attnum, a.attname AS name, "
            "format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull AS not_null, "
            "a.attidentity::text AS identity, a.attgenerated::text AS generated, "
            "pg_get_expr(d.adbin,d.adrelid) AS default_expr "
            "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
            "WHERE a.attnum > 0 AND NOT a.attisdropped "
            "AND c.relkind IN ('r','p','v','m','S') "
            "AND n.nspname NOT IN ('pg_catalog','information_schema','timescaledb_information','timescaledb_experimental') "
            "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE '_timescaledb%' "
            "ORDER BY n.nspname,c.relname,a.attnum"
        )]
        constraints = [dict(row) for row in await conn.fetch(
            "SELECT conrelid::regclass::text AS relation, conname AS name, contype::text AS kind, "
            "pg_get_constraintdef(oid,true) AS definition "
            "FROM pg_constraint WHERE connamespace NOT IN ("
            "SELECT oid FROM pg_namespace WHERE nspname IN ('pg_catalog','information_schema') "
            "OR nspname LIKE 'pg_toast%' OR nspname LIKE '_timescaledb%') "
            "ORDER BY conrelid::regclass::text,conname"
        )]
        indexes = [dict(row) for row in await conn.fetch(
            "SELECT schemaname AS schema, tablename AS relation, indexname AS name, indexdef AS definition "
            "FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema','timescaledb_information','timescaledb_experimental') "
            "AND schemaname NOT LIKE 'pg_toast%' AND schemaname NOT LIKE '_timescaledb%' "
            "ORDER BY schemaname,tablename,indexname"
        )]
        automation_indexes = frozenset(
            row["name"] for row in indexes
            if row["schema"] == "public" and row["relation"] == "automation_state"
        )
        if automation_indexes != EXPECTED_AUTOMATION_INDEXES:
            missing = EXPECTED_AUTOMATION_INDEXES - automation_indexes
            extra = automation_indexes - EXPECTED_AUTOMATION_INDEXES
            raise RenameProofFailure(
                f"automation_state index set drifted: missing={sorted(missing)}, extra={sorted(extra)}"
            )

        table_rows: list[dict[str, object]] = []
        for relation in relation_rows:
            if relation["kind"] not in ("r", "p", "m"):
                continue
            schema_name = str(relation["schema"]).replace('"', '""')
            relation_name = str(relation["name"]).replace('"', '""')
            query = (
                'SELECT count(*)::bigint AS row_count, '
                "COALESCE(md5(string_agg(encode(record_send(t),'hex'), chr(10) "
                "ORDER BY encode(record_send(t),'hex'))), md5('')) AS row_digest "
                f'FROM "{schema_name}"."{relation_name}" AS t'
            )
            row = await conn.fetchrow(query)
            table_rows.append({
                "schema": relation["schema"], "relation": relation["name"],
                "row_count": int(row["row_count"]), "row_digest": str(row["row_digest"]),
            })

        job_rows = await conn.fetch(
            "SELECT to_jsonb(j) AS job FROM timescaledb_information.jobs AS j ORDER BY j.job_id"
        )
        jobs: list[dict[str, object]] = []
        for row in job_rows:
            raw = row["job"]
            job = json.loads(raw) if isinstance(raw, str) else dict(raw)
            for volatile in ("next_start", "last_run_started_at", "last_successful_finish", "last_run_status", "last_run_duration"):
                job.pop(volatile, None)
            jobs.append(job)
        source_retention = [
            job for job in jobs
            if job.get("proc_name") == "policy_retention"
            and job.get("hypertable_schema") == "public"
            and job.get("hypertable_name") == "measurement"
        ]
        compliant_source_compression = [
            job for job in jobs
            if job.get("proc_name") == "policy_compression"
            and job.get("hypertable_schema") == "public"
            and job.get("hypertable_name") == "measurement"
        ]
        short_source_compression = [
            job for job in jobs
            if job.get("proc_name") == "policy_compression"
            and job.get("hypertable_schema") == "public"
            and job.get("hypertable_name") == "automation_state"
        ]
        refresh_jobs = [
            job for job in jobs
            if job.get("proc_name") == "policy_refresh_continuous_aggregate"
        ]
        if len(source_retention) != 1 or source_retention[0].get("scheduled") is not False:
            raise RenameProofFailure("paused disposable source policy job is absent or drifted")
        if (
            len(compliant_source_compression) != 1
            or compliant_source_compression[0].get("scheduled") is not True
        ):
            raise RenameProofFailure("compliant source compression policy did not remain enabled")
        if (
            len(short_source_compression) != 1
            or short_source_compression[0].get("scheduled") is not False
        ):
            raise RenameProofFailure("short-interval source compression policy was not paused")
        if len(refresh_jobs) != 1 or refresh_jobs[0].get("scheduled") is not True:
            raise RenameProofFailure("unrelated continuous-aggregate refresh job did not remain scheduled")
        job_owners = [
            job.get("owner") or job.get("owner_name")
            for job in (
                source_retention[0],
                compliant_source_compression[0],
                short_source_compression[0],
                refresh_jobs[0],
            )
        ]
        if not all(job_owners) or len(set(job_owners)) != 1:
            raise RenameProofFailure("Timescale job owner parity is not visible in the disposable proof")
        connected_role = str(await conn.fetchval("SELECT current_user"))
        if job_owners[0] != connected_role:
            raise RenameProofFailure("disposable policy jobs are not owned by the connected non-superuser role")

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
            "automation_state_indexes": sorted(automation_indexes),
            "table_rows_record_send_md5": table_rows,
            "timescale_jobs": jobs,
            "fixture_marker": {"name": "grow-data-lifecycle", "version": 1},
            "connected_role": connected_role,
        }
    finally:
        await conn.close()


async def _policy_probe(url: str) -> None:
    _require_harness_marker(url)
    conn = await asyncpg.connect(url, timeout=8)
    try:
        await _marker(conn)
        initial = await inspect_source_age_policies(conn)
        if not initial.ok:
            raise RenameProofFailure("disposable fixture unexpectedly begins with a scheduled source-age policy")

        await conn.execute(
            "CREATE MATERIALIZED VIEW public.grow_rename_policy_refresh_probe "
            "WITH (timescaledb.continuous) AS "
            "SELECT time_bucket(INTERVAL '1 day', time) AS bucket, sensor_id, count(*) AS sample_count "
            "FROM public.measurement GROUP BY time_bucket(INTERVAL '1 day', time), sensor_id WITH NO DATA"
        )
        await conn.execute(
            "ALTER TABLE public.measurement SET (timescaledb.compress, "
            "timescaledb.compress_segmentby = 'sensor_id', "
            "timescaledb.compress_orderby = 'time DESC')"
        )
        await conn.execute(
            "ALTER TABLE public.automation_state SET (timescaledb.compress, "
            "timescaledb.compress_segmentby = 'location', "
            "timescaledb.compress_orderby = 'timestamp DESC')"
        )
        source_job_id = await conn.fetchval(
            "SELECT add_retention_policy('public.measurement', INTERVAL '100 years', if_not_exists => TRUE)"
        )
        compliant_compression_job_id = await conn.fetchval(
            "SELECT add_compression_policy('public.measurement', INTERVAL '3 months', if_not_exists => TRUE)"
        )
        short_compression_job_id = await conn.fetchval(
            "SELECT add_compression_policy('public.automation_state', INTERVAL '2 months', if_not_exists => TRUE)"
        )
        refresh_job_id = await conn.fetchval(
            "SELECT add_continuous_aggregate_policy("
            "'public.grow_rename_policy_refresh_probe', "
            "start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 day', "
            "schedule_interval => INTERVAL '1 day', if_not_exists => TRUE)"
        )
        if any(
            job_id is None
            for job_id in (
                source_job_id,
                compliant_compression_job_id,
                short_compression_job_id,
                refresh_job_id,
            )
        ):
            raise RenameProofFailure("Timescale did not return all disposable policy job ids")

        blocked = await inspect_source_age_policies(conn)
        expected_conflicts = {
            (int(source_job_id), "public.measurement", "policy_retention"),
            (int(short_compression_job_id), "public.automation_state", "policy_compression"),
        }
        actual_conflicts = {
            (conflict.job_id, conflict.source_table, conflict.proc_name)
            for conflict in blocked.conflicts
        }
        if actual_conflicts != expected_conflicts:
            raise RenameProofFailure(
                f"source policy gate reported unexpected conflicts: {blocked.as_dict()}"
            )
        try:
            await assert_source_age_policies_clear(conn)
        except SourceAgePolicyBlocked as error:
            if error.code != "source_age_policy_conflict" or error.report != blocked:
                raise RenameProofFailure("source policy blocker did not preserve its fixed code/report") from error
        else:
            raise RenameProofFailure("assert_source_age_policies_clear accepted a scheduled source policy")

        await conn.fetchval("SELECT alter_job($1, scheduled => FALSE)", int(source_job_id))
        await conn.fetchval("SELECT alter_job($1, scheduled => FALSE)", int(short_compression_job_id))
        clear = await assert_source_age_policies_clear(conn)
        if not clear.ok:
            raise RenameProofFailure("pausing the reported source policies did not clear the activation gate")
        states = await conn.fetch(
            "SELECT job_id, scheduled FROM timescaledb_information.jobs WHERE job_id=ANY($1::integer[])",
            [
                int(source_job_id),
                int(compliant_compression_job_id),
                int(short_compression_job_id),
                int(refresh_job_id),
            ],
        )
        by_id = {int(row["job_id"]): bool(row["scheduled"]) for row in states}
        expected = {
            int(source_job_id): False,
            int(compliant_compression_job_id): True,
            int(short_compression_job_id): False,
            int(refresh_job_id): True,
        }
        if by_id != expected:
            raise RenameProofFailure("policy reconciliation changed an unrelated job or missed its source target")
    finally:
        await conn.close()



def _guard_only(url: str) -> None:
    _require_harness_marker(url)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "guard-only",
            "policy-probe",
            "database-setting-probe",
            "owner-role-state",
            "owner-rename",
            "locked-rename-probe",
            "snapshot",
        ),
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--target-name")
    args = parser.parse_args()
    try:
        if args.mode == "guard-only":
            _guard_only(args.database_url)
            return 0
        if args.mode == "policy-probe":
            asyncio.run(_policy_probe(args.database_url))
            return 0
        if args.mode == "database-setting-probe":
            print(asyncio.run(_database_setting_probe(args.database_url)))
            return 0
        if args.mode == "owner-role-state":
            print(asyncio.run(_owner_role_state_probe(args.database_url)))
            return 0
        if args.mode in ("owner-rename", "locked-rename-probe"):
            if args.target_name is None:
                parser.error(f"{args.mode} requires --target-name")
            probe = _owner_rename if args.mode == "owner-rename" else _locked_rename_probe
            print(asyncio.run(probe(args.database_url, args.target_name)))
            return 0
        snapshot = asyncio.run(_canonical_snapshot(args.database_url))
        print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, asyncpg.PostgresError, SourceAgePolicyBlocked, SourceAgePolicyInspectionFailed, RenameProofFailure, ValueError) as error:
        # Never echo a DSN, password, or server exception detail into test logs.
        print(f"grow rename proof failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
