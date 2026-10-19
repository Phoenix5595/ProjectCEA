"""Owned PostgreSQL -> source-resident capture -> verified ZIP field proof."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from dav_fixture import loopback_dav
from smoke_lifecycle import _assert, _parse_database_url
import asyncpg
import httpx

from grow_archive_service.binary_codec import record_payload
from grow_archive_service.cache import ArchiveCache
from grow_archive_service.capture import SourceCapture
from grow_archive_service.exporter import ExportService
from grow_archive_service.execution import HeavyWorkExecutor
from grow_archive_service.membership import CaptureWindow, selection_sql, window_arrays
from grow_archive_service.parquet_codec import iter_archive_binary_rows
from grow_archive_service.pg_fields import decode
from grow_archive_service.schema import load_source_catalog
from grow_archive_service.webdav import WebDAVArtifactClient
from shared.nextcloud_dav import discover_nextcloud


async def run(database_url: str) -> None:
    _parse_database_url(database_url)
    _assert(os.getenv('MONITORING_HARNESS_INTERNAL') == '1', 'owned harness required')
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    heavy = HeavyWorkExecutor()
    heavy.control.allow_resume()
    grow_id, dataset_id = uuid4(), uuid4()
    start = datetime(2026, 1, 4, tzinfo=UTC)
    end = datetime(2026, 1, 6, tzinfo=UTC)
    full_rate_start = datetime(2026, 1, 4, 12, tzinfo=UTC)
    full_rate_end = full_rate_start + timedelta(hours=3)
    full_rate_rows = 3 * 60 * 60
    sparse_times = (
        datetime(2026, 1, 4, 22, 15, tzinfo=UTC),
        datetime(2026, 1, 5, 10, 15, tzinfo=UTC),
    )
    sparse_hours = {value.replace(minute=0, second=0, microsecond=0) for value in sparse_times}
    _assert(len(sparse_hours) == 2, 'sparse fixture rows must occupy separate UTC hours')
    try:
        async with pool.acquire() as conn:
            _assert(await conn.fetchval(
                "SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
                "WHERE fixture_name='grow-data-lifecycle'") == 1, 'missing fixture marker')
            crop_batch_id = await conn.fetchval(
                "INSERT INTO crop_batch(crop_name,start_date,end_date,room_id) "
                "VALUES('Owned source-resident lossless grow','2026-01-04','2026-01-06',1) "
                "RETURNING batch_id")
            await conn.execute(
                "INSERT INTO grow_lifecycle(grow_plan_id,crop_batch_id,state,original_drying_end,"
                "first_closed_at,closed_at) VALUES($1,$2,'closed','2026-01-05',clock_timestamp(),clock_timestamp())",
                grow_id, crop_batch_id)
            await conn.execute(
                "INSERT INTO archive_dataset(dataset_id,kind,grow_plan_id,room,available_from,available_to,"
                "anniversary_at) VALUES($1,'grow',$2,'Flower Room',$3,$4,'2027-01-06T05:00:00Z')",
                dataset_id, grow_id, start, end)
            await conn.execute(
                "INSERT INTO archive_window(dataset_id,calendar_revision,event_id,phase_type,room,start_at,end_at) "
                "VALUES($1,1,1000,'flower_bulk','Flower Room',$2,$3)", dataset_id, start, end)
            sensors = await conn.fetch(
                'SELECT s.sensor_id,rm.name AS room FROM sensor s JOIN device d USING(device_id) '
                'LEFT JOIN rack r USING(rack_id) LEFT JOIN room rm USING(room_id)')
            for sensor in sensors:
                await conn.execute(
                    "INSERT INTO archive_scope_override(source_table,source_identity,start_at,end_at,room,confirmed_at) "
                    "VALUES('measurement',jsonb_build_object('sensor_id',$1::integer),$2,$3,$4,clock_timestamp())",
                    sensor['sensor_id'], start, end, sensor['room'] or 'Flower Room')
            devices = await conn.fetch(
                'SELECT d.device_id,rm.name AS room FROM device d '
                'LEFT JOIN rack r USING(rack_id) LEFT JOIN room rm USING(room_id)')
            for device in devices:
                await conn.execute(
                    "INSERT INTO archive_scope_override(source_table,source_identity,start_at,end_at,room,confirmed_at) "
                    "VALUES('actuator_events',jsonb_build_object('device_id',$1::integer),$2,$3,$4,clock_timestamp())",
                    device['device_id'], start, end, device['room'] or 'Flower Room')

            flower_sensor = await conn.fetchrow(
                "SELECT s.sensor_id,s.name FROM sensor s JOIN device d USING(device_id) "
                "LEFT JOIN rack r USING(rack_id) LEFT JOIN room rm USING(room_id) "
                "WHERE rm.name='Flower Room' AND right(s.name,2) IN ('_f','_b') "
                "ORDER BY s.sensor_id LIMIT 1")
            _assert(flower_sensor is not None, 'Flower Room front/back sensor fixture missing')
            full_rate_sensor_id = flower_sensor['sensor_id']
            await conn.execute(
                "UPDATE automation_state SET active_rule_ids='[0:1][4:5]={{1,2},{3,4}}'::integer[] WHERE id=1")
            await conn.execute(
                "INSERT INTO mode_transition_history(location,cluster,new_mode_id,triggered_by,triggered_at) "
                "VALUES('Flower Room','main',1,'system',NULL)")
            await conn.execute(
                "INSERT INTO measurement(time,sensor_id,value,status) "
                "SELECT $1::timestamptz+ticks.i*interval '1 second',$2,"
                "CASE WHEN ticks.i%997=0 THEN '-0'::real ELSE ((ticks.i%201)-100)*0.125::real END,"
                "CASE WHEN ticks.i%5=0 THEN NULL ELSE 'ok' END "
                "FROM generate_series(0,$3-1) AS ticks(i)",
                full_rate_start, full_rate_sensor_id, full_rate_rows)
            await conn.executemany(
                "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,$2,$3,'warning')",
                [(sparse_times[0], full_rate_sensor_id, 18.75),
                 (sparse_times[1], full_rate_sensor_id, 19.25)],
            )
            await conn.execute(
                "INSERT INTO automation_state(timestamp,location,cluster,device_name,device_state,device_mode,"
                "pid_output,duty_cycle_percent,active_rule_ids,control_reason) "
                "SELECT $1::timestamptz+ticks.i*interval '1 second','Flower Room','main','heater_f_1',"
                "CASE WHEN ticks.i%2=0 THEN 1 ELSE 0 END,'auto',(ticks.i%201)::real,"
                "((ticks.i*7)%101)::real,CASE WHEN ticks.i%2=0 THEN ARRAY[1,2]::integer[] ELSE ARRAY[]::integer[] END,"
                "CASE WHEN ticks.i%2=0 THEN 'on' ELSE 'off' END "
                "FROM generate_series(0,$2-1) AS ticks(i)", full_rate_start, full_rate_rows)
            indices_before = await conn.fetch(
                "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
                "AND tablename='automation_state' ORDER BY indexname")

        windows = (CaptureWindow('Flower Room', start, end),)
        source: dict[str, Counter] = {}
        source_observations: dict[str, int] = {}
        oids: dict[str, tuple[int, ...]] = {}
        catalog = load_source_catalog()
        async with pool.acquire() as conn:
            for stream in catalog.defined_streams():
                predicate, role = selection_sql(stream)
                records = await conn.fetch(
                    f'SELECT record_send(m) AS payload,{role} AS role FROM public."{stream.name}" m '
                    f'WHERE {predicate}', *window_arrays(windows))
                if any(column.name == stream.time_column and not column.not_null
                       for column in stream.columns):
                    null_predicate, null_role = selection_sql(stream, include_null_time=True)
                    records += await conn.fetch(
                        f'SELECT record_send(m) AS payload,{null_role} AS role FROM public."{stream.name}" m '
                        f'WHERE {null_predicate}', ['Flower Room'])
                source[stream.name] = Counter((row['payload'], row['role']) for row in records)
                source_observations[stream.name] = sum(
                    1 for row in records if row['role'] == 'observation')
                query_count = await conn.fetchval(
                    f'SELECT count(*) FROM public."{stream.name}" m WHERE {predicate} AND ({role})=\'observation\'',
                    *window_arrays(windows))
                _assert(query_count == source_observations[stream.name],
                        f'source observation query disagrees with its binary bag: {stream.name}')
                attributes = await conn.fetch(
                    'SELECT atttypid::integer AS oid FROM pg_attribute WHERE attrelid=$1::regclass '
                    'AND attnum>0 AND NOT attisdropped ORDER BY attnum', stream.relation)
                oids[stream.name] = tuple(row['oid'] for row in attributes)
            _assert(source['mode_transition_history'] and
                    any(role == 'context' for _payload, role in source['mode_transition_history']),
                    'NULL-time context missing from source bag')

        os.environ['CALDAV_ALLOW_HTTP_TEST'] = 'true'
        with tempfile.TemporaryDirectory(prefix='grow-lossless-source-owned-') as directory, \
                loopback_dav() as (origin, _state):
            root = Path(directory)

            @asynccontextmanager
            async def dav_factory(instance_id=None):
                async with httpx.AsyncClient(
                    auth=httpx.BasicAuth('fixture', 'fixture-only-password'), trust_env=False,
                    follow_redirects=False, timeout=10,
                ) as client:
                    discovery = await discover_nextcloud(client, origin + '/dav')
                    if instance_id is not None:
                        _assert(discovery.instance_id == instance_id, 'fixture instance changed')
                    yield WebDAVArtifactClient(
                        discovery, client, instance_id=discovery.instance_id,
                        scratch_root=root / 'staging' / 'http')

            service = ExportService(
                pool, root, heavy, SourceCapture(pool, heavy), dav_factory=dav_factory,
                cache=ArchiveCache(root / 'cache', root / 'staging', reserve=0),
            )
            queue = service.queue
            job = await queue.enqueue(dataset_id, 'export', 1, required_closed=False)
            claim = await queue.claim(uuid4(), now=datetime.now(UTC), job_id=job.job_id)
            _assert(claim is not None, 'source export job was not claimable')
            try:
                artifact_id = await service.export_dataset(claim)
                await queue.succeed(claim)
            except BaseException as error:
                code = getattr(error, 'code', '')
                if code:
                    await queue.fail(claim, code)
                raise
            verified = await service.reverify_artifact(artifact_id)
            artifact = await pool.fetchrow(
                'SELECT local_path FROM archive_artifact WHERE artifact_id=$1', artifact_id)
            archive_path = Path(artifact['local_path'])
            _assert(archive_path.is_file(), 'verified export did not retain a local ZIP copy')

            decoded_observations: dict[str, int] = {}
            for stream in catalog.defined_streams():
                decoded: Counter = Counter()
                observation_rows = 0
                for batch in iter_archive_binary_rows(archive_path, verified, stream.name):
                    for binary in batch:
                        original = binary[:len(stream.columns)]
                        record_role = binary[len(stream.columns) + 3].decode()
                        decoded[(record_payload(original, oids[stream.name]), record_role)] += 1
                        observation_rows += record_role == 'observation'
                _assert(decoded == source[stream.name],
                        f'ZIP original binary fields or multiplicities changed: {stream.name}')
                _assert(observation_rows == source_observations[stream.name],
                        f'ZIP observation count differs from source window query: {stream.name}')
                _assert(verified.data['observation_counts'].get(stream.name, 0) == observation_rows,
                        f'manifest observation count differs from decoded ZIP rows: {stream.name}')
                decoded_observations[stream.name] = observation_rows

            entries = [entry for entry in verified.data['members']
                       if entry.get('record_role') == 'observation']
            _assert(all(entry.get('window_start') is not None and entry.get('window_end') is not None
                        for entry in entries), 'observation members omitted their source time windows')
            for stream_name in ('measurement', 'automation_state'):
                stream_entries = [entry for entry in entries if entry.get('source_table') == stream_name]
                exact_span_entries = [entry for entry in stream_entries
                                      if datetime.fromisoformat(entry['window_start'].replace('Z', '+00:00')) >= full_rate_start
                                      and datetime.fromisoformat(entry['window_end'].replace('Z', '+00:00')) <= full_rate_end]
                _assert(sum(entry['row_count'] for entry in exact_span_entries) == full_rate_rows,
                        f'raw/{stream_name} members did not preserve every full-rate tick')

            measurement = catalog.by_name()['measurement']
            measurement_time = next(i for i, col in enumerate(measurement.columns)
                                    if col.name == measurement.time_column)
            measurement_sensor = next(i for i, col in enumerate(measurement.columns)
                                      if col.name == 'sensor_id')
            measurement_in_span = 0
            sparse_hits = set()
            for batch in iter_archive_binary_rows(archive_path, verified, 'measurement'):
                for binary in batch:
                    fields = binary[:len(measurement.columns)]
                    record_role = binary[len(measurement.columns) + 3].decode()
                    if record_role == 'observation':
                        observed_at = decode(fields[measurement_time], measurement.columns[measurement_time].type)
                        sensor_id = decode(fields[measurement_sensor], measurement.columns[measurement_sensor].type)
                        if sensor_id == full_rate_sensor_id and observed_at in sparse_times:
                            sparse_hits.add(observed_at)
                        if sensor_id == full_rate_sensor_id and full_rate_start <= observed_at < full_rate_end:
                            measurement_in_span += 1
            _assert(measurement_in_span == full_rate_rows,
                    'ZIP measurement stream bucketed or lost per-second source rows')
            _assert(sparse_hits == set(sparse_times),
                    'source capture skipped sparse rows in separated hours')

            automation = catalog.by_name()['automation_state']
            automation_time = next(i for i, col in enumerate(automation.columns)
                                   if col.name == automation.time_column)
            device_state = next(i for i, col in enumerate(automation.columns) if col.name == 'device_state')
            pid_output = next(i for i, col in enumerate(automation.columns) if col.name == 'pid_output')
            duty_cycle = next(i for i, col in enumerate(automation.columns) if col.name == 'duty_cycle_percent')
            automation_in_span = 0
            states, pid_values, duty_values = set(), set(), set()
            for batch in iter_archive_binary_rows(archive_path, verified, 'automation_state'):
                for binary in batch:
                    fields = binary[:len(automation.columns)]
                    record_role = binary[len(automation.columns) + 3].decode()
                    if record_role == 'observation':
                        observed_at = decode(fields[automation_time], automation.columns[automation_time].type)
                        if full_rate_start <= observed_at < full_rate_end:
                            automation_in_span += 1
                            states.add(decode(fields[device_state], automation.columns[device_state].type))
                            pid_values.add(decode(fields[pid_output], automation.columns[pid_output].type))
                            duty_values.add(decode(fields[duty_cycle], automation.columns[duty_cycle].type))
            _assert(automation_in_span == full_rate_rows,
                    'ZIP automation_state stream omitted per-tick control history')
            _assert(states == {0, 1}, 'automation_state per-tick on/off states were not retained')
            _assert(None not in pid_values and len(pid_values) > 1,
                    'automation_state per-tick pid_output values were not retained')
            _assert(None not in duty_values and len(duty_values) > 1,
                    'automation_state per-tick duty_cycle_percent values were not retained')

            _assert('mode_transition_history' in verified.data['context_counts'],
                    'NULL-time context missing from ZIP')
            _assert(verified.data['absent_streams'] == [
                'light_state_history', 'pid_adjustments', 'setpoint_history', 'rollout_events'],
                'optional absence misrepresented')
            async with pool.acquire() as conn:
                indices_after = await conn.fetch(
                    "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename='automation_state' ORDER BY indexname")
            _assert(indices_after == indices_before, 'automation_state indexes changed')

            from smoke_archive_graphs import assert_graph_reader_parity
            await assert_graph_reader_parity(pool, heavy, root, dataset_id, start, end,
                                             archive_path, verified)
            print(
                f'Source-resident capture preserved all {len(catalog.defined_streams())} defined streams: '
                f'SHA256={verified.sha256}, ZIPbytes={verified.byte_size}, '
                f'observations={sum(decoded_observations.values())}, '
                f'full-rate measurement={measurement_in_span}, sparse rows={len(sparse_hits)} across {len(sparse_hours)} hours, '
                'NULL-time context, arrays, signed zero and six automation indexes unchanged')
    finally:
        await heavy.shutdown()
        await pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    asyncio.run(run(parser.parse_args().database_url))
