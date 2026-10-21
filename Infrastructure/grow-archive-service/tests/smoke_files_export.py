"""Guarded real DAV/export/load/cache/annual proof; no production targets."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from smoke_lifecycle import _assert, _parse_database_url
from dav_fixture import loopback_dav
import asyncpg
import httpx

from grow_archive_service.binary_codec import record_payload
from grow_archive_service.cache import ArchiveCache, CacheLimits
from grow_archive_service.capture import SourceCapture
from grow_archive_service.execution import HeavyWorkExecutor
from grow_archive_service.exporter import ExportService
from grow_archive_service.graphs import ArchiveGraphReader
from grow_archive_service.lifecycle_calendar import quarter_bounds
from grow_archive_service.models import ArchiveError
from grow_archive_service.parquet_codec import iter_archive_binary_rows, verify_archive
from grow_archive_service.schema import load_source_catalog
from grow_archive_service.repository import ArchiveRepository
from grow_archive_service.webdav import ArtifactName, WebDAVArtifactClient
from shared.nextcloud_dav import discover_nextcloud


async def expect_error(code, action):
    try:
        await action()
    except ArchiveError as error:
        _assert(error.code == code, f'expected {code}; got {error.code}')
    else:
        raise AssertionError(f'expected {code}')


async def source_bags(pool):
    async with pool.acquire() as conn:
        return {stream.name: Counter(row['payload'] for row in await conn.fetch(
            f'SELECT record_send(m) AS payload FROM public."{stream.name}" m'))
                for stream in load_source_catalog().defined_streams()}


async def run(database_url):
    _parse_database_url(database_url)
    _assert(os.getenv('MONITORING_HARNESS_INTERNAL') == '1', 'owned harness marker required')
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2, command_timeout=5)
    heavy = HeavyWorkExecutor()
    heavy.control.allow_resume()
    try:
        _assert(await pool.fetchval("SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
                                    "WHERE fixture_name='grow-data-lifecycle'") == 1, 'fixture marker missing')
        os.environ['CALDAV_ALLOW_HTTP_TEST'] = 'true'
        with tempfile.TemporaryDirectory(prefix='grow-files-owned-') as directory, loopback_dav() as (origin, state):
            root = Path(directory)

            @asynccontextmanager
            async def dav_factory(instance_id=None):
                async with httpx.AsyncClient(auth=('fixture', 'fixture-only-password'), trust_env=False,
                                             follow_redirects=False, timeout=10) as client:
                    discovery = await discover_nextcloud(client, origin + '/dav')
                    if instance_id is not None:
                        _assert(discovery.instance_id == instance_id, 'fixture instance changed')
                    yield WebDAVArtifactClient(discovery, client, instance_id=discovery.instance_id,
                                               scratch_root=root / 'staging' / 'http')

            cache = ArchiveCache(root / 'cache', root / 'staging')
            service = ExportService(pool, root, heavy, SourceCapture(pool, heavy),
                                    dav_factory=dav_factory, cache=cache)
            queue = service.queue

            async def finish(job_id, handler):
                claim = await queue.claim(uuid4(), now=datetime.now(UTC), job_id=job_id)
                _assert(claim is not None, 'target fixture job was not claimable')
                stop_renewal = asyncio.Event()

                async def renew_claim():
                    while True:
                        try:
                            await asyncio.wait_for(stop_renewal.wait(), timeout=20)
                        except TimeoutError:
                            await queue.renew(claim)
                        else:
                            return

                renewal = asyncio.create_task(renew_claim())
                try:
                    result = await handler(claim)
                except ArchiveError as error:
                    stop_renewal.set()
                    await renewal
                    await queue.fail(claim, error.code)
                    raise
                except BaseException:
                    stop_renewal.set()
                    await renewal
                    raise
                stop_renewal.set()
                await renewal
                await queue.succeed(claim)
                return result

            async def export(dataset):
                job = await queue.enqueue(dataset, 'export', 1, required_closed=False)
                return await finish(job.job_id, service.export_dataset)

            async def load(dataset):
                request = await service.enqueue_load(dataset, 1)
                if request.load is not None:
                    pin = request.load
                else:
                    _assert(request.job_id is not None, 'load returned neither selection nor job')
                    pin = await finish(request.job_id, service.run_load)
                _assert(pin.source_kind == 'zip', 'Historical load did not select a verified ZIP')
                return pin

            datasets = {}
            for quarter in (1, 2, 3, 4):
                start, end = quarter_bounds(2025, quarter)
                dataset = uuid4()
                datasets[quarter] = dataset
                await pool.execute(
                    "INSERT INTO archive_dataset(dataset_id,kind,calendar_year,quarter,room,available_from,available_to) "
                    "VALUES($1,'veg_quarter',2025,$2,'Veg Room',$3,$4)", dataset, quarter, start, end,
                )
                # These two identities are declared Veg sensors in the owned SQL fixture.
                for sensor in (5, 6):
                    await pool.execute(
                        "INSERT INTO archive_scope_override(source_table,source_identity,room,start_at,end_at) "
                        "VALUES('measurement',jsonb_build_object('sensor_id',$1::integer),'Veg Room',$2,$3)",
                        sensor, start, end,
                    )
                    for offset, value in enumerate((-0.0, 1.5, 2.5, 3.5)):
                        await pool.execute(
                            "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,$2,$3,'ok')",
                            start + timedelta(days=1, minutes=offset), sensor, value,
                        )
            before_source = await source_bags(pool)
            await expect_error('archive_export_required',
                               lambda: service.enqueue_load(datasets[4], 1))
            artifacts = {}
            artifacts[1] = await export(datasets[1])
            artifacts[2] = await export(datasets[2])
            state.drop_final_ack = True
            artifacts[3] = await export(datasets[3])
            _assert(not state.drop_final_ack, 'fixture did not exercise accepted MOVE with lost acknowledgement')
            state.quota = True
            await expect_error('archive_remote_quota', lambda: export(datasets[4]))
            _assert(await source_bags(pool) == before_source, 'quota failure changed original fields')
            state.quota = False
            await pool.execute(
                "UPDATE archive_job SET next_attempt_at=clock_timestamp() WHERE dataset_id=$1 "
                "AND kind='export' AND state='pending'", datasets[4],
            )
            artifacts[4] = await export(datasets[4])
            for dataset in datasets.values():
                entry = await ArchiveRepository(pool).entry(dataset)
                _assert(entry['local_tier'] == 'hot',
                        f'source-resident dataset has incorrect local tier: {entry["local_tier"]}')
            rows = {q: dict(await pool.fetchrow('SELECT * FROM archive_artifact WHERE artifact_id=$1', artifact))
                    for q, artifact in artifacts.items()}
            for quarter, artifact in artifacts.items():
                verified = await service.reverify_artifact(artifact)
                _assert(verified.data['observation_counts']['measurement'] == 8, 'export lost/duplicated observations')
                _assert(verified.sha256 == rows[quarter]['sha256'], 'remote SHA differs from catalog')
                _assert(verified.byte_size == rows[quarter]['byte_size'], 'remote byte size differs from catalog')
            put_count, move_count = state.counters.get('PUT', 0), state.counters.get('MOVE', 0)
            reused = await service.request_export(datasets[2], 1)
            _assert(reused.artifact_id == artifacts[2] and reused.job_id is None, 'verified export was not reused')
            _assert((state.counters.get('PUT', 0), state.counters.get('MOVE', 0)) == (put_count, move_count),
                    'reusing an immutable verified artifact uploaded again')
            bad_path = '/dav/files/fixture/' + rows[2]['remote_path']
            original_remote = state.files[bad_path]
            state.files[bad_path] = original_remote + b'corrupt'
            await expect_error('archive_integrity_failed', lambda: service.reverify_artifact(artifacts[2]))
            _assert(await source_bags(pool) == before_source, 'corrupt remote verification changed source data')
            state.files[bad_path] = original_remote
            state.auth_failure = True
            await expect_error('archive_remote_unavailable', lambda: service.reverify_artifact(artifacts[2]))
            state.auth_failure = False

            # Borrowing an already cached input must hold the same eviction
            # protection as a freshly downloaded input, including offline use.
            source_dataset = dict(await pool.fetchrow(
                'SELECT * FROM archive_dataset WHERE dataset_id=$1', datasets[1]))
            state.auth_failure = True
            prior = await service.prior_archive_path(source_dataset)
            _assert(prior.component_dataset_id is None,
                    'standalone quarter prior unexpectedly carries an annual component id')
            borrowed = prior.path
            state.auth_failure = False
            cache.limits = CacheLimits(file_bytes=max(row['byte_size'] for row in rows.values()), total_bytes=1)
            async with pool.acquire() as conn:
                await cache.evict_unpinned(conn)
            borrowed_manifest = await asyncio.to_thread(verify_archive, borrowed, rows[1]['sha256'])
            _assert(borrowed_manifest.dataset_id == str(datasets[1]), 'pressure removed an in-flight cached input')
            await service.release_prior_archive(borrowed)

            # Clear unselected persistent copies through real LRU pressure, not filesystem fakes.
            cache.limits = CacheLimits(file_bytes=max(row['byte_size'] for row in rows.values()), total_bytes=1)
            async with pool.acquire() as conn:
                await cache.evict_unpinned(conn)
            first_size, second_size = rows[1]['byte_size'], rows[2]['byte_size']
            cache.limits = CacheLimits(file_bytes=first_size, total_bytes=first_size)
            first_pin = await load(datasets[1])
            _assert(cache.owns_cache_path(first_pin.local_path), 'inclusive per-file/total boundary did not cache')
            async with service.acquire_load(first_pin.load_id) as active:
                decoded = Counter()
                attributes = await pool.fetch(
                    "SELECT atttypid::integer AS oid FROM pg_attribute WHERE attrelid='measurement'::regclass "
                    'AND attnum>0 AND NOT attisdropped ORDER BY attnum')
                oids = tuple(row['oid'] for row in attributes)
                for batch in iter_archive_binary_rows(active.local_path, active.manifest, 'measurement'):
                    for binary in batch:
                        if binary[-1] == b'observation':
                            decoded[record_payload(binary[:-4], oids)] += 1
                start, end = quarter_bounds(2025, 1)
                expected = Counter(row['payload'] for row in await pool.fetch(
                    'SELECT record_send(m) AS payload FROM measurement m WHERE time>=$1 AND time<$2', start, end))
                _assert(decoded == expected, 'downloaded typed ML fields or multiplicity changed')
                graph = await ArchiveGraphReader(pool, heavy.run, root).read(
                    active, start=start + timedelta(days=1), end=start + timedelta(days=1, minutes=5), max_points=10)
                _assert(sum(item.sample_count for item in graph['sensors'].statistics) == 8,
                        'explicit ZIP graph lost raw sensor observations')
                _assert(active.local_path.read_bytes() == state.files['/dav/files/fixture/' + rows[1]['remote_path']],
                        'local selected ZIP differs from actual HTTP-uploaded artifact')

                # A missing transfer collection must rotate its UUID and restart at chunk 1.
                expired_id = uuid4()
                event_start = len(state.upload_events)
                async with dav_factory() as dav:
                    await dav.upload(
                        active.local_path, ArtifactName(datasets[1], 1, active.manifest.sha256,
                                                       kind='veg_quarter', year=2025, quarter=1),
                        upload_id=expired_id, resume_from=2, verifier=service._verify_file,
                    )
                events = state.upload_events[event_start:]
                _assert(events and events[0][0].rsplit('/', 1)[-1] != str(expired_id),
                        'expired upload collection was silently reused')
                _assert(events[0][1] == '00001' and all(1 <= int(item[1]) <= 10000 for item in events),
                        'upload did not restart with decimal one-based chunks')
            cache.limits = CacheLimits(file_bytes=max(first_size, second_size), total_bytes=first_size + second_size)
            second_pin = await load(datasets[2])
            _assert(cache.owns_cache_path(second_pin.local_path), 'inclusive total-size boundary staged unnecessarily')
            third_pin = await load(datasets[3])
            _assert(cache.owns_staging_path(third_pin.local_path), 'pinned cache pressure exceeded the total bound')
            _assert(first_pin.local_path.is_file() and second_pin.local_path.is_file(), 'pressure evicted a selected copy')
            for pin in (first_pin, second_pin, third_pin):
                await service.release_load(pin.load_id)

            # Four real quarterly artifacts become one byte-preserving annual container.
            cache.limits = CacheLimits(file_bytes=max(row['byte_size'] for row in rows.values()),
                                       total_bytes=4 * max(row['byte_size'] for row in rows.values()))
            annual_id = uuid4()
            year_start, _ = quarter_bounds(2025, 1)
            _, year_end = quarter_bounds(2025, 4)
            await pool.execute(
                "INSERT INTO archive_dataset(dataset_id,kind,calendar_year,room,available_from,available_to) "
                "VALUES($1,'veg_year',2025,'Veg Room',$2,$3)", annual_id, year_start, year_end,
            )
            annual_job = await queue.enqueue(annual_id, 'annual_merge', 1, required_closed=False)
            annual_artifact = await finish(annual_job.job_id, service.merge_annual)
            annual_row = await pool.fetchrow('SELECT * FROM archive_artifact WHERE artifact_id=$1', annual_artifact)
            _assert(annual_row['byte_size'] > cache.limits.file_bytes, 'annual fixture did not exceed the scaled file bound')
            annual_manifest = await service.reverify_artifact(annual_artifact)
            _assert(annual_manifest.data['observation_counts']['measurement'] == 32, 'annual member totals changed')
            _assert(await pool.fetchval('SELECT count(*) FROM archive_component WHERE parent_artifact_id=$1',
                                        annual_artifact) == 4, 'annual child catalog bindings missing')
            quarter_one, quarter_two = await load(datasets[1]), await load(datasets[2])
            _assert(quarter_one.artifact_id == quarter_two.artifact_id == annual_artifact,
                    'quarters did not resolve their annual artifact')
            _assert(quarter_one.member_prefix == 'Q1/' and quarter_two.member_prefix == 'Q2/',
                    'quarter selection resolved the wrong component')
            _assert(quarter_one.local_path == quarter_two.local_path and cache.owns_staging_path(quarter_one.local_path),
                    'oversized annual selections did not share one staging copy')
            shared_path = quarter_one.local_path
            async with service.acquire_load(quarter_one.load_id) as active:
                await service.release_load(quarter_two.load_id)
                await pool.execute("UPDATE archive_load SET expires_at=now()-interval '1 second' WHERE load_id=$1",
                                   quarter_one.load_id)
                _assert(await service.expire_loads() == 1, 'idle expiry did not remove the selected metadata row')
                _assert(hashlib.sha256(shared_path.read_bytes()).hexdigest() == annual_manifest.sha256,
                        'release/expiry unlinked an in-flight annual reader')
                rows_read = sum(len(batch) for batch in iter_archive_binary_rows(
                    active.local_path, active.manifest, 'measurement', component_dataset_id=datasets[1]))
                _assert(rows_read == 8, 'annual quarter reader exposed another child or lost samples')
            _assert(not shared_path.exists(), 'last in-flight reader leaked oversized staging')
            _assert(await source_bags(pool) == before_source, 'disabled lifecycle changed original fields')
            print(json.dumps({
                'result': 'PASS', 'quarter_artifacts': 4, 'annual_bytes': annual_manifest.byte_size,
                'annual_sha256': annual_manifest.sha256, 'observations': 32,
                'checks': ['typed_remote_ML', 'explicit_ZIP_graph', 'immutable_reuse', '401', '507',
                           'corrupt_remote', 'lost_MOVE_ack', 'expired_collection_rotation',
                           'inclusive_cache_limits', 'pinned_pressure', 'four_quarter_container',
                           'shared_oversized_pins', 'in_flight_expiry', 'source_bytes_unchanged'],
            }, sort_keys=True))
    finally:
        await heavy.shutdown()
        await pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    asyncio.run(run(parser.parse_args().database_url))
