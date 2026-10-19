"""Guarded end-to-end source-resident grow/Veg/nongrow lifecycle proof."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

from dav_fixture import loopback_dav
from smoke_derived_refresh import install_real_derived_schema
from smoke_lifecycle import _assert, _parse_database_url, _snapshot_payload
import asyncpg
import httpx
import redis
from fastapi import HTTPException

from grow_archive_service.capture import SourceCapture
from grow_archive_service.derived_refresh import DerivedHistoryMaintenance
from grow_archive_service.deletion import SourceDeletion
from grow_archive_service.execution import HeavyWorkExecutor
from grow_archive_service.exporter import ExportService
from grow_archive_service.freshness import PublicationFreshnessGuard
from grow_archive_service.lifecycle import LifecycleService, quarter_bounds, retention_cutoffs
from grow_archive_service.models import ArchiveError
from grow_archive_service.schema import load_source_catalog
from grow_archive_service.webdav import WebDAVArtifactClient
from shared.history_availability import require_local_history
from shared.nextcloud_dav import discover_nextcloud
from shared.redis_keys import monitoring_current_publication_key


async def _source_bags(pool):
    async with pool.acquire() as conn:
        return {
            stream.name: Counter(row['payload'] for row in await conn.fetch(
                f'SELECT record_send(m) AS payload FROM public."{stream.name}" m'))
            for stream in load_source_catalog().defined_streams()
        }


async def _expect_error(code, action):
    try:
        await action()
    except ArchiveError as error:
        _assert(error.code == code, f'expected {code}, got {error.code}')
    else:
        raise AssertionError(f'expected {code}')


async def _create_grow(pool):
    grow_id, dataset_id = uuid4(), uuid4()
    start, end = datetime(2025, 8, 1, tzinfo=UTC), datetime(2025, 9, 2, tzinfo=UTC)
    batch = await pool.fetchval(
        "INSERT INTO crop_batch(crop_name,start_date,end_date,room_id) "
        "SELECT $1,$2,$3,room_id FROM room WHERE name='Flower Room' RETURNING batch_id",
        f'owned-enabled-lifecycle-{grow_id}', start.date(), end.date())
    _assert(batch is not None, 'no Flower Room crop batch room found')
    await pool.execute(
        "INSERT INTO grow_lifecycle(grow_plan_id,crop_batch_id,state,original_drying_end,"
        "first_closed_at,closed_at) VALUES($1,$2,'closed',$3,$4,$4)",
        grow_id, batch, date(2025, 9, 1), datetime(2025, 9, 2, tzinfo=UTC))
    anniversary = datetime(2026, 9, 2, 4, tzinfo=UTC)
    sample_at = datetime(2025, 8, 2, 1, 15, tzinfo=UTC)
    window_start = sample_at.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=1)
    await pool.execute(
        "INSERT INTO archive_dataset(dataset_id,kind,grow_plan_id,room,calendar_revision,"
        "data_revision,published_revision,state,available_from,available_to,anniversary_at) "
        "VALUES($1,'grow',$2,'Flower Room',1,1,NULL,'pending',$3,$4,$5)",
        dataset_id, grow_id, window_start, window_end, anniversary)
    event_id = await pool.fetchval("SELECT nextval(pg_get_serial_sequence('calendar_event','id'))")
    await pool.execute(
        "INSERT INTO archive_window(dataset_id,calendar_revision,event_id,phase_type,room,start_at,end_at) "
        "VALUES($1,1,$2,'drying','Flower Room',$3,$4)", dataset_id, event_id, window_start, window_end)
    await pool.execute(
        "INSERT INTO archive_scope_override(source_table,source_identity,room,start_at,end_at) "
        "VALUES('measurement',jsonb_build_object('sensor_id',1),'Flower Room',$1,$2)",
        window_start, window_end)
    await pool.execute(
        "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,1,31.25::real,'ok')", sample_at)
    return grow_id, dataset_id, sample_at, anniversary


async def _finish(queue, job_id, handler):
    claim = await queue.claim(uuid4(), now=datetime.now(UTC), job_id=job_id)
    _assert(claim is not None, f'owned lifecycle job not claimable: {job_id}')
    try:
        value = await handler(claim)
        await queue.succeed(claim)
        return value
    except ArchiveError as error:
        await queue.fail(claim, error.code)
        raise


async def _run_export_planned(service, exporter, dataset_id, now):
    tick = await service.run_lifecycle_tick(now)
    jobs = [item for item in tick.planned if item.dataset_id == dataset_id and item.kind == 'export']
    _assert(len(jobs) == 1, f'planner did not schedule one export: planned={tick.planned} held={tick.held}')
    return await _finish(service.queue, jobs[0].job_id, exporter.export_dataset)


async def run(database_url: str) -> None:
    _parse_database_url(database_url)
    _assert(os.getenv('MONITORING_HARNESS_INTERNAL') == '1', 'owned harness marker required')
    await install_real_derived_schema(database_url)
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3, command_timeout=30)
    heavy = HeavyWorkExecutor()
    heavy.control.allow_resume()
    redis_process = None
    redis_client = None
    redis_reader = None
    publisher = None
    publish_stop = asyncio.Event()
    try:
        _assert(await pool.fetchval(
            "SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
            "WHERE fixture_name='grow-data-lifecycle'") == 1, 'owned fixture marker missing')
        indexes_before = tuple(await pool.fetch(
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND tablename='automation_state' ORDER BY indexname"))
        _assert(len(indexes_before) == 6, 'fixture does not preserve all six automation_state indexes')
        os.environ['CALDAV_ALLOW_HTTP_TEST'] = 'true'
        with tempfile.TemporaryDirectory(prefix='grow-enabled-lifecycle-') as directory, \
                loopback_dav() as (origin, state):
            root = Path(directory)
            redis_process = subprocess.Popen(
                ['redis-server', '--bind', '127.0.0.1', '--port', '0', '--unixsocket',
                 str(root / 'redis.sock'), '--unixsocketperm', '700', '--save', '', '--appendonly', 'no'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            redis_client = redis.Redis(unix_socket_path=str(root / 'redis.sock'), decode_responses=True)
            redis_reader = redis.asyncio.Redis(unix_socket_path=str(root / 'redis.sock'), decode_responses=True)
            for _ in range(200):
                try:
                    redis_client.ping()
                    break
                except redis.RedisError:
                    await asyncio.sleep(0.05)
            else:
                _assert(False, 'owned Redis fixture did not start')
            freshness_keys = [monitoring_current_publication_key(room)
                              for room in ('Flower Room', 'Veg Room')]

            async def publish_freshness():
                while not publish_stop.is_set():
                    payload = _snapshot_payload(datetime.now(UTC))
                    await asyncio.to_thread(redis_client.mset,
                                            {key: payload for key in freshness_keys})
                    try:
                        await asyncio.wait_for(publish_stop.wait(), timeout=1)
                    except TimeoutError:
                        pass

            publisher = asyncio.create_task(publish_freshness())
            freshness = PublicationFreshnessGuard(redis_reader, heavy.control)
            for _ in range(100):
                verdict = await freshness.poll_once()
                if verdict.all_fresh and not freshness.paused:
                    break
                await asyncio.sleep(0.05)
            else:
                _assert(False, 'freshness fixture did not resume archive work')

            @asynccontextmanager
            async def dav_factory(instance_id=None):
                async with httpx.AsyncClient(auth=('fixture', 'fixture-only-password'), trust_env=False,
                                             follow_redirects=False, timeout=15) as client:
                    discovery = await discover_nextcloud(client, origin + '/dav')
                    if instance_id is not None:
                        _assert(discovery.instance_id == instance_id, 'Nextcloud instance changed')
                    yield WebDAVArtifactClient(discovery, client, instance_id=discovery.instance_id,
                                               scratch_root=root / 'staging' / 'http')

            policy_now = datetime(2026, 9, 1, 12, tzinfo=UTC)
            maintenance = DerivedHistoryMaintenance(pool, heavy)
            exporter = ExportService(pool, root, heavy, SourceCapture(pool, heavy),
                                     clock=lambda: policy_now, dav_factory=dav_factory)
            deletion = SourceDeletion(pool, heavy, exporter.queue, maintenance)
            service = LifecycleService(pool, exporter, deletion, maintenance, enabled=True,
                                       clock=lambda: policy_now, freshness=freshness)

            # Closing already happened before this fixture; planning exports the
            # closed grow while every byte remains in the ordinary source table.
            grow_id, dataset_id, sample_at, anniversary = await _create_grow(pool)
            sample_wire = await pool.fetchval(
                "SELECT record_send(m) FROM measurement m WHERE time=$1 AND sensor_id=1", sample_at)
            artifact_id = await _run_export_planned(service, exporter, dataset_id, policy_now)
            _assert(await pool.fetchval(
                "SELECT state='verified' FROM archive_artifact WHERE artifact_id=$1", artifact_id),
                'closed grow export did not produce a verified remote artifact')
            _assert(await pool.fetchval(
                "SELECT record_send(m) FROM measurement m WHERE time=$1 AND sensor_id=1", sample_at) == sample_wire,
                'planned export changed the source observation')
            _assert(await pool.fetchval(
                "SELECT state FROM archive_dataset WHERE dataset_id=$1", dataset_id) == 'ready',
                'published grow dataset did not become ready')

            policy_now = anniversary - timedelta(microseconds=1)
            early = await service.run_lifecycle_tick(policy_now)
            _assert(not any(item.dataset_id == dataset_id and item.kind == 'expire' for item in early.planned),
                    'grow expiry was planned before the anniversary')
            _assert(await pool.fetchval(
                "SELECT record_send(m) FROM measurement m WHERE time=$1 AND sensor_id=1", sample_at) == sample_wire,
                'pre-anniversary tick changed grow source bytes')

            policy_now = anniversary
            due = await service.run_lifecycle_tick(policy_now)
            expiry_jobs = [item for item in due.planned if item.dataset_id == dataset_id and item.kind == 'expire']
            _assert(len(expiry_jobs) == 1,
                    f'anniversary planner did not schedule one grow expiry: {due.planned} {due.held}')
            expiry_job_id = expiry_jobs[0].job_id
            remote_path = await pool.fetchval(
                'SELECT remote_path FROM archive_artifact WHERE artifact_id=$1', artifact_id)
            remote_key = '/dav/files/fixture/' + remote_path
            remote_bytes = state.files[remote_key]
            source_bags_before = await _source_bags(pool)
            uploads_before = state.counters.get('PUT', 0)

            async def retry_expiry_after_failure(code):
                await _expect_error(code, lambda: _finish(service.queue, expiry_job_id, service.expire))
                _assert(await _source_bags(pool) == source_bags_before,
                        f'{code} remote verification failure changed source table bytes')
                _assert(await pool.fetchval(
                    "SELECT record_send(m) FROM measurement m WHERE time=$1 AND sensor_id=1", sample_at)
                    == sample_wire, f'{code} changed the grow source observation')
                await pool.execute(
                    "UPDATE archive_job SET state='pending',next_attempt_at=clock_timestamp() WHERE job_id=$1",
                    expiry_job_id)

            state.files.pop(remote_key)
            await retry_expiry_after_failure('archive_remote_unavailable')
            state.files[remote_key] = remote_bytes + b'corrupt'
            await retry_expiry_after_failure('archive_integrity_failed')
            state.files[remote_key] = remote_bytes

            hour_start = sample_at.replace(minute=0, second=0, microsecond=0)
            hour_end = hour_start + timedelta(hours=1)
            await maintenance.refresh('measurement', 'Flower Room', hour_start, hour_end)
            before_aggregate = await pool.fetchval(
                "SELECT sample_count FROM public.monitoring_measurement_1hour "
                "WHERE bucket=$1 AND sensor_id=1", hour_start)
            _assert(before_aggregate == 1, f'source sample was not present in hourly aggregate: {before_aggregate}')
            removed = await _finish(service.queue, expiry_job_id, service.expire)
            _assert(removed == 1, f'verified anniversary expiry removed {removed} rows, expected one')
            _assert(state.counters.get('PUT', 0) == uploads_before, 'expiry unexpectedly uploaded an archive')
            availability = await pool.fetchrow(
                "SELECT source_rows_removed,aggregates_refreshed FROM archive_availability "
                "WHERE dataset_id=$1 AND source_table='measurement' AND room='Flower Room' "
                "AND start_at<=$2 AND end_at>=$3 ORDER BY start_at LIMIT 1",
                dataset_id, sample_at, sample_at)
            _assert(availability is not None and availability['source_rows_removed']
                    and availability['aggregates_refreshed'],
                    f'deletion availability/aggregate refresh was not recorded: {availability}')
            _assert(await pool.fetchval(
                "SELECT count(*) FROM measurement WHERE time=$1 AND sensor_id=1", sample_at) == 0,
                'verified grow expiry retained its source row')
            _assert(await pool.fetchval(
                "SELECT count(*) FROM public.monitoring_measurement_1hour "
                "WHERE bucket=$1 AND sensor_id=1", hour_start) == 0,
                'derived hourly aggregate was not refreshed after source deletion')
            async with pool.acquire() as conn:
                try:
                    await require_local_history(conn, room='Flower Room', start=hour_start, end=hour_end,
                                                source_tables=('measurement',))
                except HTTPException as error:
                    _assert(error.status_code == 409
                            and error.detail == {'code': 'archive_selection_required',
                                                 'dataset_ids': [str(dataset_id)]},
                            f'ordinary history did not return the expected archive-selection 409: {error.detail}')
                else:
                    raise AssertionError('removed grow history bypassed the 409 availability guard')
            _assert(await exporter.reverify_artifact(artifact_id), 'deleted grow source lacks verified remote ZIP')
            settled = await service.run_lifecycle_tick(policy_now)
            _assert(not any(item.dataset_id == dataset_id for item in settled.planned),
                    f'fully removed grow was rescheduled: {settled.planned}')

            # A complete Veg quarter uses the same direct source export; the
            # progressive cutoff removes only samples strictly older than 12 months.
            veg_year, veg_quarter = 2025, 2
            veg_id = uuid4()
            _, quarter_end = quarter_bounds(veg_year, veg_quarter)
            policy_now = datetime(2026, 6, 30, 12, tzinfo=UTC)
            expiry_cutoff = retention_cutoffs(policy_now).local_expiry_before
            veg_window_start = expiry_cutoff - timedelta(hours=2)
            old_veg_at = veg_window_start
            old_veg_anchor_at = expiry_cutoff - timedelta(hours=1)
            recent_veg_at = expiry_cutoff + timedelta(hours=1)
            await pool.execute(
                "INSERT INTO archive_dataset(dataset_id,kind,calendar_year,quarter,room,"
                "calendar_revision,data_revision,state,available_from,available_to) "
                "VALUES($1,'veg_quarter',$2,$3,'Veg Room',1,1,'pending',$4,$5)",
                veg_id, veg_year, veg_quarter, veg_window_start, quarter_end)
            await pool.execute(
                "INSERT INTO archive_scope_override(source_table,source_identity,room,start_at,end_at) "
                "VALUES('measurement',jsonb_build_object('sensor_id',5),'Veg Room',$1,$2)",
                veg_window_start, quarter_end)
            for instant, value in ((old_veg_at, 24.5), (old_veg_anchor_at, 25.5),
                                   (recent_veg_at, 26.5)):
                await pool.execute(
                    "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,5,$2::real,'ok')",
                    instant, value)
            veg_export_tick = await service.run_lifecycle_tick(policy_now)
            veg_export_jobs = [item for item in veg_export_tick.planned
                               if item.dataset_id == veg_id and item.kind == 'export']
            _assert(len(veg_export_jobs) == 1,
                    f'complete nine-month Veg quarter did not plan its export: {veg_export_tick.planned}')
            await _finish(service.queue, veg_export_jobs[0].job_id, exporter.export_dataset)
            expiry_tick = await service.run_lifecycle_tick(policy_now)
            veg_expiry_jobs = [item for item in expiry_tick.planned
                               if item.dataset_id == veg_id and item.kind == 'expire']
            _assert(len(veg_expiry_jobs) == 1,
                    f'verified Veg quarter did not plan progressive expiry: {expiry_tick.planned}')
            _assert(old_veg_at < old_veg_anchor_at < expiry_cutoff < recent_veg_at,
                    'Veg samples do not span the twelve-month cutoff and predecessor anchor')
            old_wire = await pool.fetchval(
                'SELECT record_send(m) FROM measurement m WHERE time=$1 AND sensor_id=5', old_veg_at)
            anchor_wire = await pool.fetchval(
                'SELECT record_send(m) FROM measurement m WHERE time=$1 AND sensor_id=5', old_veg_anchor_at)
            recent_wire = await pool.fetchval(
                'SELECT record_send(m) FROM measurement m WHERE time=$1 AND sensor_id=5', recent_veg_at)
            _assert(old_wire is not None and anchor_wire is not None and recent_wire is not None,
                    'Veg source rows were not seeded')
            veg_removed = await _finish(service.queue, veg_expiry_jobs[0].job_id, service.expire)
            _assert(veg_removed == 1, f'progressive Veg expiry removed {veg_removed} rows, expected one')
            _assert(await pool.fetchval(
                'SELECT count(*) FROM measurement WHERE time=$1 AND sensor_id=5', old_veg_at) == 0,
                'oldest Veg source row before the twelve-month cutoff remained')
            _assert(await pool.fetchval(
                'SELECT record_send(m) FROM measurement m WHERE time=$1 AND sensor_id=5', old_veg_anchor_at)
                == anchor_wire, 'latest pre-cutoff Veg operational anchor was removed or changed')
            _assert(await pool.fetchval(
                'SELECT record_send(m) FROM measurement m WHERE time=$1 AND sensor_id=5', recent_veg_at)
                == recent_wire, 'Veg source row newer than the cutoff changed or was removed')
            _assert(len({old_wire, anchor_wire, recent_wire}) == 3,
                    'Veg fixture wire payloads did not distinguish all rows')

            # Nongrow control history keeps operational anchors and the exact
            # exclusive cutoff row while deleting its eligible predecessor.
            control_name = f'monitoring_test_lifecycle_{uuid4().hex}'
            cutoff = retention_cutoffs(policy_now).local_expiry_before
            control_old, control_anchor = cutoff - timedelta(minutes=2), cutoff - timedelta(minutes=1)
            control_id = int(await pool.fetchval('SELECT COALESCE(max(id),0)+1 FROM control_history'))
            async with pool.acquire() as conn:
                streams = await service._expirable_streams(conn)
                rooms = await service._known_rooms(conn)
                for descriptor, _binding in streams:
                    for room in rooms:
                        cursor_time = (control_old if descriptor.name == 'control_history'
                                       and room == 'Flower Room' else cutoff)
                        await conn.execute(
                            "INSERT INTO archive_expiry_cursor(source_table,room,processed_before) "
                            "VALUES($1,$2,$3) ON CONFLICT(source_table,room) DO UPDATE "
                            "SET processed_before=EXCLUDED.processed_before,updated_at=clock_timestamp()",
                            descriptor.name, room, cursor_time)
                await conn.execute(
                    "INSERT INTO control_history(id,timestamp,location,cluster,device_name,channel,"
                    "old_state,new_state,mode,reason,sensor_value,setpoint,load_percent) "
                    "VALUES($1,$2,'Flower Room','main',$3,0,0,1,'auto','monitoring_test expiry',"
                    "21.0::real,22.0::real,0.0::real)", control_id, control_old, control_name)
                await conn.execute(
                    "INSERT INTO control_history(id,timestamp,location,cluster,device_name,channel,"
                    "old_state,new_state,mode,reason,sensor_value,setpoint,load_percent) "
                    "VALUES($1,$2,'Flower Room','main',$3,0,1,1,'auto','monitoring_test anchor',"
                    "21.0::real,22.0::real,0.0::real)", control_id + 1, control_anchor, control_name)
                await conn.execute(
                    "INSERT INTO control_history(id,timestamp,location,cluster,device_name,channel,"
                    "old_state,new_state,mode,reason,sensor_value,setpoint,load_percent) "
                    "VALUES($1,$2,'Flower Room','main',$3,0,1,0,'auto','monitoring_test exact cutoff',"
                    "21.0::real,22.0::real,0.0::real)", control_id + 2, cutoff, control_name)
                null_context_id = await conn.fetchval(
                    "INSERT INTO mode_transition_history(location,cluster,new_mode_id,triggered_by,triggered_at) "
                    "VALUES('Flower Room','main',1,'system',NULL) RETURNING id")
            anchor_wire = await pool.fetchval(
                'SELECT record_send(m) FROM control_history m WHERE id=$1', control_id + 1)
            exact_wire = await pool.fetchval(
                'SELECT record_send(m) FROM control_history m WHERE id=$1', control_id + 2)
            null_wire = await pool.fetchval(
                'SELECT record_send(m) FROM mode_transition_history m WHERE id=$1', null_context_id)
            config_wires = Counter(row['payload'] for row in await pool.fetch(
                'SELECT record_send(m) AS payload FROM config_versions m'))
            nongrow = await service.run_nongrow_expiry(policy_now)
            _assert(nongrow.deleted_rows >= 1, 'nongrow expiry deleted no eligible exact-cutoff predecessor')
            _assert(any(item.code == 'null_time_context_retained' for item in nongrow.held),
                    'NULL-time lifecycle context was not held')
            _assert(await pool.fetchval('SELECT count(*) FROM control_history WHERE id=$1', control_id) == 0,
                    'eligible control predecessor at the twelve-month cutoff remained')
            _assert(await pool.fetchval(
                'SELECT record_send(m) FROM control_history m WHERE id=$1', control_id + 1) == anchor_wire,
                'operational control anchor was removed or changed')
            _assert(await pool.fetchval(
                'SELECT record_send(m) FROM control_history m WHERE id=$1', control_id + 2) == exact_wire,
                'observation at the exclusive cutoff was removed or changed')
            _assert(await pool.fetchval(
                'SELECT record_send(m) FROM mode_transition_history m WHERE id=$1', null_context_id) == null_wire,
                'NULL-time operational context was removed or changed')
            _assert(Counter(row['payload'] for row in await pool.fetch(
                'SELECT record_send(m) AS payload FROM config_versions m')) == config_wires,
                'static configuration history changed during nongrow expiry')

            indexes_after = tuple(await pool.fetch(
                "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
                "AND tablename='automation_state' ORDER BY indexname"))
            _assert(indexes_after == indexes_before, 'lifecycle changed an automation_state index')
            print(json.dumps({'result': 'PASS', 'checks': [
                'six_automation_state_indexes_preserved', 'owned_real_redis_freshness',
                'loopback_verified_grow_export_kept_source_rows', 'preanniversary_expiry_hold',
                'anniversary_expiry_planned', 'missing_remote_zip_holds_source',
                'corrupt_remote_zip_holds_source', 'verified_zip_deletion_recorded',
                'history_guard_returns_409', 'derived_aggregate_refreshed',
                'progressive_veg_expiry_keeps_recent_source',
                'nongrow_exact_cutoff_and_operational_anchor',
                'null_time_context_and_static_configuration_preserved'],
                'grow_dataset': str(dataset_id), 'veg_dataset': str(veg_id)}, sort_keys=True))
    finally:
        publish_stop.set()
        if publisher is not None:
            await asyncio.gather(publisher, return_exceptions=True)
        if redis_client is not None:
            redis_client.close()
        if redis_reader is not None:
            await redis_reader.aclose()
        if redis_process is not None:
            redis_process.terminate()
            try:
                redis_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                redis_process.kill()
                redis_process.wait(timeout=5)
        await heavy.shutdown()
        await pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    asyncio.run(run(parser.parse_args().database_url))
