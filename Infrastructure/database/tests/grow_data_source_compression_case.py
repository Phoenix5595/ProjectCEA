"""Guarded disposable proof for grow source compression policy migration."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

import asyncpg

HERE = Path(__file__).resolve().parent
COMPRESSION_SQL = HERE.parent / "grow_data_source_compression.sql"
HARNESS_LOCK = Path("/tmp/grow-lifecycle-harness.lock")
PROTECTED_NAMES = ("cea_sensors", "sj_cea")


class CompressionProofFailure(AssertionError):
    """A guarded disposable source compression proof failed."""


def _database_name(url: str) -> str:
    parsed = urlsplit(url)
    expected_host = os.environ.get("CEA_TEST_PGHOST", "127.0.0.1").lower()
    expected_port = int(os.environ.get("CEA_TEST_PGPORT", "5433"))
    if expected_host not in {"127.0.0.1", "localhost"}:
        raise CompressionProofFailure("CEA_TEST_PGHOST must be a loopback TCP host")
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname != expected_host
        or parsed.username is None
        or parsed.password is None
        or parsed.port is None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise CompressionProofFailure("database URL is not the configured loopback harness form")
    if parsed.port != expected_port:
        raise CompressionProofFailure("database port does not match CEA_TEST_PGPORT")
    name = unquote(parsed.path[1:])
    lowered = name.casefold()
    if any(protected in lowered for protected in PROTECTED_NAMES):
        raise CompressionProofFailure("refusing protected database-name substring")
    if not re.fullmatch(r"monitoring_test_[a-z0-9_]+", lowered):
        raise CompressionProofFailure("database name is outside monitoring_test_ namespace")
    return name


def _require_harness(url: str) -> str:
    if os.environ.get("GROW_HARNESS_INTERNAL") != "1":
        raise CompressionProofFailure("grow harness marker is missing")
    if os.environ.get("MONITORING_HARNESS_INTERNAL") != "1":
        raise CompressionProofFailure("monitoring disposable-harness marker is missing")
    if os.environ.get("GROW_SOURCE_COMPRESSION_CASE_LOCKED") != "1":
        raise CompressionProofFailure("source compression proof is not marked as lock-held")
    if os.environ.get("MONITORING_TEST_DATABASE_URL") != url:
        raise CompressionProofFailure("URL does not exactly match the harness-owned database URL")
    if not COMPRESSION_SQL.is_file():
        raise CompressionProofFailure(f"compression policy SQL is missing: {COMPRESSION_SQL}")
    if shutil.which("flock") is None:
        raise CompressionProofFailure("flock is required to verify the shared harness lock")
    if not HARNESS_LOCK.is_file():
        raise CompressionProofFailure("shared harness lock file does not exist")
    lock_probe = subprocess.run(
        ["flock", "-n", str(HARNESS_LOCK), "true"],
        check=False,
        capture_output=True,
        text=True,
    )
    if lock_probe.returncode != 1 or lock_probe.stderr:
        raise CompressionProofFailure("/tmp/grow-lifecycle-harness.lock is not held")
    return _database_name(url)


async def _hypertable_options(
    conn: asyncpg.Connection, table: str
) -> tuple[bool, tuple[str, ...]]:
    row = await conn.fetchrow(
        "SELECT h.compression_enabled, c.reloptions "
        "FROM timescaledb_information.hypertables h "
        "JOIN pg_namespace n ON n.nspname = h.hypertable_schema "
        "JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = h.hypertable_name "
        "WHERE h.hypertable_schema = 'public' AND h.hypertable_name = $1",
        table,
    )
    if row is None:
        raise CompressionProofFailure(f"expected public.{table} to be a hypertable")
    return bool(row["compression_enabled"]), tuple(row["reloptions"] or ())


async def _assert_jobs(
    conn: asyncpg.Connection,
    *,
    tables: tuple[str, ...],
    proc_name: str,
    interval_key: str,
    interval: str,
    expected_count: int,
) -> None:
    actual = await conn.fetchval(
        "SELECT count(*) FROM timescaledb_information.jobs "
        "WHERE hypertable_schema = 'public' "
        "AND hypertable_name = ANY($1::text[]) "
        "AND proc_name = $2 "
        "AND scheduled "
        "AND (config ->> $3)::interval = $4::text::interval",
        list(tables),
        proc_name,
        interval_key,
        interval,
    )
    if int(actual or 0) != expected_count:
        raise CompressionProofFailure(
            f"expected {expected_count} scheduled {proc_name} job(s) with "
            f"{interval_key}={interval} for {tables}; found {actual}"
        )


async def _prepare_fixture(conn: asyncpg.Connection) -> dict[str, tuple[bool, tuple[str, ...]]]:
    for table in ("measurement", "effective_setpoints", "automation_state", "relay_observation"):
        await _hypertable_options(conn, table)

    # Deliberately use valid but non-default settings: the migration must not
    # replace settings on hypertables whose compression is already enabled.
    await conn.execute(
        "ALTER TABLE public.measurement SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'sensor_id', "
        "timescaledb.compress_orderby = 'time ASC')"
    )
    await conn.execute(
        "ALTER TABLE public.effective_setpoints SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'location,cluster,device_name,mode', "
        "timescaledb.compress_orderby = 'time ASC')"
    )
    prior_settings = {
        table: await _hypertable_options(conn, table)
        for table in ("measurement", "effective_setpoints")
    }
    if not all(enabled for enabled, _ in prior_settings.values()):
        raise CompressionProofFailure("pre-existing compression settings were not enabled")

    for table in ("measurement", "effective_setpoints"):
        retention_job = await conn.fetchval(
            "SELECT add_retention_policy($1::regclass, INTERVAL '2 years', if_not_exists => TRUE)",
            f"public.{table}",
        )
        compression_job = await conn.fetchval(
            "SELECT add_compression_policy($1::regclass, INTERVAL '7 days', if_not_exists => TRUE)",
            f"public.{table}",
        )
        if retention_job is None or compression_job is None:
            raise CompressionProofFailure(f"could not seed prior policies for public.{table}")

    await _assert_jobs(
        conn,
        tables=("measurement", "effective_setpoints"),
        proc_name="policy_retention",
        interval_key="drop_after",
        interval="2 years",
        expected_count=2,
    )
    await _assert_jobs(
        conn,
        tables=("measurement", "effective_setpoints"),
        proc_name="policy_compression",
        interval_key="compress_after",
        interval="7 days",
        expected_count=2,
    )

    # Recreate control_history as a plain PostgreSQL relation solely to
    # exercise the source-compression script's skip branch; production uses a
    # hypertable.
    await conn.execute("DROP TABLE public.control_history CASCADE")
    await conn.execute("CREATE TABLE public.control_history (id BIGINT, time TIMESTAMPTZ)")
    if await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM timescaledb_information.hypertables "
        "WHERE hypertable_schema = 'public' AND hypertable_name = 'control_history')"
    ):
        raise CompressionProofFailure("public.control_history is still a hypertable in the proof fixture")
    return prior_settings


async def _run(url: str) -> None:
    database_name = _require_harness(url)
    conn = await asyncpg.connect(url, timeout=8)
    try:
        if await conn.fetchval("SELECT current_database()") != database_name:
            raise CompressionProofFailure("connection reached a different database")
        prior_settings = await _prepare_fixture(conn)
    finally:
        await conn.close()

    psql = shutil.which("psql")
    if psql is None:
        raise CompressionProofFailure("psql is required to apply the guarded SQL proof")
    completed = subprocess.run(
        [
            psql,
            "-X",
            "--set=ON_ERROR_STOP=1",
            f"--dbname={url}",
            f"--file={COMPRESSION_SQL}",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise CompressionProofFailure(
            "grow_data_source_compression.sql failed in the disposable database: "
            f"{completed.stderr.strip()}"
        )
    if "Skipping public.control_history: relation exists but is not a TimescaleDB hypertable" not in completed.stderr:
        raise CompressionProofFailure(f"missing NOTICE for plain public.control_history: {completed.stderr}")

    conn = await asyncpg.connect(url, timeout=8)
    try:
        for table, settings in prior_settings.items():
            if await _hypertable_options(conn, table) != settings:
                raise CompressionProofFailure(f"pre-existing compression settings changed for public.{table}")
        for table in ("automation_state", "relay_observation"):
            enabled, _ = await _hypertable_options(conn, table)
            if not enabled:
                raise CompressionProofFailure(f"compression was not enabled for public.{table}")

        await _assert_jobs(
            conn,
            tables=("measurement", "effective_setpoints", "automation_state", "relay_observation"),
            proc_name="policy_compression",
            interval_key="compress_after",
            interval="3 months",
            expected_count=4,
        )
        retention_count = await conn.fetchval(
            "SELECT count(*) FROM timescaledb_information.jobs "
            "WHERE hypertable_schema = 'public' "
            "AND hypertable_name IN ('measurement','effective_setpoints') "
            "AND proc_name = 'policy_retention' AND scheduled"
        )
        if int(retention_count or 0) != 0:
            raise CompressionProofFailure("pre-existing 2-year source retention policies remain scheduled")
        plain_policy_count = await conn.fetchval(
            "SELECT count(*) FROM timescaledb_information.jobs "
            "WHERE hypertable_schema = 'public' "
            "AND hypertable_name = 'control_history' "
            "AND proc_name IN ('policy_retention','policy_compression')"
        )
        if int(plain_policy_count or 0) != 0:
            raise CompressionProofFailure("policy jobs were attached to a plain source relation")
        plain_relation_count = await conn.fetchval(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND c.relname = 'control_history'"
        )
        if int(plain_relation_count or 0) != 1:
            raise CompressionProofFailure("plain control_history relation was not preserved")
    finally:
        await conn.close()

    print(
        "grow source compression proof passed: plain control_history skipped; "
        "2-year retention removed; 7-day policies replaced with 3 months; "
        "existing compression settings preserved"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.database_url))
    except (CompressionProofFailure, asyncpg.PostgresError, OSError, subprocess.TimeoutExpired) as error:
        print(f"grow source compression proof failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
