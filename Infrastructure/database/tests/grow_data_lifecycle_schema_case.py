"""Schema-case runner: the python half of --case schema.

test-grow-data-lifecycle.sh applies the fixture and applies the idempotent
grow_data_lifecycle.sql migration twice before running the pure-SQL schema
case. This runner owns the descriptor preflight and every behaviour that needs
to re-run the preflight between statements, plus the parent-owned durable
queue proof.

It only accepts the guarded disposable loopback database URL the harness
passes (mirroring the bash guards: loopback host, monitoring_test_ namespace,
both production names rejected), and it fails closed on every ambiguity.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Sequence

HERE = Path(__file__).resolve().parent
INFRASTRUCTURE = HERE.parents[1]
SERVICE_ROOT = INFRASTRUCTURE / "grow-archive-service"
for _candidate in (INFRASTRUCTURE, SERVICE_ROOT, SERVICE_ROOT / "tests"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

import asyncpg  # noqa: E402  (sys.path must be ready before imports)

from grow_archive_service.schema import (  # noqa: E402
    assert_schema_preflight,
    load_source_catalog,
    run_schema_preflight,
)

CONNECTION_PATTERN = re.compile(
    r"^postgresql://[^:/@]+:[^/@]+@([^:/?#]+):([0-9]+)/([^/?#]+)$"
)


class CaseFailure(AssertionError):
    """A schema-case assertion failed; the bash runner treats this as exit 1."""


def assert_safe_database_url(url: str) -> None:
    """Mirror the bash guard: loopback host, monitoring_test_ name only."""

    match = CONNECTION_PATTERN.match(url)
    if not match:
        raise CaseFailure("database URL does not match the guarded PostgreSQL URL shape")
    host, port, database_name = match.group(1), match.group(2), match.group(3)
    lowered_host = host.lower()
    lowered_database = database_name.lower()
    if lowered_host not in ("127.0.0.1", "localhost", "::1"):
        raise CaseFailure(f"refusing non-loopback database host: {host}")
    for marker in ("mothernode", "projectcea"):
        if marker in lowered_host:
            raise CaseFailure(f"refusing production-looking database host: {host}")
    for marker in ("cea_sensors", "sj_cea"):
        if marker in lowered_database:
            raise CaseFailure(f"refusing production database name: {database_name}")
    if not re.fullmatch(r"monitoring_test_[a-z0-9_]+", lowered_database):
        raise CaseFailure(f"test database name must use the monitoring_test_ namespace: {database_name}")
    if not 1 <= int(port) <= 65535:
        raise CaseFailure(f"invalid PostgreSQL port: {port}")


def expect_blocked(report, stream_name: str, code: str) -> None:
    if report.ok:
        raise CaseFailure(f"preflight unexpectedly passed while {stream_name} should report {code}")
    stream = report.stream(stream_name)
    if code not in [finding.code for finding in stream.findings]:
        found = [finding.code for finding in stream.findings] or report.blocked_codes
        raise CaseFailure(f"expected {code} on {stream_name}, got {found}")


def expect_clean(report, *, context: str) -> None:
    if not report.ok:
        raise CaseFailure(f"{context}: preflight should pass: {report.as_dict()}")


def expect_absent(report, stream_name: str) -> None:
    stream = report.stream(stream_name)
    if stream.status != "absent":
        raise CaseFailure(f"expected {stream_name} recorded absent, got {stream.status}")


async def outstanding_queue_rows(pool) -> int:
    row = await pool.fetchrow(
        "SELECT count(*) AS outstanding FROM archive_job WHERE state IN ('pending', 'running')"
    )
    return int(row["outstanding"])


async def run_queue_smoke(pool) -> None:
    """The parent-owned queue proof runs inside this disposable database.

    It must start with no outstanding queue rows (the SQL case never creates
    archive_job rows) and it leaves its own dataset/jobs behind; this runner
    cleans those disposable rows afterwards so later cases see a plain queue.
    """

    from smoke_queue import exercise_queue  # parent owns tests/smoke_queue.py

    active = await outstanding_queue_rows(pool)
    if active:
        raise CaseFailure(f"queue smoke requires no outstanding queue rows, found {active}")
    await exercise_queue(pool)
    await pool.execute(
        "DELETE FROM archive_job WHERE dataset_id IN ("
        "  SELECT dataset_id FROM archive_dataset WHERE kind = 'veg_quarter' AND calendar_year = 2040)"
    )
    await pool.execute("DELETE FROM archive_dataset WHERE kind = 'veg_quarter' AND calendar_year = 2040")
    leftover = await pool.fetchval(
        "SELECT count(*) FROM archive_dataset WHERE kind = 'veg_quarter' AND calendar_year = 2040"
    )
    if leftover:
        raise CaseFailure(f"queue cleanup left {leftover} disposable archive_dataset rows")
    print("durable queue proof passed (fixture rows cleaned)")


async def run_drift_probes(pool) -> None:
    """Every drift class must block, and restoring must unblock again."""

    async with pool.acquire() as conn:
        baseline = await run_schema_preflight(conn, catalog=load_source_catalog())
        expect_clean(baseline, context="baseline preflight on the migrated fixture")
        for name in ("light_state_history", "pid_adjustments", "setpoint_history", "rollout_events"):
            expect_absent(baseline, name)

        # 1. Unknown column on a core stream blocks, then restores cleanly.
        await conn.execute("ALTER TABLE public.measurement ADD COLUMN capture_drift_probe TEXT")
        try:
            report = await run_schema_preflight(conn)
            expect_blocked(report, "measurement", "schema_unknown_column")
        finally:
            await conn.execute("ALTER TABLE public.measurement DROP COLUMN capture_drift_probe")
        expect_clean(await assert_schema_preflight(conn), context="after dropping the drifted column")

        # Dropping and re-adding a field changes its ordinal; transaction
        # rollback restores both data and the exact original schema.
        drift = conn.transaction()
        await drift.start()
        try:
            await conn.execute(
                "ALTER TABLE public.mode_transition_history DROP COLUMN parameters_synced")
            expect_blocked(await run_schema_preflight(conn),
                           "mode_transition_history", "schema_missing_column")
            await conn.execute(
                "ALTER TABLE public.mode_transition_history ADD COLUMN parameters_synced JSONB")
            expect_blocked(await run_schema_preflight(conn),
                           "mode_transition_history", "schema_column_order_changed")
        finally:
            await drift.rollback()
        expect_clean(await assert_schema_preflight(conn), context="after column-order rollback")

        # 3. Type drift blocks.
        await conn.execute(
            "ALTER TABLE public.config_versions ALTER COLUMN config_type TYPE CHARACTER VARYING"
        )
        expect_blocked(
            await run_schema_preflight(conn),
            "config_versions",
            "schema_type_mismatch",
        )
        await conn.execute(
            "ALTER TABLE public.config_versions ALTER COLUMN config_type TYPE TEXT"
        )

        # 4. Nullability drift blocks.
        await conn.execute(
            "ALTER TABLE public.config_versions ALTER COLUMN config_type DROP NOT NULL"
        )
        expect_blocked(
            await run_schema_preflight(conn),
            "config_versions",
            "schema_nullability_mismatch",
        )
        await conn.execute(
            "ALTER TABLE public.config_versions ALTER COLUMN config_type SET NOT NULL"
        )

        # 5. A missing core stream blocks; renaming it back restores.
        await conn.execute("ALTER TABLE public.relay_observation RENAME TO relay_observation_probe")
        expect_blocked(
            await run_schema_preflight(conn),
            "relay_observation",
            "schema_core_stream_missing",
        )
        await conn.execute("ALTER TABLE public.relay_observation_probe RENAME TO relay_observation")
        expect_clean(
            await assert_schema_preflight(conn),
            context="after renaming the core stream back",
        )

        # 6. An unavailable stream that actually exists without a descriptor blocks.
        await conn.execute("CREATE TABLE public.setpoint_history (id integer)")
        try:
            unavailable = await run_schema_preflight(conn)
            if unavailable.ok:
                raise CaseFailure("preflight must block when an unavailable stream exists unmanaged")
            if unavailable.blocked_codes.count("schema_descriptor_unavailable") != 1:
                raise CaseFailure(
                    f"expected exactly one schema_descriptor_unavailable, got {unavailable.blocked_codes}"
                )
        finally:
            await conn.execute("DROP TABLE public.setpoint_history")

        # 7. Extra unique constraint blocks; dropping it restores.
        await conn.execute(
            "ALTER TABLE public.calendar_mode_application "
            "ADD CONSTRAINT calendar_probe_unique UNIQUE (id)"
        )
        unique_report = await run_schema_preflight(conn)
        if unique_report.ok:
            raise CaseFailure("preflight must block when the unique-constraint set drifts")
        if "schema_unique_constraint_mismatch" not in unique_report.blocked_codes:
            raise CaseFailure(f"expected schema_unique_constraint_mismatch, got {unique_report.blocked_codes}")
        await conn.execute(
            "ALTER TABLE public.calendar_mode_application DROP CONSTRAINT calendar_probe_unique"
        )

        # 8. A dropped primary key blocks; restoring it restores.
        await conn.execute(
            "ALTER TABLE public.monitoring_photoperiod_coverage "
            "DROP CONSTRAINT monitoring_photoperiod_coverage_pkey"
        )
        expect_blocked(
            await run_schema_preflight(conn),
            "monitoring_photoperiod_coverage",
            "schema_primary_key_mismatch",
        )
        await conn.execute(
            "ALTER TABLE public.monitoring_photoperiod_coverage "
            "ADD CONSTRAINT monitoring_photoperiod_coverage_pkey PRIMARY KEY (id)"
        )

        expect_clean(
            await assert_schema_preflight(conn),
            context="after every drift probe was restored",
        )

    print("schema preflight drift probes passed")


async def main_async(*, database_url: str, with_queue_smoke: bool) -> int:
    assert_safe_database_url(database_url)
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    try:
        if with_queue_smoke:
            await run_queue_smoke(pool)
        else:
            print("skipping queue smoke (not requested)")
        await run_drift_probes(pool)
    finally:
        await pool.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grow-data-lifecycle-schema-case")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--with-queue-smoke", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(database_url=args.database_url, with_queue_smoke=args.with_queue_smoke))


if __name__ == "__main__":
    raise SystemExit(main())
