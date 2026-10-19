"""Real marked-DB/native-HTTP proof of removed-history isolation and MVCC races."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

from smoke_lifecycle import _assert, _owned_socket, _parse_database_url, _start_server, _stop_server
import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from monitoring_service.database import ReadOnlyDatabase
from monitoring_service.sensor_models import MonitoringRange
from monitoring_service.sensor_repository import SensorMonitoringRepository
from monitoring_service.sensor_routes import get_sensor_reads, router
from shared.history_availability import local_history_scope, require_local_history


async def run(database_url: str) -> None:
    _parse_database_url(database_url)
    if os.getenv('MONITORING_HARNESS_INTERNAL') != '1':
        raise RuntimeError('history smoke requires the owned disposable harness')
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)
    server = task = None
    read_conn = None
    old_start = datetime(2026, 1, 5, tzinfo=UTC)
    old_end = old_start + timedelta(hours=1)
    dataset = uuid4()
    try:
        async with pool.acquire() as conn:
            _assert(await conn.fetchval("SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata WHERE fixture_name='grow-data-lifecycle'") == 1, 'missing owned DB marker')
            _assert(not await conn.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user'), 'fixture role must not be superuser')
            await conn.execute("INSERT INTO archive_dataset(dataset_id,kind,calendar_year,quarter,room,available_from,available_to) VALUES($1,'veg_quarter',2099,1,'Flower Room',$2,$3)", dataset, old_start, old_end)
            await conn.execute("INSERT INTO archive_availability(dataset_id,revision,source_table,room,start_at,end_at,state) VALUES($1,1,'measurement','Flower Room',$2,$3,'captured')", dataset, old_start, old_end)
        database = ReadOnlyDatabase(pool)
        # The exercised historical route never reads Redis or starts a control service.
        reads = SensorMonitoringRepository(database, None)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_sensor_reads] = lambda: reads
        sock, port = _owned_socket()
        server, task = await _start_server(app, sock)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
            params = {'start': old_start.isoformat(), 'end': old_end.isoformat()}
            response = await client.get('/api/sensors/monitoring/range/Flower Room', params=params)
            _assert(response.status_code == 200, f'captured-but-not-removed range failed: {response.text}')
            before = response.json()
            _assert(any(series['points'] for series in before['series']), 'fixture must have original Flower records')
            read_conn = await asyncpg.connect(database_url)
            snapshot = read_conn.transaction(isolation='repeatable_read', readonly=True)
            await snapshot.start()
            await require_local_history(read_conn, room='Flower Room', start=old_start, end=old_end, source_tables=('measurement',))
            source_before = await read_conn.fetch('SELECT record_send(m) AS payload FROM measurement m WHERE sensor_id=1 AND time>=$1 AND time<$2', old_start, old_end)
            async with pool.acquire() as conn, conn.transaction():
                removed = await conn.fetch('DELETE FROM measurement WHERE sensor_id=1 AND time>=$1 AND time<$2 RETURNING time', old_start, old_end)
                _assert(len(removed) == len(source_before), 'owned source removal count mismatch')
                await conn.execute(
                    "UPDATE archive_availability SET source_rows_removed=true,"
                    "source_removed_at=clock_timestamp() WHERE dataset_id=$1 AND revision=1",
                    dataset,
                )
            # An in-flight reader keeps its complete pre-deletion snapshot.
            await require_local_history(read_conn, room='Flower Room', start=old_start, end=old_end, source_tables=('measurement',))
            old_visible = await read_conn.fetch('SELECT record_send(m) AS payload FROM measurement m WHERE sensor_id=1 AND time>=$1 AND time<$2', old_start, old_end)
            _assert(Counter(row['payload'] for row in old_visible) == Counter(row['payload'] for row in source_before), 'in-flight reader became partial')
            await snapshot.commit()
            for path in ('range', 'stats'):
                response = await client.get(f'/api/sensors/monitoring/{path}/Flower Room', params=params)
                _assert(response.status_code == 409 and response.json()['detail'] == {'code':'archive_selection_required','dataset_ids':[str(dataset)]}, f'{path} returned silently partial history: {response.text}')
            response = await client.get('/api/sensors/monitoring/overview/Flower Room', params={'start': old_start.isoformat(), 'end': (old_start+timedelta(days=30)).isoformat()})
            _assert(response.status_code == 409, f'overview bypassed removed-history guard: {response.text}')
            # Direct native repository statistics share the ReadOnlyDatabase
            # guard: the scoped fetch must raise 409, not silently serve
            # partially removed history.
            async def _native_statistics(room: str) -> int:
                try:
                    await reads.statistics(room, MonitoringRange(start=old_start, end=old_end))
                except HTTPException as error:
                    return error.status_code
                return 200
            _assert(await _native_statistics('Flower Room') == 409, 'native repository statistics served partially removed history')
            response = await client.get('/api/sensors/monitoring/range/Veg Room', params=params)
            _assert(response.status_code == 200 and any(s['points'] for s in response.json()['series']), 'other-room original observations lost')
            hot_end = datetime.now(UTC)
            hot_start = hot_end - timedelta(hours=1)
            async with pool.acquire() as conn:
                await conn.execute('INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,1,32.125,NULL)', hot_end-timedelta(seconds=2))
            response = await client.get('/api/sensors/monitoring/range/Flower Room', params={'start':hot_start.isoformat(),'end':hot_end.isoformat()})
            _assert(response.status_code == 200 and any(point['average'] == 32.125 for s in response.json()['series'] for point in s['points']), 'operational hot range blocked or changed')
            response = await client.get('/api/sensors/monitoring/range/Flower Room', params={'start':old_end.isoformat(),'end':(old_end+timedelta(hours=1)).isoformat()})
            _assert(response.status_code == 200, 'half-open touching interval incorrectly blocked')
            async with pool.acquire() as conn, conn.transaction():
                deleted = await conn.fetch('DELETE FROM measurement WHERE sensor_id=5 AND time>=$1 AND time<$2 RETURNING time', old_start, old_end)
                _assert(len(deleted) == 1, 'nongrow expiry fixture requires one actual removed observation')
                await conn.execute("INSERT INTO archive_availability(source_table,room,start_at,end_at,state,source_rows_removed,source_removed_at) VALUES('measurement','Veg Room',$1,$2,'expired',true,clock_timestamp())", old_start, old_end)
            response = await client.get('/api/sensors/monitoring/range/Veg Room', params=params)
            _assert(response.status_code == 410 and response.json()['detail']['code'] == 'history_expired', 'expired history returned empty success')
            async def scoped(room, sources):
                async with local_history_scope(room, old_start, old_end, sources):
                    try:
                        await database.fetch('SELECT 1')
                        return 200
                    except HTTPException as error:
                        return error.status_code
            _assert(await asyncio.gather(scoped('Flower Room',('measurement',)), scoped('Flower Room',('automation_state',))) == [409,200], 'concurrent scoped reads leaked room/source scope')
            _assert(await database.fetch('SELECT 1'), 'scope was not reset after guarded failure')
            # Native repository path must classify expired history as 410,
            # mirroring the HTTP range response above.
            async def _expired_native_statistics() -> int:
                try:
                    await reads.statistics('Veg Room', MonitoringRange(start=old_start, end=old_end))
                except HTTPException as error:
                    return error.status_code
                return 200
            _assert(await _expired_native_statistics() == 410, 'native repository statistics served expired history')
        print('Actual native HTTP range/stats/overview 409, expired410, hot/other-room/boundary200 and complete in-flight MVCC snapshot proofs passed')
    finally:
        await _stop_server(server, task)
        if read_conn is not None:
            await read_conn.close()
        await pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    asyncio.run(run(parser.parse_args().database_url))
