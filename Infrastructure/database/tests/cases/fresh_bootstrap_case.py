"""Prove that baseline.sql reproduces the cleared production copy exactly.

Usage: fresh_bootstrap_case.py <reference_db> <fresh_db>
Both databases must be monitoring_test_* on the CEA_TEST_PGPORT cluster.
Production (sj_cea, 5432) is only read inside a READ ONLY transaction.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import asyncpg
from schema_snapshot import collect_schema_snapshot

CLEAR_SQL = HERE.parents[1] / "baseline" / "clear_history.sql"
JOB_IDENTITY_FIELDS = ("job_id", "application_name")
# Background jobs start right after post-restore and may legitimately advance
# these progress rows between the two snapshots; their counts still compare.
JOB_WRITTEN_TABLES = frozenset(
    {"monitoring_overview_coverage", "monitoring_relay_overview_progress"}
)


class FreshBootstrapFailure(AssertionError):
    pass


def _table_list(name: str) -> frozenset[str]:
    text = CLEAR_SQL.read_text(encoding="utf-8")
    match = re.search(rf"INSERT INTO {name}\(name\) VALUES(.*?);", text, re.S)
    if match is None:
        raise FreshBootstrapFailure(f"{name} list not found in {CLEAR_SQL}")
    return frozenset(re.findall(r"\('([a-z0-9_]+)'\)", match.group(1)))


def _disposable(name: str) -> str:
    if not re.fullmatch(r"monitoring_test_[a-z0-9_]+", name):
        raise FreshBootstrapFailure(f"refusing non-disposable database {name}")
    return name


async def _connect(database: str, port: int) -> asyncpg.Connection:
    return await asyncpg.connect(
        host="127.0.0.1",
        port=port,
        user="cea_user",
        password=os.environ["PGPASSWORD"],
        database=database,
        timeout=10,
    )


def _normalized(snapshot: dict[str, object]) -> dict[str, object]:
    jobs = []
    for job in snapshot["timescale_jobs"]:
        jobs.append({k: v for k, v in job.items() if k not in JOB_IDENTITY_FIELDS})
    normalized = dict(snapshot)
    normalized["table_rows_record_send_md5"] = [
        {k: v for k, v in row.items() if k != "row_digest"}
        if row["schema"] == "public" and row["relation"] in JOB_WRITTEN_TABLES
        else row
        for row in snapshot["table_rows_record_send_md5"]
    ]
    normalized["timescale_jobs"] = sorted(
        jobs, key=lambda job: json.dumps(job, sort_keys=True, default=str)
    )
    return json.loads(json.dumps(normalized, sort_keys=True, default=str))


def _report_difference(reference: dict[str, object], fresh: dict[str, object]) -> list[str]:
    problems = []
    for key in sorted(set(reference) | set(fresh)):
        left, right = reference.get(key), fresh.get(key)
        if left == right:
            continue
        if isinstance(left, list) and isinstance(right, list):
            only_left = [item for item in left if item not in right][:3]
            only_right = [item for item in right if item not in left][:3]
            problems.append(f"{key}: reference-only={only_left} fresh-only={only_right}")
        else:
            problems.append(f"{key}: reference={left} fresh={right}")
    return problems


async def main(reference_db: str, fresh_db: str) -> None:
    port = int(os.environ.get("CEA_TEST_PGPORT", "5433"))
    if port == 5432:
        raise FreshBootstrapFailure("CEA_TEST_PGPORT must not be production 5432")
    cleared, kept = _table_list("baseline_cleared"), _table_list("baseline_kept")

    reference_conn = await _connect(_disposable(reference_db), port)
    fresh_conn = await _connect(_disposable(fresh_db), port)
    try:
        reference = _normalized(await collect_schema_snapshot(reference_conn))
        fresh = _normalized(await collect_schema_snapshot(fresh_conn))
        token_count = await fresh_conn.fetchval(
            "SELECT count(*) FROM public.calendar_room_profile WHERE caldav_sync_token IS NOT NULL"
        )
        failed_jobs = await fresh_conn.fetch(
            "SELECT s.job_id, j.proc_name FROM timescaledb_information.job_stats s "
            "JOIN timescaledb_information.jobs j USING (job_id) WHERE s.last_run_status = 'Failed'"
        )
        setting_keys = [
            row["key"]
            for row in await fresh_conn.fetch("SELECT key FROM public.service_setting ORDER BY key")
        ]
    finally:
        await reference_conn.close()
        await fresh_conn.close()

    problems = _report_difference(reference, fresh)

    fresh_rows = {
        row["relation"]: row["row_count"]
        for row in fresh["table_rows_record_send_md5"]
        if row["schema"] == "public"
    }
    for table in sorted(cleared & set(fresh_rows)):
        if fresh_rows[table] != 0:
            problems.append(f"cleared table {table} has {fresh_rows[table]} rows")
    for cagg in fresh["continuous_aggregate_row_counts"]:
        if cagg["row_count"] != 0:
            problems.append(
                f"continuous aggregate {cagg['view_name']} has {cagg['row_count']} rows"
            )
    if token_count != 0:
        problems.append(f"calendar_room_profile keeps {token_count} caldav_sync_token values")
    for job in failed_jobs:
        problems.append(f"job {job['job_id']} ({job['proc_name']}) failed on the fresh database")

    production = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        user="cea_user",
        password=os.environ["PGPASSWORD"],
        database="sj_cea",
        timeout=10,
    )
    try:
        async with production.transaction(readonly=True):
            await production.execute("SET LOCAL statement_timeout = '30s'")
            for table in sorted(kept & set(fresh_rows)):
                if table == "migration_0012_state":
                    continue
                exists = await production.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL", f"public.{table}"
                )
                if not exists:
                    problems.append(f"kept table {table} is missing from production")
                    continue
                count = await production.fetchval(f'SELECT count(*) FROM public."{table}"')
                if table == "schema_migration":
                    # The disposable copy also applied the release's pending migrations.
                    if fresh_rows[table] < count:
                        problems.append(
                            f"schema_migration: fresh={fresh_rows[table]} < production={count}"
                        )
                elif count != fresh_rows[table]:
                    problems.append(
                        f"kept table {table}: fresh={fresh_rows[table]} production={count}"
                    )
    finally:
        await production.close()

    print("service_setting keys for owner review:", ", ".join(setting_keys))
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        raise SystemExit(1)
    print(
        f"fresh bootstrap proof passed: {len(kept)} kept, {len(cleared)} cleared, snapshots equal"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: fresh_bootstrap_case.py <reference_db> <fresh_db>")
    asyncio.run(main(sys.argv[1], sys.argv[2]))
