"""Representative full-rate one-day source capture/export measurement.

This standalone proof is not a registered grow-data-lifecycle case. Run it
with the guarded ``with_monitoring_test_db`` helper, using
``fixtures/grow_data_lifecycle_fixture.sql`` and applying both grow lifecycle
SQL files first; ``--database-url`` must be the helper's disposable URL.

It seeds one Toronto-local Flower Room day at documented observed rates,
captures every catalogued raw stream through ``SourceCapture``, and publishes
the verified ZIP through the real exporter and owned loopback DAV/Redis
fixtures. The reported source bytes are the measured ``record_send`` payload
bytes selected for that day. It does not project a multi-day grow or assert a
compression ratio.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
INFRASTRUCTURE = HERE.parent
MONITORING_ROOT = INFRASTRUCTURE / "monitoring-service"
for _entry in (str(HERE), str(INFRASTRUCTURE), str(MONITORING_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)


from smoke_lifecycle import _assert, _parse_database_url, _publish_freshness_fixture  # noqa: E402
from dav_fixture import loopback_dav  # noqa: E402

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import redis  # noqa: E402

from grow_archive_service.cache import ArchiveCache  # noqa: E402
from grow_archive_service.capture import SourceCapture  # noqa: E402
from grow_archive_service.config import load_settings  # noqa: E402
from grow_archive_service.deletion import SourceDeletion  # noqa: E402
from grow_archive_service.derived_refresh import DerivedHistoryMaintenance  # noqa: E402
from grow_archive_service.dispatcher import ArchiveDispatcher  # noqa: E402
from grow_archive_service.execution import HeavyWorkExecutor  # noqa: E402
from grow_archive_service.exporter import ExportService  # noqa: E402
from grow_archive_service.freshness import PublicationFreshnessGuard  # noqa: E402
from grow_archive_service.lifecycle import LifecycleService  # noqa: E402
from grow_archive_service.membership import CaptureWindow, selection_sql, window_arrays  # noqa: E402
from grow_archive_service.schema import load_source_catalog  # noqa: E402
from grow_archive_service.webdav import WebDAVArtifactClient  # noqa: E402
from shared.nextcloud_dav import discover_nextcloud  # noqa: E402

# Documented observed seven-day production rates (read-only sample).
OBSERVED = {
    "measurement_rows_per_day": 337784,
    "automation_state_rows_per_day": 258912,
    "effective_setpoints_rows_per_day": 25101,
    "source": "local://grow-size-estimate.json seven-day read-only production sample",
}

DAY_START = date(2026, 1, 5)  # Toronto-local day; window is [midnight, midnight) EST/EDT

STREAM_TIME_COLUMNS = {
    "measurement": "time",
    "automation_state": "time",
    "effective_setpoints": "time",
    "control_history": "time",
    "relay_observation": "time",
    "monitoring_room_photoperiod": "time",
    "monitoring_photoperiod_coverage": "time",
}


def _peak_rss_kib() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _process_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return float(usage.ru_utime + usage.ru_stime)


async def _source_record_send_bytes(pool: asyncpg.Pool, start: datetime, end: datetime) -> int:
    """Measure raw source tuple bytes selected for the one-day Flower scope."""
    catalog = load_source_catalog()
    windows = (CaptureWindow("Flower Room", start, end),)
    rooms, starts, ends = window_arrays(windows)
    total = 0
    async with pool.acquire() as conn:
        for stream in catalog.defined_streams():
            _assert(stream.time_column is not None, f"{stream.name} has no source time column")
            predicate, _role = selection_sql(stream)
            total += int(
                await conn.fetchval(
                    f"SELECT COALESCE(sum(octet_length(record_send(m))),0) "
                    f'FROM public."{stream.name}" m WHERE {predicate}',
                    rooms,
                    starts,
                    ends,
                    timeout=120.0,
                )
            )
            time_column = next(
                column for column in stream.columns if column.name == stream.time_column
            )
            if not time_column.not_null:
                null_predicate, _null_role = selection_sql(stream, include_null_time=True)
                total += int(
                    await conn.fetchval(
                        f"SELECT COALESCE(sum(octet_length(record_send(m))),0) "
                        f'FROM public."{stream.name}" m WHERE {null_predicate}',
                        rooms,
                        timeout=120.0,
                    )
                )
    return total


async def _start_redis(root: Path) -> tuple[subprocess.Popen, int]:
    """Own a loopback redis-server fixture for real publication freshness."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
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
            str(root),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run_proof._redis_process = process
    return process, port


# Flower-room lane composition for the seeded day (documented context cadences).
FLOWER_SENSORS = (1, 2, 3, 4)  # Flower Back Node + Flower Front Node sensors
FLOWER_DEVICES = (1, 2)
FLOWER_AUTOMATION_LANES = (
    ("Flower Room", "main", "heater_f_1"),
    ("Flower Room", "main", "fan_f_1"),
)
FLOWER_LIGHT_LANES = (("Flower Room", "main", "light_f_1"),)


def _strides(duration_seconds: float) -> dict[str, float]:
    """Evenly space each observed lane's exact row count across the day."""
    return {
        "measurement": duration_seconds
        / (OBSERVED["measurement_rows_per_day"] / len(FLOWER_SENSORS)),
        "automation": duration_seconds
        / (OBSERVED["automation_state_rows_per_day"] / len(FLOWER_AUTOMATION_LANES)),
        "effective": duration_seconds
        / (OBSERVED["effective_setpoints_rows_per_day"] / len(FLOWER_LIGHT_LANES)),
    }


async def attribute_flower_scope(pool: asyncpg.Pool, start: datetime, end: datetime) -> None:
    """Dated owner-confirmed scope corrections for every Flower sensor."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT s.sensor_id FROM sensor s JOIN device d USING(device_id) "
            "JOIN rack r USING(rack_id) JOIN room rm USING(room_id) WHERE rm.name=$1",
            "Flower Room",
        )
        for row in rows:
            await conn.execute(
                "INSERT INTO archive_scope_override(source_table,source_identity,start_at,end_at,room,confirmed_at) "
                "VALUES('measurement',jsonb_build_object('sensor_id',$1::integer),$2,$3,'Flower Room',clock_timestamp())",
                row["sensor_id"],
                start,
                end,
            )


async def seed_representative_day(
    pool: asyncpg.Pool, start: datetime, end: datetime
) -> dict[str, int]:
    """Insert one full-rate Flower day as real source rows; returns actual counts."""
    strides = _strides((end - start).total_seconds())
    measurement_rows_per_lane = OBSERVED["measurement_rows_per_day"] // len(FLOWER_SENSORS)
    automation_rows_per_lane = OBSERVED["automation_state_rows_per_day"] // len(
        FLOWER_AUTOMATION_LANES
    )
    effective_rows = OBSERVED["effective_setpoints_rows_per_day"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Each integer ordinal emits one exact row, with evenly spaced
            # timestamps from the start of the day up to (but not including) its end.
            for index, sensor in enumerate(FLOWER_SENSORS):
                await conn.execute(
                    f"INSERT INTO measurement(time,sensor_id,value,status) "
                    f"SELECT sample.ts,$2,(({index} + 1)::real * 11.5"
                    f" + ((extract(epoch from sample.ts)::int % 1000) * 0.005)::real),"
                    f"(CASE WHEN (extract(second from sample.ts)::int % 97) = 0 THEN 'warning' ELSE 'ok' END) "
                    "FROM generate_series(0,$3::integer - 1) AS ticks(g) "
                    "CROSS JOIN LATERAL (SELECT $1::timestamptz + "
                    "make_interval(secs => g * $4::double precision) AS ts) sample",
                    start,
                    sensor,
                    measurement_rows_per_lane,
                    strides["measurement"],
                )
            # Two automation lanes contain exactly the documented 258,912
            # state rows/day, including their per-tick PID and duty-cycle values.
            for location, cluster, device in FLOWER_AUTOMATION_LANES:
                await conn.execute(
                    'INSERT INTO automation_state("time",location,cluster,device_name,device_state,device_mode,'
                    "pid_output,duty_cycle_percent,active_rule_ids,active_schedule_ids,control_reason) "
                    "SELECT sample.ts,$2,$3,$4,1,'auto',"
                    "((extract(epoch from sample.ts)::int % 200) * 0.005)::real,"
                    "50.0::real,'{1,2}','{3,4}','representative load' "
                    "FROM generate_series(0,$5::integer - 1) AS ticks(g) "
                    "CROSS JOIN LATERAL (SELECT $1::timestamptz + "
                    "make_interval(secs => g * $6::double precision) AS ts) sample",
                    start,
                    location,
                    cluster,
                    device,
                    automation_rows_per_lane,
                    strides["automation"],
                )
            # The light lane has exactly its observed 25,101 setpoint rows/day.
            await conn.execute(
                'INSERT INTO effective_setpoints("time",location,cluster,mode,device_name,'
                "effective_light_intensity,nominal_light_intensity,ramp_progress_light) "
                "SELECT sample.ts,$2,$3,$5,$4,80.0::real,40.0::real,0.5::real "
                "FROM generate_series(0,$6::integer - 1) AS ticks(g) "
                "CROSS JOIN LATERAL (SELECT $1::timestamptz + "
                "make_interval(secs => g * $7::double precision) AS ts) sample",
                start,
                *FLOWER_LIGHT_LANES[0],
                "DAY",
                effective_rows,
                strides["effective"],
            )
            # Control-history rows follow the documented per-minute cadence.
            for device in FLOWER_DEVICES:
                await conn.execute(
                    'INSERT INTO control_history("time",location,cluster,device_name,channel,'
                    "old_state,new_state,mode,reason,setpoint) "
                    "SELECT g,'Flower Room','main',$2,0,0,1,'auto','representative control',23.0::real "
                    "FROM generate_series($1::timestamptz,$3::timestamptz - interval '1 microsecond',interval '1 minute') g",
                    start,
                    f"node_{device}",
                    end,
                )
            await conn.execute(
                'INSERT INTO relay_observation("time",session_id,channel,observed_state,device_id,'
                "device_name,device_type,location,cluster,registry_version,reason) "
                "SELECT g,'22222222-2222-4222-8222-222222222222',0,(row_number() OVER ()) % 2 = 0,"
                "1,'Flower Back Node','sensor-node','Flower Room','main',0,'state_changed' "
                "FROM generate_series($1::timestamptz,$2::timestamptz - interval '1 microsecond',interval '1 minute') g",
                start,
                end,
            )
            await conn.execute(
                'INSERT INTO monitoring_room_photoperiod("time",location,cluster,phase,mode_id,submode_id,'
                "runtime_snapshot_version,source) "
                "SELECT g,'Flower Room','main',"
                "CASE WHEN extract(hour from g) < 12 THEN 'SUN' ELSE 'MOON' END,2,1,0,'representative' "
                "FROM generate_series($1::timestamptz,$2::timestamptz - interval '1 microsecond',interval '1 minute') g",
                start,
                end,
            )
            await conn.execute(
                'INSERT INTO monitoring_photoperiod_coverage("time",location,cluster,state,reason,'
                "runtime_snapshot_version) "
                "SELECT g,'Flower Room','main','available','initial',0 "
                "FROM generate_series($1::timestamptz,$2::timestamptz - interval '1 microsecond',interval '1 minute') g",
                start,
                end,
            )
        counts = {}
        for stream, time_column in STREAM_TIME_COLUMNS.items():
            counts[stream] = int(
                await conn.fetchval(
                    f'SELECT count(*) FROM "{stream}" WHERE "{time_column}" >= $1 AND "{time_column}" < $2',
                    start,
                    end,
                )
            )
    return counts


async def run_proof(database_url: str) -> None:
    _parse_database_url(database_url)
    _assert(os.getenv("MONITORING_HARNESS_INTERNAL") == "1", "owned harness marker required")
    os.environ["ARCHIVE_CPU_DUTY_CYCLE"] = "0.25"
    os.environ["ARCHIVE_WORKER_CONCURRENCY"] = "1"
    settings = load_settings()
    _assert(settings.cpu_duty_cycle == 0.25, "archive CPU duty-cycle setting was not applied")
    pool = await asyncpg.create_pool(database_url, min_size=2, max_size=6, command_timeout=10)
    heavy = HeavyWorkExecutor(duty_cycle=settings.cpu_duty_cycle)
    heavy.control.allow_resume()
    started_monotonic = time.monotonic()
    provenance = {
        "rates_source": OBSERVED["source"],
        "rate_basis": "documented observed 7-day read-only production sample",
        "source_bytes_definition": (
            "measured sum of record_send payload bytes for rows selected from all "
            "catalogued streams in the one-day Flower scope; excludes indexes and storage overhead"
        ),
        "archive_cpu_duty_cycle": settings.cpu_duty_cycle,
    }
    try:
        async with pool.acquire() as conn:
            _assert(
                await conn.fetchval(
                    "SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
                    "WHERE fixture_name='grow-data-lifecycle'"
                )
                == 1,
                "fixture marker missing",
            )
        os.environ["CALDAV_ALLOW_HTTP_TEST"] = "true"

        with (
            tempfile.TemporaryDirectory(prefix="grow-retention-representative-") as directory,
            loopback_dav() as (origin, _dav_state),
        ):
            root = Path(directory)

            # Owned loopback Redis publications drive the real freshness guard.
            redis_process, redis_port = await _start_redis(root)
            client = redis.Redis.from_url(
                f"redis://127.0.0.1:{redis_port}/0", decode_responses=True
            )
            reader = redis.asyncio.Redis.from_url(
                f"redis://127.0.0.1:{redis_port}/0", decode_responses=True
            )
            for _ in range(100):
                try:
                    client.ping()
                    await reader.ping()
                    break
                except redis.RedisError:
                    await asyncio.sleep(0.05)
            else:
                _assert(False, "owned loopback Redis fixture did not start")
            publish_stop = asyncio.Event()
            publisher = asyncio.create_task(_publish_freshness_fixture(client, publish_stop))
            guard = PublicationFreshnessGuard(reader, heavy.control)
            pause_sampler = None
            try:
                for _ in range(100):
                    verdict = await guard.poll_once()
                    if verdict.all_fresh and not guard.paused:
                        break
                    await asyncio.sleep(0.05)
                else:
                    _assert(False, "fresh publications never resumed heavy work")
                freshness_pause_count = 0

                async def sample_pauses():
                    nonlocal freshness_pause_count
                    while not publish_stop.is_set():
                        if guard.paused or heavy.control.paused:
                            freshness_pause_count += 1
                        await asyncio.sleep(0.25)

                pause_sampler = asyncio.create_task(sample_pauses())

                @asynccontextmanager
                async def dav_factory(instance_id=None):
                    async with httpx.AsyncClient(
                        auth=("fixture", "fixture-only-password"),
                        trust_env=False,
                        follow_redirects=False,
                        timeout=30,
                    ) as http:
                        discovery = await discover_nextcloud(http, origin + "/dav")
                        if instance_id is not None:
                            _assert(
                                discovery.instance_id == instance_id, "fixture instance changed"
                            )
                        yield WebDAVArtifactClient(
                            discovery,
                            http,
                            instance_id=discovery.instance_id,
                            scratch_root=root / "staging" / "http",
                        )

                cache = ArchiveCache(root / "cache", root / "staging")
                capture = SourceCapture(pool, heavy)
                service = ExportService(
                    pool, root, heavy, capture, dav_factory=dav_factory, cache=cache
                )
                derived = DerivedHistoryMaintenance(pool, heavy)
                deletion = SourceDeletion(pool, heavy, service.queue, derived)
                lifecycle = LifecycleService(
                    pool, service, deletion, derived, enabled=False, freshness=guard
                )
                dispatcher = ArchiveDispatcher(lifecycle)

                # Seed one complete Toronto-local day at the documented source rates.
                day_start_utc = datetime(
                    DAY_START.year,
                    DAY_START.month,
                    DAY_START.day,
                    tzinfo=ZoneInfo("America/Toronto"),
                ).astimezone(UTC)
                next_day = DAY_START + timedelta(days=1)
                day_end_utc = datetime(
                    next_day.year, next_day.month, next_day.day, tzinfo=ZoneInfo("America/Toronto")
                ).astimezone(UTC)
                day_counts = await seed_representative_day(pool, day_start_utc, day_end_utc)
                _assert(
                    day_counts["measurement"] == OBSERVED["measurement_rows_per_day"],
                    f"measurement day rate mismatch: {day_counts['measurement']}",
                )
                _assert(
                    day_counts["automation_state"] == OBSERVED["automation_state_rows_per_day"],
                    f"automation day rate mismatch: {day_counts['automation_state']}",
                )
                _assert(
                    day_counts["effective_setpoints"]
                    == OBSERVED["effective_setpoints_rows_per_day"],
                    f"effective day rate mismatch: {day_counts['effective_setpoints']}",
                )
                dataset_id, grow_id = uuid4(), uuid4()
                async with pool.acquire() as conn:
                    crop_batch_id = await conn.fetchval(
                        "INSERT INTO crop_batch(crop_name,start_date,end_date,room_id) "
                        "VALUES($1,$2,$3,1) RETURNING batch_id",
                        "Representative Flower Day",
                        DAY_START,
                        next_day,
                    )
                    await conn.execute(
                        "INSERT INTO grow_lifecycle(grow_plan_id,crop_batch_id,state,original_drying_end,"
                        "first_closed_at,closed_at) VALUES($1,$2,'closed',$3::timestamptz,"
                        "clock_timestamp(),clock_timestamp())",
                        grow_id,
                        crop_batch_id,
                        day_end_utc,
                    )
                    await conn.execute(
                        "INSERT INTO archive_dataset(dataset_id,kind,grow_plan_id,room,available_from,available_to,"
                        "anniversary_at) VALUES($1,'grow',$2,'Flower Room',$3,$4,$5)",
                        dataset_id,
                        grow_id,
                        day_start_utc,
                        day_end_utc,
                        day_end_utc + timedelta(days=365),
                    )
                    await conn.execute(
                        "INSERT INTO archive_window(dataset_id,calendar_revision,event_id,phase_type,room,start_at,end_at) "
                        "VALUES($1,1,1000,'flower_bulk','Flower Room',$2,$3)",
                        dataset_id,
                        day_start_utc,
                        day_end_utc,
                    )
                await attribute_flower_scope(pool, day_start_utc, day_end_utc)
                source_bytes = await _source_record_send_bytes(pool, day_start_utc, day_end_utc)

                job = await service.queue.enqueue(dataset_id, "export", 1, required_closed=False)
                claim = await service.queue.claim(uuid4(), now=datetime.now(UTC), job_id=job.job_id)
                _assert(claim is not None, "export job was not claimable")
                export_started = time.monotonic()
                export_cpu_started = _process_cpu_seconds()
                await dispatcher._run_claim(claim)
                job_state = await service.queue.get(job.job_id)
                _assert(
                    job_state.state == "succeeded",
                    f"export job ended in {job_state.state}: {job_state.last_error_code}",
                )
                artifact_id = await pool.fetchval(
                    "SELECT artifact_id FROM public.archive_artifact "
                    "WHERE dataset_id=$1 AND revision=1 AND state='verified'",
                    dataset_id,
                )
                _assert(artifact_id is not None, "export job did not publish a verified artifact")
                verified = await service.reverify_artifact(artifact_id)
                export_wall_seconds = time.monotonic() - export_started
                export_process_cpu_seconds = _process_cpu_seconds() - export_cpu_started
                freshness_final = guard.verdict
                max_observed_age = max(
                    (
                        (freshness_final.checked_at - room.observed_at).total_seconds()
                        for room in (freshness_final.rooms or ())
                        if room.observed_at is not None
                    ),
                    default=None,
                )
                metrics = {
                    "result": "PASS",
                    "provenance": provenance,
                    "seeded_rows_by_stream": day_counts,
                    "manifest_observation_counts": verified.data.get("observation_counts", {}),
                    "manifest_context_counts": verified.data.get("context_counts", {}),
                    "source_record_send_bytes": source_bytes,
                    "zip_bytes": verified.byte_size,
                    "zip_sha256": verified.sha256,
                    "export_wall_seconds": export_wall_seconds,
                    "export_process_cpu_seconds": export_process_cpu_seconds,
                    "export_process_average_cpu_percent_of_one_core": (
                        export_process_cpu_seconds / export_wall_seconds * 100.0
                    ),
                    "freshness_max_observation_age_seconds": max_observed_age,
                    "freshness_all_fresh_at_end": bool(
                        freshness_final and freshness_final.all_fresh
                    ),
                    "freshness_pause_count": freshness_pause_count,
                    "peak_rss_kib": _peak_rss_kib(),
                    "proof_wall_seconds": time.monotonic() - started_monotonic,
                }
                print(json.dumps(metrics, sort_keys=True, default=str))
            finally:
                publish_stop.set()
                if pause_sampler is not None:
                    pause_sampler.cancel()
                    await asyncio.gather(pause_sampler, return_exceptions=True)
                await asyncio.gather(publisher, return_exceptions=True)
                await reader.aclose()
                client.close()
                if redis_process.poll() is None:
                    redis_process.terminate()
                    try:
                        redis_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        redis_process.kill()
    finally:
        await heavy.shutdown()
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    asyncio.run(run_proof(parser.parse_args().database_url))
