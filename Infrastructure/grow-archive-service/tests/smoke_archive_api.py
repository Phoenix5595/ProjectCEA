"""Actual archive HTTP/dispatcher/Redis/DAV fixture proof, without mocked handlers."""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4
from typing import get_args

import httpx

from cryptography.fernet import Fernet
from smoke_lifecycle import run, _assert
from dav_fixture import loopback_dav
from grow_archive_service.lifecycle_calendar import quarter_bounds
from grow_archive_service.api_models import LocalTier
from grow_archive_service.parquet_codec import verify_archive


async def scenario(calendar, archives, pool, _sink, _database):
    os.environ['CALDAV_ALLOW_HTTP_TEST'] = 'true'
    _assert(set(get_args(LocalTier)) == {'hot', 'zip_cache', 'remote_zip', 'none'},
            'archive API local-tier vocabulary changed')
    os.environ['CALENDAR_SYNC_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
    with loopback_dav() as (origin, state):
        saved = await calendar.post('/api/calendar/sync/connections', json={
            'caldav_base_url': origin + '/dav/', 'username': 'fixture',
            'app_password': 'fixture-only-password',
            'target_calendar_url': origin + '/dav/calendars/fixture/culture/',
        })
        _assert(saved.status_code == 200, saved.text)
        connection = saved.json()
        connected = await calendar.post(f"/api/calendar/sync/connections/{connection['id']}/reconnect", json={
            'expected_generation': connection['generation'], 'backup_confirmed': True,
        })
        _assert(connected.status_code == 200 and connected.json()['sync']['ok'], connected.text)
        dataset, older = uuid4(), uuid4()
        start, end = quarter_bounds(2025, 2)
        old_start, old_end = quarter_bounds(2025, 1)
        for identity, quarter, lower, upper in ((dataset, 2, start, end), (older, 1, old_start, old_end)):
            await pool.execute(
                "INSERT INTO archive_dataset(dataset_id,kind,calendar_year,quarter,room,available_from,available_to) "
                "VALUES($1,'veg_quarter',2025,$2,'Veg Room',$3,$4)", identity, quarter, lower, upper,
            )
        for sensor in (5, 6):
            await pool.execute(
                "INSERT INTO archive_scope_override(source_table,source_identity,room,start_at,end_at) "
                "VALUES('measurement',jsonb_build_object('sensor_id',$1::integer),'Veg Room',$2,$3)",
                sensor, start, end,
            )
            for offset in range(4):
                await pool.execute(
                    "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,$2,$3,'ok')",
                    start + timedelta(days=1, minutes=offset), sensor, offset * 0.25,
                )
        source_before = await pool.fetch(
            'SELECT encode(record_send(m),\'hex\') AS payload FROM measurement m ORDER BY payload')
        network_before = state.counters.copy()
        first = await archives.get('/api/archives', params={'room': 'Veg Room', 'limit': 1})
        _assert(first.status_code == 200, first.text)
        _assert(first.json()['items'][0]['dataset_id'] == str(dataset), 'catalog order is not newest first')
        cursor = first.json()['next_cursor']
        _assert(cursor is not None, 'catalog did not provide a continuation for older entry')
        second = await archives.get('/api/archives', params={'room': 'Veg Room', 'limit': 1, 'cursor': cursor})
        _assert(second.status_code == 200 and second.json()['items'][0]['dataset_id'] == str(older), second.text)
        detail = await archives.get(f'/api/archives/{dataset}')
        _assert(detail.status_code == 200 and detail.json()['published_revision'] is None, detail.text)
        _assert(detail.json()['local_tier'] == 'hot',
                f'unexported source-resident archive has the wrong tier: {detail.json()["local_tier"]}')
        _assert(state.counters == network_before, 'ordinary catalog/detail GET performed remote IO')
        _assert(await pool.fetchval('SELECT count(*) FROM archive_load') == 0, 'catalog GET selected an archive')

        bad_cursor = await archives.get('/api/archives', params={'room': 'Veg Room', 'cursor': 'not-a-cursor'})
        _assert(bad_cursor.status_code == 400
                and bad_cursor.json()['detail']['code'] == 'archive_cursor_invalid', bad_cursor.text)
        unknown_room = await archives.get('/api/archives', params={'room': 'Attic'})
        _assert(unknown_room.status_code == 400
                and unknown_room.json()['detail']['code'] == 'archive_room_invalid', unknown_room.text)
        over_limit = await archives.get('/api/archives', params={'room': 'Veg Room', 'limit': 51})
        _assert(over_limit.status_code == 422, over_limit.text)
        missing = await archives.get(f'/api/archives/{uuid4()}')
        _assert(missing.status_code == 404
                and missing.json()['detail']['code'] == 'archive_not_found', missing.text)
        async with httpx.AsyncClient(base_url=str(archives.base_url), trust_env=False, timeout=10) as unauth:
            denied = await unauth.get('/api/archives', params={'room': 'Veg Room'})
            _assert(denied.status_code == 401, 'archive catalog accepted a missing API key')
        for route, revision, code in (
            ('exports', 2, 'archive_revision_conflict'),
            ('loads', 9, 'archive_revision_conflict'),
            ('loads', 1, 'archive_export_required'),
        ):
            rejected = await archives.post(f'/api/archives/{dataset}/{route}',
                                           json={'expected_revision': revision})
            _assert(rejected.status_code == 409 and rejected.json()['detail']['code'] == code,
                    rejected.text)
        unknown_retry = await archives.post(f'/api/archives/jobs/{uuid4()}/retry')
        _assert(unknown_retry.status_code == 404
                and unknown_retry.json()['detail']['code'] == 'archive_job_not_found', unknown_retry.text)

        # Exercise keyset continuation under an actual equal-time tie as well.
        await pool.execute('UPDATE archive_dataset SET available_from=$2,available_to=$3 '
                           'WHERE dataset_id=$1', older, start, end)
        seen = []
        tie_cursor = None
        for _ in range(3):
            params = {'room': 'Veg Room', 'limit': 1}
            if tie_cursor is not None:
                params['cursor'] = tie_cursor
            tie_page = await archives.get('/api/archives', params=params)
            _assert(tie_page.status_code == 200, tie_page.text)
            seen.extend(item['dataset_id'] for item in tie_page.json()['items'])
            tie_cursor = tie_page.json()['next_cursor']
            if tie_cursor is None:
                break
        _assert(sorted(seen) == sorted((str(dataset), str(older))) and tie_cursor is None,
                f'equal-time catalog pagination lost or repeated a dataset: {seen}')

        async def wait_job(job_id):
            for _ in range(200):
                response = await archives.get(f'/api/archives/jobs/{job_id}')
                _assert(response.status_code == 200, response.text)
                job = response.json()
                public_job = json.dumps(job)
                _assert(not any(private in public_job for private in (
                    'lease_token', 'lease_owner', 'fixture-only-password',
                    os.environ['CALENDAR_SYNC_ENCRYPTION_KEY'],
                )), 'private claim or credentials escaped the job API')
                if job['state'] == 'succeeded':
                    return job
                _assert(job['state'] not in ('blocked', 'failed'), f"job held: {job['last_error_code']}")
                await asyncio.sleep(0.1)
            raise AssertionError('owned archive job did not complete')

        export = await archives.post(f'/api/archives/{dataset}/exports', json={'expected_revision': 1})
        _assert(export.status_code == 202, export.text)
        await wait_job(export.json()['job_id'])
        published = await archives.get(f'/api/archives/{dataset}')
        _assert(published.status_code == 200, published.text)
        _assert(published.json()['published_revision'] == 1 and published.json()['state'] == 'ready',
                'explicit disabled-lifecycle export did not publish a source-resident ZIP revision')
        _assert(published.json()['local_tier'] == 'hot',
                f'exported source-resident archive has the wrong tier: {published.json()["local_tier"]}')
        _assert(await pool.fetchval('SELECT count(*) FROM archive_load') == 0, 'export auto-selected history')
        graph_params = {'start': (start + timedelta(days=1)).isoformat(),
                        'end': (start + timedelta(days=1, minutes=5)).isoformat(), 'max_points': 10}
        unselected = await archives.get(f'/api/archives/{dataset}/graph', params=graph_params)
        _assert(unselected.status_code == 422, 'unselected graph silently auto-loaded an archive')
        loaded = await archives.post(f'/api/archives/{dataset}/loads', json={'expected_revision': 1})
        _assert(loaded.status_code in (200, 202), loaded.text)
        if loaded.status_code == 202:
            ready = (await wait_job(loaded.json()['job_id']))['progress']
        else:
            ready = loaded.json()
        _assert(ready['source_kind'] == 'zip', 'Historical API load did not use a verified ZIP')
        load_id = ready['load_id']
        graph = await archives.get(f'/api/archives/{dataset}/graph', params={**graph_params, 'load_id': load_id})
        _assert(graph.status_code == 200, graph.text)
        _assert(sum(item['sample_count'] for item in graph.json()['sensors']['statistics']) == 8,
                'explicit HTTP graph lost or duplicated source observations')
        _assert(not {'pid', 'relay'}.intersection(graph.json()), 'archive graph exposed unrelated control series')
        download = await archives.get(f'/api/archives/{dataset}/download', params={'load_id': load_id})
        _assert(download.status_code == 200 and download.headers['content-type'] == 'application/zip',
                f'download failed: {download.status_code}')
        with tempfile.TemporaryDirectory(prefix='archive-http-download-') as directory:
            path = Path(directory) / 'download.zip'
            path.write_bytes(download.content)
            verified = await asyncio.to_thread(verify_archive, path)
            _assert(verified.data['observation_counts']['measurement'] == 8, 'HTTP ML ZIP count differs')
        disposition = download.headers.get('content-disposition', '')
        _assert(disposition.startswith('attachment; filename="') and disposition.endswith('.zip"')
                and '\r' not in disposition and '\n' not in disposition,
                f'unsafe ML download filename header: {disposition!r}')
        retry_succeeded = await archives.post(f"/api/archives/jobs/{export.json()['job_id']}/retry")
        _assert(retry_succeeded.status_code == 409
                and retry_succeeded.json()['detail']['code'] == 'archive_job_retry_not_allowed',
                retry_succeeded.text)
        released = await archives.delete(f'/api/archives/loads/{load_id}')
        _assert(released.status_code == 204, released.text)
        stale = await archives.get(f'/api/archives/{dataset}/graph', params={**graph_params, 'load_id': load_id})
        _assert(stale.status_code == 409 and stale.json()['detail']['code'] == 'archive_load_required', stale.text)
        released_download = await archives.get(f'/api/archives/{dataset}/download', params={'load_id': load_id})
        _assert(released_download.status_code == 409
                and released_download.json()['detail']['code'] == 'archive_load_required',
                released_download.text)
        # The running service, not this test, must sweep idle selections even
        # with automatic source retention disabled. Scale the file bound below
        # the real ZIP so the durable selection owns an oversized staging file.
        again = await archives.post(f'/api/archives/{dataset}/loads', json={'expected_revision': 1})
        _assert(again.status_code in (200, 202), again.text)
        idle_ready = ((await wait_job(again.json()['job_id']))['progress']
                      if again.status_code == 202 else again.json())
        idle_id = idle_ready['load_id']
        staging = await pool.fetchval(
            'SELECT staging_path FROM archive_load WHERE load_id=$1::text::uuid', idle_id)
        _assert(staging is not None and Path(staging).is_file(), 'fixture did not create owned staging')
        await pool.execute(
            "UPDATE archive_load SET expires_at=now()-interval '1 second' WHERE load_id=$1::text::uuid", idle_id)
        for _ in range(350):
            if not await pool.fetchval(
                'SELECT EXISTS(SELECT 1 FROM archive_load WHERE load_id=$1::text::uuid)', idle_id):
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError('running service never swept its expired archive load')
        _assert(not Path(staging).exists(), 'idle sweep leaked oversized staging')
        expired_graph = await archives.get(
            f'/api/archives/{dataset}/graph', params={**graph_params, 'load_id': idle_id})
        _assert(expired_graph.status_code == 409
                and expired_graph.json()['detail']['code'] == 'archive_load_required', expired_graph.text)
        source_after = await pool.fetch(
            'SELECT encode(record_send(m),\'hex\') AS payload FROM measurement m ORDER BY payload')
        _assert(source_after == source_before, 'disabled lifecycle changed original source bytes')
        print('Actual archive HTTP proof: local-only paginated catalog; encrypted DAV credentials; '
              'freshness-gated queued export/load; source-resident ZIP publish; explicit graph/ML download; '
              'unload; production idle sweep; raw preserved')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    os.environ['ARCHIVE_LOCAL_FILE_LIMIT_BYTES'] = '1'
    asyncio.run(run(parser.parse_args().database_url, scenario=scenario))
