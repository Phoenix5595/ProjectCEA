"""Actual native CAGG + relay hour/day bridge derived-repair proof.

Runs only inside the guarded disposable harness (monitoring_test_* DB owned by
a non-superuser monitoring_role_*). Installs the canonical forward SQL (native
CAGGs, six materialized-only read models, six hour/day overview CAGGs,
relay slice, grow lifecycle) as the owned non-superuser, seeds bounded old
windows, materializes everything through the real refresh procedure and the
real relay rebuild procedure, runs the actual DerivedHistoryMaintenance
preflight (positive + four negative blocks BEFORE any delete), deletes only the
bounded fixture rows and records their removal in archive_availability,
performs real top-level bucket-aligned refreshes and proves: archived
contributions are gone from every dependent aggregate, retained windows are
byte-identical, relay hours hold recomputed recorded-owner durations, coverage
is never advanced, and no privilege/job changes occur. A real Redis freshness
gate (stale->pause->fresh resume) is exercised through the actual
PublicationFreshnessGuard/HeavyWorkControl pair.
No fake fallbacks: every expected failure raises; the proof aborts otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from collections import Counter
import os
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from smoke_lifecycle import (
    _assert,
    _owned_socket,
    _parse_database_url,
    _snapshot_payload,
)
import asyncpg
import redis

from grow_archive_service.derived_refresh import DerivedHistoryMaintenance
from grow_archive_service.execution import HeavyWorkControl, HeavyWorkExecutor
from grow_archive_service.freshness import PublicationFreshnessGuard
from shared.redis_keys import monitoring_current_publication_key

HERE = Path(__file__).resolve().parent
INFRASTRUCTURE = HERE.parent.parent
DATABASE = INFRASTRUCTURE / "database"

READ_MODELS_SQL = DATABASE / "monitoring_read_models.sql"
OVERVIEW_SQL = DATABASE / "monitoring_history_overview.sql"
LIFECYCLE_SQL = DATABASE / "grow_data_lifecycle.sql"
EXPIRY_CURSOR_SQL = DATABASE / "grow_data_lifecycle_expiry_cursor.sql"
ACKNOWLEDGE_FAILURES_SQL = DATABASE / "monitoring_acknowledge_failures.sql"
GATE_MIGRATION_SQL = DATABASE / "migrations/0006_monitoring_gate_auto_clear.sql"
GATE_MIGRATION_DOWN_SQL = DATABASE / "migrations/0006_monitoring_gate_auto_clear.down.sql"

# Bounded old windows (fixture CHECK allows 2020-01-01 .. now()+1day).
W_OLD_START = datetime(2026, 2, 7, 0, 0, tzinfo=UTC)  # 48h archived window
W_OLD_END = W_OLD_START + timedelta(hours=48)
W_KEEP_START = datetime(2026, 2, 10, 0, 0, tzinfo=UTC)  # 24h retained window
W_KEEP_END = W_KEEP_START + timedelta(hours=24)
W_OLD = (W_OLD_START, W_OLD_END)
W_KEEP = (W_KEEP_START, W_KEEP_END)

TORONTO = ZoneInfo("America/Toronto")
TORONTO_DAY_START = datetime(2026, 2, 7, 0, 0, tzinfo=TORONTO).astimezone(UTC)
TORONTO_DAY_END = datetime(2026, 2, 8, 0, 0, tzinfo=TORONTO).astimezone(UTC)


def _test_pg_port() -> str:
    port = os.environ.get("CEA_TEST_PGPORT", "5433")
    _assert(port.isdecimal(), "CEA_TEST_PGPORT must be a decimal port")
    port_number = int(port)
    _assert(
        1 <= port_number <= 65535 and port_number != 5432,
        "CEA_TEST_PGPORT must not target production port 5432",
    )
    return str(port_number)


async def _apply_file(database_url: str, path: Path, *, disposable_marker: bool = False) -> None:
    command = ["psql", "-X", "--set=ON_ERROR_STOP=1", "--no-psqlrc"]
    if disposable_marker:
        command.extend(["--command", "SET app.grow_cycle_history_disposable = '1'"])
    command.extend(["--file", str(path), database_url])
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await process.communicate()
    _assert(process.returncode == 0, f"canonical SQL {path.name} failed:\n{out.decode()[-2000:]}")


async def _set_native_aggregate_owner(database_name: str, owner: str) -> None:
    """Transfer one guarded disposable CAGG using the harness's local admin path."""
    _assert(
        re.fullmatch(r"monitoring_test_[a-z0-9_]+", database_name) is not None,
        f"refusing admin ownership change for unexpected database {database_name!r}",
    )
    _assert(
        re.search(r"cea_sensors|sj_cea", database_name, re.IGNORECASE) is None,
        f"refusing admin ownership change for protected database {database_name!r}",
    )
    _assert(
        re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_$]*", owner) is not None,
        f"unexpected owner identifier {owner!r}",
    )
    command = [
        "sudo",
        "-u",
        "postgres",
        "psql",
        "-p",
        _test_pg_port(),
        "-X",
        "--set=ON_ERROR_STOP=1",
        "--host=/var/run/postgresql",
        f"--dbname={database_name}",
        "--command",
        f'ALTER MATERIALIZED VIEW public.monitoring_measurement_1min OWNER TO "{owner}"',
    ]
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await process.communicate()
    _assert(
        process.returncode == 0,
        f"disposable native CAGG ownership transfer failed:\n{out.decode()[-2000:]}",
    )


async def install_canonical_sql(database_url: str) -> None:
    """Canonical forward SQL as the owned non-superuser; owned files applied twice."""
    await _apply_file(database_url, READ_MODELS_SQL)
    await _apply_file(database_url, READ_MODELS_SQL)
    for _ in range(2):
        await _apply_file(database_url, OVERVIEW_SQL, disposable_marker=True)
    for path in (LIFECYCLE_SQL, EXPIRY_CURSOR_SQL):
        await _apply_file(database_url, path)
        await _apply_file(database_url, path)


async def prove_monitoring_failure_gate(database_url: str, pool: asyncpg.Pool) -> None:
    database_name = _parse_database_url(database_url)["database"]
    for path in (GATE_MIGRATION_SQL, GATE_MIGRATION_DOWN_SQL, GATE_MIGRATION_SQL):
        await _apply_file(database_url, path)
    print("monitoring gate migration proof: forward, down, and forward applied")
    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            "SELECT job_id FROM timescaledb_information.jobs "
            "WHERE proc_schema='public' "
            "AND proc_name='monitoring_refresh_overview_incremental' "
            "ORDER BY job_id LIMIT 1"
        )
    _assert(job_id is not None, "overview incremental job is missing")

    def set_job_stats(
        *,
        total_runs: int,
        total_successes: int,
        total_failures: int,
        consecutive_failures: int,
        last_run_success: bool,
    ) -> None:
        sql = f"""
            INSERT INTO _timescaledb_internal.bgw_job_stat (
                job_id, last_start, last_finish, next_start, last_successful_finish,
                last_run_success, total_runs, total_duration, total_duration_failures,
                total_successes, total_failures, total_crashes, consecutive_failures,
                consecutive_crashes, flags)
            VALUES (
                {job_id}, now() - interval '1 minute', now(),
                now() + interval '5 minutes', now(), {str(last_run_success).upper()},
                {total_runs}, interval '1 second', interval '1 second',
                {total_successes}, {total_failures}, 0, {consecutive_failures}, 0, 0)
            ON CONFLICT (job_id) DO UPDATE SET
                last_start=EXCLUDED.last_start,
                last_finish=EXCLUDED.last_finish,
                next_start=EXCLUDED.next_start,
                last_successful_finish=EXCLUDED.last_successful_finish,
                last_run_success=EXCLUDED.last_run_success,
                total_runs=EXCLUDED.total_runs,
                total_duration=EXCLUDED.total_duration,
                total_duration_failures=EXCLUDED.total_duration_failures,
                total_successes=EXCLUDED.total_successes,
                total_failures=EXCLUDED.total_failures,
                total_crashes=EXCLUDED.total_crashes,
                consecutive_failures=EXCLUDED.consecutive_failures,
                consecutive_crashes=EXCLUDED.consecutive_crashes,
                flags=EXCLUDED.flags
        """
        result = subprocess.run(
            [
                "sudo",
                "-u",
                "postgres",
                "psql",
                "-p",
                _test_pg_port(),
                "-X",
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "--host=/var/run/postgresql",
                f"--dbname={database_name}",
                "--command",
                sql,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _assert(
            result.returncode == 0,
            f"could not seed disposable TimescaleDB job stats: {result.stdout}",
        )

    set_job_stats(
        total_runs=2,
        total_successes=1,
        total_failures=1,
        consecutive_failures=0,
        last_run_success=True,
    )
    async with pool.acquire() as conn:
        after_success = await conn.fetchval(
            "SELECT public.monitoring_overview_unacknowledged_failures(config) "
            "FROM timescaledb_information.jobs WHERE job_id=$1",
            job_id,
        )
    _assert(after_success is False, "a success after one failure still tripped the gate")
    print("monitoring gate proof: one failure followed by success -> false")

    set_job_stats(
        total_runs=5,
        total_successes=1,
        total_failures=4,
        consecutive_failures=3,
        last_run_success=False,
    )
    async with pool.acquire() as conn:
        after_three_failures = await conn.fetchval(
            "SELECT public.monitoring_overview_unacknowledged_failures(config) "
            "FROM timescaledb_information.jobs WHERE job_id=$1",
            job_id,
        )
    _assert(after_three_failures is True, "three consecutive failures did not trip the gate")
    print("monitoring gate proof: three consecutive failures -> true")

    process = subprocess.run(
        [
            "psql",
            "-X",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--file",
            str(ACKNOWLEDGE_FAILURES_SQL),
            database_url,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _assert(
        process.returncode == 0,
        f"monitoring failure acknowledgement script failed: {process.stdout}",
    )
    process = subprocess.run(
        [
            "psql",
            "-X",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--file",
            str(ACKNOWLEDGE_FAILURES_SQL),
            database_url,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _assert(
        process.returncode == 0,
        f"second monitoring failure acknowledgement run failed: {process.stdout}",
    )
    async with pool.acquire() as conn:
        remaining = await conn.fetchval(
            "SELECT COALESCE(bool_or(public.monitoring_overview_unacknowledged_failures(config)), false) "
            "FROM timescaledb_information.jobs "
            "WHERE proc_schema='public' "
            "AND proc_name IN ('monitoring_refresh_overview_incremental', "
            "'monitoring_rebuild_relay_overview')"
        )
        all_scheduled = await conn.fetchval(
            "SELECT bool_and(scheduled) FROM timescaledb_information.jobs "
            "WHERE proc_schema='public' "
            "AND proc_name IN ('monitoring_refresh_overview_incremental', "
            "'monitoring_rebuild_relay_overview')"
        )
    _assert(remaining is False, "owner acknowledgement did not clear the failure gate")
    _assert(all_scheduled is True, "owner acknowledgement did not resume paused overview jobs")
    print("monitoring gate proof: acknowledgement cleared gate and was idempotent")


async def _materialize_measurement_family(
    conn: asyncpg.Connection, lo: datetime, hi: datetime
) -> None:
    call = (
        "CALL public.refresh_continuous_aggregate($1::regclass,$2::timestamptz,$3::timestamptz,true,"
        "'{\"buckets_per_batch\":0}'::jsonb)"
    )
    views = (
        "monitoring_measurement_1min",
        "monitoring_measurement_5min",
        "monitoring_measurement_1hour",
        "monitoring_measurement_1day",
        "monitoring_effective_setpoints_1min",
        "monitoring_effective_setpoints_5min",
        "monitoring_light_intensity_1hour",
        "monitoring_light_intensity_1day",
        "monitoring_automation_state_1min",
        "monitoring_automation_state_5min",
        "monitoring_requested_pid_1hour",
        "monitoring_requested_pid_1day",
    )
    for view in views:
        await conn.execute(call, f"public.{view}", lo, hi)


async def _drain_relay_rebuild(conn: asyncpg.Connection) -> None:
    """Run the real paused rebuild procedure top-level until its dirty work is consumed."""
    # Each call claims at most one UTC day; repeat while every call advances.
    # The live UTC day is rebuilt only once complete, so dirty work confined
    # to it is the drained state.
    previous = None
    while True:
        await conn.execute(
            "CALL public.monitoring_rebuild_relay_overview(NULL, "
            '\'{"enabled":true,"acknowledged_failures":{}}\'::jsonb)'
        )
        row = await conn.fetchrow(
            "SELECT dirty_from,dirty_through,claimed_from,last_error,"
            "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS live_day "
            "FROM public.monitoring_relay_overview_progress"
        )
        if row["claimed_from"] is None and (
            row["dirty_from"] is None or row["dirty_from"] >= row["live_day"]
        ):
            return
        state = (row["dirty_from"], row["dirty_through"], row["claimed_from"])
        if state == previous:
            raise AssertionError(f"relay rebuild stopped advancing: {dict(row)}")
        previous = state


async def seed_old_windows(conn: asyncpg.Connection) -> None:
    # measurement: minute rows for sensor 1 (Flower) and 5 (Veg) in both windows.
    for lo, hi in (W_OLD, W_KEEP):
        for sensor in (1, 5):
            await conn.execute(
                "INSERT INTO measurement(time,sensor_id,value,status) "
                "SELECT g + CASE WHEN $2=5 THEN interval '30 seconds' ELSE interval '0 seconds' END,"
                "$2,(extract(epoch from g)/60)::int % 7 * 0.25::real,'ok' "
                "FROM generate_series($1::timestamptz,$3::timestamptz - interval '1 minute',interval '1 minute') g",
                lo,
                sensor,
                hi,
            )
    # automation_state: minute rows Flower/Veg lanes.
    for lo, hi in (W_OLD, W_KEEP):
        for lane, device in (("Flower Room", "heater_f_1"), ("Veg Room", "fan_v_1")):
            await conn.execute(
                'INSERT INTO automation_state("time",location,cluster,device_name,device_state,device_mode,'
                "pid_output,duty_cycle_percent,control_reason) "
                "SELECT g + CASE WHEN $2='Veg Room' THEN interval '30 seconds' ELSE interval '0 seconds' END,"
                "$2,'main',$3,1,'auto',(greatest(0,least(1,extract(epoch from g)/7200)))::real,"
                "50.0::real,'fixture' FROM generate_series($1::timestamptz,$4::timestamptz - interval '1 minute',interval '1 minute') g",
                lo,
                lane,
                device,
                hi,
            )
    # effective_setpoints: light lane minute rows (drives monitoring_light_intensity_*).
    for lo, hi in (W_OLD, W_KEEP):
        for lane, device in (("Flower Room", "light_f_1"), ("Veg Room", "light_v_1")):
            await conn.execute(
                'INSERT INTO effective_setpoints("time",location,cluster,device_name,mode,effective_light_intensity,'
                "nominal_light_intensity,ramp_progress_light) "
                "SELECT g + CASE WHEN $2='Veg Room' THEN interval '30 seconds' ELSE interval '0 seconds' END,"
                "$2,'main',$3,'DAY',40.0::real,80.0::real,0.5::real "
                "FROM generate_series($1::timestamptz,$4::timestamptz - interval '1 minute',interval '1 minute') g",
                lo,
                lane,
                device,
                hi,
            )
    # relay_observation: channel 0 on/off every 10 minutes with 5-minute heartbeats.
    for lo, hi in (W_OLD, W_KEEP):
        for index, instant in enumerate(_minutes(lo, hi, 5)):
            heartbeat = index % 4 == 3
            if heartbeat:
                await conn.execute(
                    'INSERT INTO relay_observation("time",session_id,reason,registry_version) '
                    "VALUES($1,'33333333-3333-4333-8333-333333333333','heartbeat',0)",
                    instant,
                )
            else:
                on = (index // 4) % 2 == 0
                await conn.execute(
                    'INSERT INTO relay_observation("time",session_id,channel,observed_state,device_id,'
                    "device_name,device_type,location,cluster,registry_version,reason) "
                    "VALUES($1,'22222222-2222-4222-8222-222222222222',0,$2,1,'Flower Back Node','sensor-node',"
                    "'Flower Room','main',0,'state_changed')",
                    instant,
                    on,
                )


def _minutes(lo: datetime, hi: datetime, step_minutes: int):
    cursor = lo
    while cursor < hi:
        yield cursor
        cursor += timedelta(minutes=step_minutes)


async def materialize_fixtures(pool: asyncpg.Pool) -> None:
    lo, hi = W_OLD_START, W_KEEP_END
    async with pool.acquire() as conn:
        await _materialize_measurement_family(conn, lo, hi)
        await _drain_relay_rebuild(conn)
        # Daily relay CAGG over the rebuilt hours (real 5-arg CALL).
        await conn.execute(
            "CALL public.refresh_continuous_aggregate('public.monitoring_relay_duration_1day',"
            "$1::timestamptz,$2::timestamptz,true,'{\"buckets_per_batch\":0}'::jsonb)",
            lo,
            hi,
        )
        hourly = await conn.fetchval(
            "SELECT count(*) FROM public.monitoring_relay_duration_1hour WHERE bucket>=$1 AND bucket<$2",
            lo,
            hi,
        )
        daily = await conn.fetchval(
            "SELECT count(*) FROM public.monitoring_relay_duration_1day WHERE bucket>=$1 AND bucket<$2",
            lo,
            hi,
        )
        _assert(hourly > 0 and daily > 0, "relay materialization produced no rows")


async def snapshot_aggregates(
    pool: asyncpg.Pool, *, require_seeded: bool = False
) -> dict[str, list]:
    spans = {"old": W_OLD, "keep": W_KEEP}
    queries = {
        "monitoring_measurement_1min": "SELECT bucket,sensor_id,sample_count,value_sum,value_sum_squares,min_value,max_value,last_observed_at FROM public.monitoring_measurement_1min WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2",
        "monitoring_measurement_1day": "SELECT bucket,sensor_id,sample_count,value_sum,value_sum_squares,min_value,max_value,last_observed_at FROM public.monitoring_measurement_1day WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2",
        "monitoring_measurement_1hour": "SELECT bucket,sensor_id,sample_count,value_sum,value_sum_squares,min_value,max_value,last_observed_at FROM public.monitoring_measurement_1hour WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2",
        "monitoring_requested_pid_1hour": "SELECT bucket,location,cluster,device_name,sample_count,requested_tw,requested_min,requested_max,last_observed_at FROM public.monitoring_requested_pid_1hour WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2,3,4",
        "monitoring_requested_pid_1day": "SELECT bucket,location,cluster,device_name,sample_count,requested_tw,requested_min,requested_max,last_observed_at FROM public.monitoring_requested_pid_1day WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2,3,4",
        "monitoring_light_intensity_1hour": "SELECT bucket,location,cluster,device_name,sample_count,value_sum,value_sum_squares,min_value,max_value,last_observed_at FROM public.monitoring_light_intensity_1hour WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2,3,4",
        "monitoring_light_intensity_1day": "SELECT bucket,location,cluster,device_name,sample_count,value_sum,value_sum_squares,min_value,max_value,last_observed_at FROM public.monitoring_light_intensity_1day WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2,3,4",
        "relay_1hour": "SELECT bucket,location,cluster,channel,device_id,device_name,device_type,on_microseconds,off_microseconds,last_observed_at,source_watermark,active FROM public.monitoring_relay_duration_1hour WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2,3,4,5,6,7",
        "relay_1day": "SELECT bucket,location,cluster,channel,device_id,device_name,device_type,on_microseconds,off_microseconds,last_observed_at,source_watermark FROM public.monitoring_relay_duration_1day WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2,3,4,5,6,7",
    }
    from grow_archive_service.derived_refresh_support import KNOWN

    for name in KNOWN:
        queries.setdefault(name, f"SELECT * FROM public.{name} WHERE bucket>=$1 AND bucket<$2")
    snapshot: dict[str, list] = {}
    async with pool.acquire() as conn:
        for name, sql in queries.items():
            for span, (lo, hi) in spans.items():
                snapshot[f"{name}:{span}"] = [
                    tuple(row.values()) for row in await conn.fetch(sql, lo, hi)
                ]
                # Baseline must be non-vacuous; after removal old windows may
                # legitimately be empty (validate recomputes them from raw).
                if require_seeded and name in KNOWN:
                    _assert(
                        snapshot[f"{name}:{span}"],
                        f"native aggregate {name} has no seeded rows in {span} window",
                    )
    return snapshot


async def _expect_error(coro, code: str, message: str) -> None:
    from grow_archive_service.models import ArchiveError

    try:
        await coro
    except ArchiveError as error:
        _assert(error.code == code, f"{message}: expected {code}, got {error.code}")
        return
    raise AssertionError(f"{message}: no ArchiveError raised")


async def install_real_derived_schema(database_url: str) -> None:
    """Install current in-house derived schema in a guarded owned database."""
    _parse_database_url(database_url)
    _assert(os.getenv("MONITORING_HARNESS_INTERNAL") == "1", "owned disposable harness required")
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            _assert(
                await conn.fetchval(
                    "SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
                    "WHERE fixture_name='grow-data-lifecycle'"
                )
                == 1,
                "missing owned DB marker",
            )
            _assert(
                await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
                is False,
                "worker role must be a non-superuser",
            )
        await install_canonical_sql(database_url)
    finally:
        await pool.close()


async def run(database_url: str) -> None:
    _parse_database_url(database_url)
    _assert(os.getenv("MONITORING_HARNESS_INTERNAL") == "1", "owned disposable harness required")
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    heavy = HeavyWorkExecutor()
    heavy.control.allow_resume()
    dataset = uuid4()
    try:
        async with pool.acquire() as conn:
            _assert(
                await conn.fetchval(
                    "SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
                    "WHERE fixture_name='grow-data-lifecycle'"
                )
                == 1,
                "missing owned DB marker",
            )
            _assert(
                await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
                is False,
                "worker role must be a non-superuser",
            )
        await install_canonical_sql(database_url)
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT set_chunk_time_interval('public.automation_state'::regclass, interval '1 day')"
            )
            await conn.execute(
                "SELECT set_chunk_time_interval('public.effective_setpoints'::regclass, interval '7 days')"
            )
        await seed_and_materialize(pool)

        maintenance = DerivedHistoryMaintenance(pool, heavy)
        sources = ("measurement", "automation_state", "effective_setpoints", "relay_observation")

        # Preflight precedes every source mutation.
        async with pool.acquire() as conn:
            _assert(
                not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"),
                "worker role must be a non-superuser",
            )
        plan = await maintenance.preflight(sources)
        measurement_refresh_order = tuple(
            dependency.view_name
            for dependency in plan.dependencies
            if dependency.source_table == "measurement"
        )
        _assert(
            measurement_refresh_order
            == (
                "monitoring_measurement_1min",
                "monitoring_measurement_5min",
                "monitoring_measurement_1hour",
                "monitoring_measurement_1day",
            ),
            f"measurement CAGGs are not refreshed parent-first: {measurement_refresh_order}",
        )

        # ---- Negative preflights BEFORE any delete; each must fail closed.
        await negative_preflight_proofs(pool, maintenance, sources)
        # The missing-overview proof drops a parent with CASCADE; rematerialize
        # every restored native view before taking the positive-proof baseline.
        async with pool.acquire() as conn:
            await _materialize_measurement_family(conn, W_OLD_START, W_KEEP_END)
        # Recreate anything the negative proofs dropped and re-verify a clean plan.
        await maintenance.preflight(sources)
        await _prove_toronto_day_window_crosses_source_chunks(pool)
        await maintenance.refresh("automation_state", None, TORONTO_DAY_START, TORONTO_DAY_END)
        await _prove_effective_setpoint_chunk_boundary_refresh(pool, maintenance)
        before = await snapshot_aggregates(pool, require_seeded=True)
        coverage_before = await pool.fetch(
            "SELECT * FROM public.monitoring_overview_coverage ORDER BY model_name"
        )
        jobs_before = await pool.fetch(
            "SELECT id,config,scheduled FROM _timescaledb_catalog.bgw_job ORDER BY id"
        )
        owners_before = await pool.fetch(
            "SELECT c.user_view_name,pg_get_userbyid(v.relowner) AS owner "
            "FROM _timescaledb_catalog.continuous_agg c JOIN pg_class v "
            "ON v.oid=to_regclass(format('%I.%I',c.user_view_schema,c.user_view_name)) ORDER BY 1"
        )

        privileges_before = await _snapshot_privileges(pool)
        await delete_test_source_rows(pool, dataset)

        # ---- Actual repairs: one refresh per source over the changed span.
        for source, start, end in (
            ("measurement", W_OLD_START, W_OLD_END),
            ("automation_state", W_OLD_START, W_OLD_END),
            ("effective_setpoints", W_OLD_START, W_OLD_END),
            ("relay_observation", W_OLD_START, W_OLD_END),
        ):
            try:
                await maintenance.refresh(source, None, start, end)
            except Exception as error:
                code = getattr(error, "code", type(error).__name__)
                detail = getattr(error, "detail", {})
                raise AssertionError(
                    f"derived refresh failed for {source}: {code} {detail}"
                ) from error
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE archive_availability SET aggregates_refreshed=true,"
                "aggregates_refreshed_at=clock_timestamp() "
                "WHERE dataset_id=$1 AND source_rows_removed AND NOT aggregates_refreshed",
                dataset,
            )

        await validate(pool, before)
        await validate_static_state(
            pool, coverage_before, jobs_before, owners_before, privileges_before
        )
        await freshness_proof(database_url)
        await prove_monitoring_failure_gate(database_url, pool)
        print(
            "Actual derived-repair proof passed: native preflight, four negative blocks before any delete, "
            "bounded disposable source-row removal recorded in archive_availability, "
            "Toronto-day automation_state/effective_setpoints refreshes run whole-range across source-chunk boundaries, "
            "real top-level CALL repairs, archived contributions gone from every dependent aggregate, "
            "retained windows byte-identical, relay hours recomputed with recorded-owner durations, "
            "coverage untouched, real Redis freshness pause/resume honored"
        )
    finally:
        await heavy.shutdown()
        await pool.close()


async def seed_and_materialize(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await seed_old_windows(conn)
    await materialize_fixtures(pool)


async def negative_preflight_proofs(
    pool: asyncpg.Pool, maintenance: DerivedHistoryMaintenance, sources: tuple[str, ...]
) -> None:
    # (a) unknown CAGG on the measurement root.
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE MATERIALIZED VIEW measurement_unknown_probe WITH (timescaledb.continuous) AS "
            "SELECT time_bucket(interval '1 minute', time) AS bucket, sensor_id, count(*) AS n "
            "FROM measurement GROUP BY bucket, sensor_id WITH NO DATA"
        )
    try:
        await _expect_error(
            maintenance.preflight(("measurement",)),
            "archive_derived_dependency_unknown",
            "unknown measurement CAGG must block preflight",
        )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DROP MATERIALIZED VIEW measurement_unknown_probe")
    # (b) unknown CAGG on the relay hourly bridge.
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE MATERIALIZED VIEW relay_unknown_probe WITH (timescaledb.continuous) AS "
            "SELECT time_bucket(INTERVAL '1 day', hour.bucket) AS bucket, hour.channel, "
            "sum(hour.on_microseconds) AS on_us "
            "FROM public.monitoring_relay_duration_1hour AS hour WHERE hour.active GROUP BY 1, 2 WITH NO DATA"
        )
    try:
        await _expect_error(
            maintenance.preflight(("relay_observation",)),
            "archive_derived_dependency_unknown",
            "unknown relay-bridge CAGG must block preflight",
        )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DROP MATERIALIZED VIEW relay_unknown_probe")
    # (c) non-owner native aggregate ownership blocks preflight.
    async with pool.acquire() as conn:
        database_name, worker_role = await conn.fetchrow("SELECT current_database(), current_user")
    await _set_native_aggregate_owner(database_name, "postgres")
    try:
        await _expect_error(
            maintenance.preflight(("measurement",)),
            "archive_derived_owner_required",
            "postgres-owned native CAGG must block preflight",
        )
    finally:
        await _set_native_aggregate_owner(database_name, worker_role)
    # (d) missing required aggregate.
    async with pool.acquire() as conn:
        await conn.execute("DROP MATERIALIZED VIEW monitoring_measurement_1hour CASCADE")
    try:
        await _expect_error(
            maintenance.preflight(("measurement",)),
            "archive_derived_dependency_missing",
            "missing overview CAGG must block preflight",
        )
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "CREATE MATERIALIZED VIEW monitoring_measurement_1hour "
                "WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS "
                "SELECT time_bucket(INTERVAL '1 hour', minute.bucket) AS bucket, minute.sensor_id, "
                "sum(minute.sample_count) AS sample_count, sum(minute.value_sum) AS value_sum, "
                "sum(minute.value_sum_squares) AS value_sum_squares, "
                "min(minute.min_value) AS min_value, max(minute.max_value) AS max_value, "
                "max(minute.last_observed_at) AS last_observed_at "
                "FROM monitoring_measurement_1min AS minute "
                "GROUP BY 1, minute.sensor_id WITH NO DATA"
            )
            await conn.execute(
                "CREATE MATERIALIZED VIEW monitoring_measurement_1day "
                "WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS "
                "SELECT time_bucket(INTERVAL '1 day', hourly.bucket) AS bucket, hourly.sensor_id, "
                "sum(hourly.sample_count) AS sample_count, sum(hourly.value_sum) AS value_sum, "
                "sum(hourly.value_sum_squares) AS value_sum_squares, min(hourly.min_value) AS min_value, "
                "max(hourly.max_value) AS max_value, max(hourly.last_observed_at) AS last_observed_at "
                "FROM monitoring_measurement_1hour AS hourly GROUP BY 1, hourly.sensor_id WITH NO DATA"
            )


async def _prove_toronto_day_window_crosses_source_chunks(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        chunks = await conn.fetch(
            "SELECT range_start,range_end FROM timescaledb_information.chunks "
            "WHERE hypertable_schema='public' AND hypertable_name='automation_state' "
            "AND range_start<$2 AND range_end>$1 ORDER BY range_start",
            TORONTO_DAY_START,
            TORONTO_DAY_END,
        )
    _assert(
        len(chunks) == 2 and chunks[0]["range_end"] == chunks[1]["range_start"],
        f"Toronto-day refresh window must cross one automation_state chunk boundary: {chunks}",
    )
    _assert(
        chunks[0]["range_end"] > TORONTO_DAY_START and chunks[0]["range_end"] < TORONTO_DAY_END,
        f"Toronto-day refresh boundary must be interior to the window: {chunks}",
    )


async def _prove_effective_setpoint_chunk_boundary_refresh(
    pool: asyncpg.Pool,
    maintenance: DerivedHistoryMaintenance,
) -> None:
    async with pool.acquire() as conn:
        source_chunk = await conn.fetchrow(
            "SELECT range_start,range_end FROM timescaledb_information.chunks "
            "WHERE hypertable_schema='public' AND hypertable_name='effective_setpoints' "
            "AND range_start<=$1 AND range_end>$1 ORDER BY range_start LIMIT 1",
            W_OLD_START,
        )
        _assert(source_chunk is not None, "effective_setpoints source chunk was not created")
        _assert(
            source_chunk["range_end"] - source_chunk["range_start"] == timedelta(days=7),
            f"effective_setpoints fixture chunk must be seven days: {source_chunk}",
        )
        boundary = source_chunk["range_end"]
        local_start = boundary.astimezone(TORONTO).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        local_end = local_start + timedelta(days=1)
        start, end = local_start.astimezone(UTC), local_end.astimezone(UTC)
        _assert(
            start < boundary < end,
            f"effective_setpoints chunk boundary must lie inside a Toronto day: {boundary}",
        )
        await conn.executemany(
            'INSERT INTO effective_setpoints("time",location,cluster,mode,device_name,effective_light_intensity) '
            "VALUES($1,'Flower Room','main','DAY','chunk_boundary_probe',$2)",
            [
                (boundary - timedelta(minutes=1), 45.0),
                (boundary + timedelta(minutes=1), 55.0),
            ],
        )
        chunks = await conn.fetch(
            "SELECT range_start,range_end FROM timescaledb_information.chunks "
            "WHERE hypertable_schema='public' AND hypertable_name='effective_setpoints' "
            "AND range_start<$2 AND range_end>$1 ORDER BY range_start",
            start,
            end,
        )
    _assert(
        len(chunks) == 2 and chunks[0]["range_end"] == chunks[1]["range_start"],
        f"effective_setpoints Toronto day must cross one seven-day chunk boundary: {chunks}",
    )
    await maintenance.refresh("effective_setpoints", None, start, end)
    async with pool.acquire() as conn:
        for view in ("monitoring_effective_setpoints_1min", "monitoring_effective_setpoints_5min"):
            count = await conn.fetchval(
                f"SELECT count(*) FROM public.{view} WHERE bucket>=$1 AND bucket<$2 "
                "AND device_name='chunk_boundary_probe'",
                start,
                end,
            )
            _assert(
                count == 2, f"{view} did not materialize both chunk-boundary probe rows: {count}"
            )


async def delete_test_source_rows(pool: asyncpg.Pool, dataset: UUID) -> None:
    """Delete only the bounded fixture rows and record the removed intervals."""
    streams = (
        ("measurement", "time", ("Flower Room", "Veg Room")),
        ("automation_state", "time", ("Flower Room", "Veg Room")),
        ("effective_setpoints", "time", ("Flower Room", "Veg Room")),
        ("relay_observation", "time", ("Flower Room", None)),
    )
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO archive_dataset(dataset_id,kind,calendar_year,quarter,room,calendar_revision,"
            "data_revision,published_revision,state,available_from,available_to) "
            "VALUES($1,'veg_quarter',2026,1,'Veg Room',1,1,1,'ready',$2,$3)",
            dataset,
            W_OLD_START,
            W_KEEP_END,
        )
        for stream_name, time_column, rooms in streams:
            for room in rooms:
                await conn.execute(
                    "INSERT INTO archive_availability(dataset_id,revision,source_table,room,start_at,end_at,state) "
                    "VALUES($1,1,$2,$3,$4,$5,'captured') ON CONFLICT DO NOTHING",
                    dataset,
                    stream_name,
                    room,
                    W_OLD_START,
                    W_OLD_END,
                )
            removed = await conn.fetch(
                f"DELETE FROM public.{stream_name} "
                f"WHERE {time_column}>=$1 AND {time_column}<$2 RETURNING 1",
                W_OLD_START,
                W_OLD_END,
            )
            _assert(removed, f"{stream_name} old window must contain seeded rows")
            await conn.execute(
                "UPDATE archive_availability SET source_rows_removed=true,"
                "source_removed_at=clock_timestamp() "
                "WHERE dataset_id=$1 AND source_table=$2 AND start_at=$3 AND end_at=$4",
                dataset,
                stream_name,
                W_OLD_START,
                W_OLD_END,
            )


async def validate(pool: asyncpg.Pool, before: dict[str, list]) -> None:
    after = await snapshot_aggregates(pool)
    # Retained windows: byte-identical buckets everywhere.
    for key, rows in before.items():
        if key.endswith(":keep"):
            _assert(Counter(after[key]) == Counter(rows), f"retained window changed for {key[:-5]}")
    # Archived window: every dependent aggregate equals recomputation from remaining raw.
    expectations = {
        "monitoring_measurement_1min:old": (
            "SELECT time_bucket(interval '1 minute', time) AS bucket, sensor_id, count(value), "
            "sum(value::numeric), sum(value::numeric*value::numeric), min(value::numeric), max(value::numeric), "
            "max(time) FROM public.measurement WHERE time>=$1 AND time<$2 GROUP BY 1,2 ORDER BY 1,2",
            "monitoring 1min CAGG",
        ),
        "monitoring_measurement_1hour:old": (
            "SELECT time_bucket(interval '1 hour', time) AS bucket, sensor_id, count(value), "
            "sum(value::numeric), sum(value::numeric*value::numeric), min(value::numeric), max(value::numeric), "
            "max(time) FROM public.measurement WHERE time>=$1 AND time<$2 GROUP BY 1,2 ORDER BY 1,2",
            "monitoring hourly CAGG",
        ),
        "monitoring_measurement_1day:old": (
            "SELECT time_bucket(interval '1 day', time) AS bucket, sensor_id, count(value), "
            "sum(value::numeric), sum(value::numeric*value::numeric), min(value::numeric), max(value::numeric), "
            "max(time) FROM public.measurement WHERE time>=$1 AND time<$2 GROUP BY 1,2 ORDER BY 1,2",
            "monitoring daily CAGG",
        ),
        "monitoring_requested_pid_1hour:old": (
            "SELECT time_bucket(interval '1 hour', \"time\") AS bucket, location, cluster, device_name, "
            "count(*), count(pid_output) FILTER (WHERE pid_output::double precision>=0 AND pid_output::double precision<=1), "
            "sum((pid_output::double precision*100.0)::numeric) FILTER (WHERE pid_output::double precision>=0 "
            "AND pid_output::double precision<=1), "
            "sum((pid_output::double precision*100.0)::numeric*(pid_output::double precision*100.0)::numeric) "
            "FILTER (WHERE pid_output::double precision>=0 AND pid_output::double precision<=1), "
            "min((pid_output::double precision*100.0)::numeric) FILTER (WHERE pid_output::double precision>=0 "
            "AND pid_output::double precision<=1), "
            "max((pid_output::double precision*100.0)::numeric) FILTER (WHERE pid_output::double precision>=0 "
            'AND pid_output::double precision<=1), max("time") '
            'FROM public.automation_state WHERE "time">=$1 AND "time"<$2 '
            "GROUP BY 1,2,3,4 ORDER BY 1,2,3,4",
            "requested pid hourly",
        ),
        "monitoring_requested_pid_1day:old": (
            "SELECT time_bucket(interval '1 day', \"time\") AS bucket, location, cluster, device_name, "
            "count(*), count(pid_output) FILTER (WHERE pid_output::double precision>=0 AND pid_output::double precision<=1), "
            "sum((pid_output::double precision*100.0)::numeric) FILTER (WHERE pid_output::double precision>=0 "
            "AND pid_output::double precision<=1), "
            "sum((pid_output::double precision*100.0)::numeric*(pid_output::double precision*100.0)::numeric) "
            "FILTER (WHERE pid_output::double precision>=0 AND pid_output::double precision<=1), "
            "min((pid_output::double precision*100.0)::numeric) FILTER (WHERE pid_output::double precision>=0 "
            "AND pid_output::double precision<=1), "
            "max((pid_output::double precision*100.0)::numeric) FILTER (WHERE pid_output::double precision>=0 "
            'AND pid_output::double precision<=1), max("time") '
            'FROM public.automation_state WHERE "time">=$1 AND "time"<$2 '
            "GROUP BY 1,2,3,4 ORDER BY 1,2,3,4",
            "requested pid daily",
        ),
        "monitoring_light_intensity_1hour:old": (
            "SELECT time_bucket(interval '1 hour', \"time\") AS bucket, location, cluster, device_name, "
            "count(effective_light_intensity), sum(effective_light_intensity::numeric), "
            "sum(effective_light_intensity::numeric*effective_light_intensity::numeric), "
            'min(effective_light_intensity::numeric), max(effective_light_intensity::numeric), max("time") '
            'FROM public.effective_setpoints WHERE "time">=$1 AND "time"<$2 AND device_name IS NOT NULL '
            "AND (effective_light_intensity IS NOT NULL OR nominal_light_intensity IS NOT NULL "
            "OR ramp_progress_light IS NOT NULL) GROUP BY 1,2,3,4 ORDER BY 1,2,3,4",
            "light hourly",
        ),
        "relay_1hour:old": (
            "SELECT * FROM public.monitoring_relay_duration_1hour WHERE bucket>=$1 AND bucket<$2 "
            "ORDER BY 1,2,3,4,5,6,7",
            "relay hourly self-consistency",
        ),
    }
    expectations["monitoring_measurement_5min:old"] = (
        "SELECT time_bucket(interval '5 minutes', time) AS bucket, sensor_id, count(value), "
        "sum(value::numeric), sum(value::numeric*value::numeric), min(value::numeric), max(value::numeric), "
        "max(time) FROM public.measurement WHERE time>=$1 AND time<$2 "
        "GROUP BY 1,2 ORDER BY 1,2",
        "measurement 5min CAGG",
    )
    for width, suffix in (("1 minute", "1min"), ("5 minutes", "5min")):
        expectations[f"monitoring_automation_state_{suffix}:old"] = (
            f"SELECT time_bucket(interval '{width}', \"time\") AS bucket, location, cluster, device_name, "
            'count(*), min(device_state), max(device_state), last(device_state, "time"), '
            'last(device_mode, "time"), count(pid_output), min(pid_output), max(pid_output), '
            'last(pid_output, "time"), count(duty_cycle_percent), min(duty_cycle_percent), '
            'max(duty_cycle_percent), last(duty_cycle_percent, "time"), '
            'last(control_reason, "time"), max("time") '
            'FROM public.automation_state WHERE "time">=$1 AND "time"<$2 '
            "GROUP BY 1,2,3,4 ORDER BY 1,2,3,4",
            f"automation state {suffix} CAGG",
        )
        metric_names = (
            "effective_heating_setpoint",
            "effective_cooling_setpoint",
            "effective_humidity_setpoint",
            "effective_co2_setpoint",
            "effective_vpd_setpoint",
            "effective_light_intensity",
        )
        columns = [
            "time_bucket(interval '%s', \"time\") AS bucket" % width,
            "location",
            "cluster",
            "device_name",
            "mode",
        ]
        for metric in metric_names:
            value = f"({metric})"
            numeric = f"{metric}::numeric"
            columns.extend(
                (
                    f"count{value} AS {metric}_count",
                    f"sum({numeric}) AS {metric}_sum",
                    f"sum({numeric}*{numeric}) AS {metric}_sum_squares",
                    f"min({numeric}) AS {metric}_min",
                    f"max({numeric}) AS {metric}_max",
                    f'last({metric}, "time") AS {metric}_last',
                )
            )
        columns.append('max("time") AS last_observed_at')
        expectations[f"monitoring_effective_setpoints_{suffix}:old"] = (
            f"SELECT {', '.join(columns)} FROM public.effective_setpoints "
            'WHERE "time">=$1 AND "time"<$2 '
            "GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5",
            f"effective setpoints {suffix} CAGG",
        )
    expectations["monitoring_light_intensity_1day:old"] = (
        "SELECT time_bucket(interval '1 day', \"time\") AS bucket, location, cluster, device_name, "
        "count(effective_light_intensity)::bigint, sum(effective_light_intensity::numeric), "
        "sum(effective_light_intensity::numeric*effective_light_intensity::numeric), "
        'min(effective_light_intensity::numeric), max(effective_light_intensity::numeric), max("time") '
        'FROM public.effective_setpoints WHERE "time">=$1 AND "time"<$2 AND device_name IS NOT NULL '
        "AND (effective_light_intensity IS NOT NULL OR nominal_light_intensity IS NOT NULL "
        "OR ramp_progress_light IS NOT NULL) GROUP BY 1,2,3,4 ORDER BY 1,2,3,4",
        "light daily CAGG",
    )
    async with pool.acquire() as conn:
        for key, (sql, label) in expectations.items():
            lo, hi = W_OLD
            if key == "relay_1hour:old":
                # Relay: superseded hours stay as active=false rows (readers
                # exclude them); hours whose observations were all removed
                # legitimately have no active row. Every remaining active hour
                # must hold recorded-owner durations recomputed from raw slices.
                rebuilt = await conn.fetch(
                    "SELECT bucket,location,cluster,channel,device_id,device_name,device_type,on_microseconds,"
                    "off_microseconds,last_observed_at,source_watermark,active "
                    "FROM public.monitoring_relay_duration_1hour WHERE bucket>=$1 AND bucket<$2 AND active "
                    "ORDER BY 1,2,3,4,5,6,7",
                    lo,
                    hi,
                )
                for row in rebuilt:
                    slices = await conn.fetch(
                        "SELECT coalesce(sum((extract(epoch FROM end_at-start_at))*1000000)::bigint "
                        "FILTER (WHERE state='on'),0) AS on_us, "
                        "coalesce(sum((extract(epoch FROM end_at-start_at))*1000000)::bigint "
                        "FILTER (WHERE state='off'),0) AS off_us "
                        "FROM public.monitoring_relay_duration_slices($1,$2,$3) "
                        "WHERE location=$4 AND cluster=$5 AND channel=$6 AND device_id=$7 "
                        "AND device_name=$8 AND device_type=$9",
                        row["bucket"],
                        row["bucket"] + timedelta(hours=1),
                        row["source_watermark"],
                        row["location"],
                        row["cluster"],
                        row["channel"],
                        row["device_id"],
                        row["device_name"],
                        row["device_type"],
                    )
                    _assert(
                        slices[0]["on_us"] == row["on_microseconds"]
                        and slices[0]["off_us"] == row["off_microseconds"],
                        f"relay hour {row['bucket']} does not hold recomputed recorded-owner durations",
                    )
                continue
            expected = [tuple(row.values()) for row in await conn.fetch(sql, lo, hi)]
            _assert(
                Counter(after[key]) == Counter(expected),
                f"archived contributions remain visible in {label}",
            )
    # Relay daily sums must equal the active hourly rollup across both windows.
    async with pool.acquire() as conn:
        daily = await conn.fetch(
            "SELECT bucket,location,cluster,channel,device_id,device_name,device_type,on_microseconds,"
            "off_microseconds,last_observed_at,source_watermark FROM public.monitoring_relay_duration_1day "
            "WHERE bucket>=$1 AND bucket<$2 ORDER BY 1,2,3,4,5,6,7",
            W_OLD_START,
            W_KEEP_END,
        )
        hourly_rollup = await conn.fetch(
            "SELECT time_bucket(interval '1 day', bucket) AS bucket, location, cluster, channel, device_id, "
            "device_name, device_type, sum(on_microseconds)::bigint, sum(off_microseconds)::bigint, "
            "max(last_observed_at), max(source_watermark) FROM public.monitoring_relay_duration_1hour "
            "WHERE active AND bucket>=$1 AND bucket<$2 GROUP BY 1,2,3,4,5,6,7 ORDER BY 1,2,3,4,5,6,7",
            W_OLD_START,
            W_KEEP_END,
        )
        _assert(
            [tuple(r.values()) for r in daily] == [tuple(r.values()) for r in hourly_rollup],
            "relay daily CAGG does not match the active hourly rollup after repair",
        )


async def _snapshot_privileges(pool: asyncpg.Pool) -> list:
    return await pool.fetch("""
        SELECT object_kind, object_id, acl
        FROM (
            SELECT 'relation'::text AS object_kind, c.oid::text AS object_id, c.relacl::text AS acl
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname = 'public' AND c.relacl IS NOT NULL
            UNION ALL
            SELECT 'routine', p.oid::text, p.proacl::text
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname = 'public' AND p.proacl IS NOT NULL
            UNION ALL
            SELECT 'schema', n.oid::text, n.nspacl::text
            FROM pg_namespace n
            WHERE n.nspname = 'public' AND n.nspacl IS NOT NULL
            UNION ALL
            SELECT 'default-privilege', format('%s:%s:%s', d.defaclrole, d.defaclnamespace, d.defaclobjtype),
                   d.defaclacl::text
            FROM pg_default_acl d
            UNION ALL
            SELECT 'role-membership', format('%s:%s', m.roleid, m.member), m.admin_option::text
            FROM pg_auth_members m
        ) acl_state
        ORDER BY object_kind, object_id
    """)


async def validate_static_state(
    pool, coverage_before, jobs_before, owners_before, privileges_before
) -> None:
    coverage_after = await pool.fetch(
        "SELECT * FROM public.monitoring_overview_coverage ORDER BY model_name"
    )
    _assert(coverage_after == coverage_before, "module advanced monitoring_overview_coverage")
    jobs_after = await pool.fetch(
        "SELECT id,config,scheduled FROM _timescaledb_catalog.bgw_job ORDER BY id"
    )
    _assert(jobs_after == jobs_before, "job configs/schedules changed")
    owners_after = await pool.fetch(
        "SELECT c.user_view_name,pg_get_userbyid(v.relowner) AS owner "
        "FROM _timescaledb_catalog.continuous_agg c JOIN pg_class v "
        "ON v.oid=to_regclass(format('%I.%I',c.user_view_schema,c.user_view_name)) ORDER BY 1"
    )
    _assert(owners_after == owners_before, "CAGG ownership changed")
    privileges_after = await _snapshot_privileges(pool)
    _assert(privileges_after == privileges_before, "module changed database ACLs")
    progress = await pool.fetchrow(
        "SELECT claimed_from,claimed_through FROM public.monitoring_relay_overview_progress"
    )
    _assert(
        progress["claimed_from"] is None and progress["claimed_through"] is None,
        "relay claim leaked",
    )


async def freshness_proof(database_url: str) -> None:
    """Real Redis publications drive the actual guard; stale pauses, fresh resumes."""
    with tempfile.TemporaryDirectory(prefix="derived-freshness-") as root:
        sock, port = _owned_socket()
        sock.close()
        process = subprocess.Popen(
            [
                "redis-server",
                "--bind",
                "127.0.0.1",
                "--port",
                str(port),
                "--protected-mode",
                "no",
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                root,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        client = None
        try:
            client = redis.asyncio.Redis.from_url(
                f"redis://127.0.0.1:{port}/0", decode_responses=True
            )
            for _ in range(100):
                try:
                    await client.ping()
                    break
                except redis.RedisError:
                    await asyncio.sleep(0.05)
            else:
                raise AssertionError("owned loopback Redis fixture did not start")
            control = HeavyWorkControl(initial_pause_reason="freshness_pending")
            guard = PublicationFreshnessGuard(client, control)
            stale = await guard.poll_once()
            _assert(
                not stale.all_fresh and guard.paused and control.paused,
                "absent publications must pause heavy work",
            )
            keys = [
                monitoring_current_publication_key(room) for room in ("Flower Room", "Veg Room")
            ]
            await client.mset(
                {key: _snapshot_payload(datetime.now(UTC) - timedelta(seconds=30)) for key in keys}
            )
            stale_observation = await guard.poll_once()
            _assert(not stale_observation.all_fresh, "30s-old observation must be stale")
            stop = asyncio.Event()

            async def publish_fresh() -> None:
                while not stop.is_set():
                    payload = _snapshot_payload(datetime.now(UTC))
                    await client.mset({key: payload for key in keys})
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=1.0)
                    except TimeoutError:
                        pass

            publisher = asyncio.create_task(publish_fresh())
            try:
                for _ in range(100):
                    verdict = await guard.poll_once()
                    if verdict.all_fresh and not guard.paused:
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("fresh publications never resumed heavy work")
            finally:
                stop.set()
                await asyncio.gather(publisher, return_exceptions=True)
            await client.mset(
                {key: _snapshot_payload(datetime.now(UTC) - timedelta(seconds=30)) for key in keys}
            )
            paused = await guard.poll_once()
            _assert(paused.heavy_work_paused and control.paused, "stale publication must re-pause")
            control.allow_resume()
            _assert(not control.paused, "allow_resume must clear the pause")
        finally:
            if client is not None:
                await client.aclose()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    asyncio.run(run(parser.parse_args().database_url))
