"""Guarded real quarterly correction/rebuild proof; no production targets.

Actual acceptance chain on the disposable monitoring_test_ fixture:
  four real HTTP quarter exports -> one annual container -> the standalone
  quarter ZIP retires (superseded and remote-deleted) -> verified Q2 expiry
  removes old source rows -> a time-window shrink rebuilds Q2 from its annual
  component plus remaining source (if any) -> the replacement annual preserves
  the other three children's member bytes and counts. A scope correction that
  touches removed Q2 time is rejected. Explicit quarter load/read resolves the
  right Q-prefix, and the quarter download truthfully identifies the whole
  annual container through real repository code.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from smoke_lifecycle import _assert, _parse_database_url
from smoke_derived_refresh import install_canonical_sql
from dav_fixture import loopback_dav
import asyncpg
import httpx

from grow_archive_service.annual_codec import annual_component
from grow_archive_service.binary_codec import record_payload
from grow_archive_service.cache import ArchiveCache, CacheLimits
from grow_archive_service.capture import SourceCapture
from grow_archive_service.deletion import SourceDeletion
from grow_archive_service.derived_refresh import DerivedHistoryMaintenance
from grow_archive_service.execution import HeavyWorkExecutor
from grow_archive_service.exporter import ExportService
from grow_archive_service.lifecycle import LifecycleService, quarter_bounds
from grow_archive_service.models import ArchiveError
from grow_archive_service.parquet_codec import iter_archive_binary_rows
from grow_archive_service.repository import ArchiveRepository
from grow_archive_service.schema import load_source_catalog
from grow_archive_service.webdav import WebDAVArtifactClient
from shared.nextcloud_dav import discover_nextcloud
from shared.grow_lifecycle import retention_cutoffs



class _FreshnessReady:
    paused = False


YEAR = 2025




async def source_bags(pool):
    async with pool.acquire() as conn:
        return {stream.name: Counter(row['payload'] for row in await conn.fetch(
            f'SELECT record_send(m) AS payload FROM public."{stream.name}" m'))
                for stream in load_source_catalog().defined_streams()}


async def child_member_rows(path, verified, dataset_id, oids):
    """Verified binary rows of one annual child, keyed by role label."""
    decoded = Counter()
    for batch in iter_archive_binary_rows(path, verified, 'measurement',
                                          component_dataset_id=dataset_id):
        for binary in batch:
            decoded[(record_payload(binary[:-4], oids), binary[-1].decode())] += 1
    return decoded


async def run(database_url):
    _parse_database_url(database_url)
    _assert(os.getenv('MONITORING_HARNESS_INTERNAL') == '1', 'owned harness marker required')
    # SourceDeletion's preflight requires the same real aggregate catalog as production.
    await install_canonical_sql(database_url)
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2, command_timeout=5)
    heavy = HeavyWorkExecutor()
    heavy.control.allow_resume()
    try:
        _assert(await pool.fetchval("SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
                                    "WHERE fixture_name='grow-data-lifecycle'") == 1, 'fixture marker missing')
        os.environ['CALDAV_ALLOW_HTTP_TEST'] = 'true'
        with tempfile.TemporaryDirectory(prefix='grow-annual-correction-owned-') as directory, \
                loopback_dav() as (origin, state):
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
            maintenance = DerivedHistoryMaintenance(pool, heavy)
            capture = SourceCapture(pool, heavy)
            captured_priors = []
            capture_impl = capture.capture

            async def capture_and_record_prior(claim, directory, *, prior):
                captured_priors.append(prior)
                return await capture_impl(claim, directory, prior=prior)

            capture.capture = capture_and_record_prior
            service = ExportService(pool, root, heavy, capture, dav_factory=dav_factory, cache=cache)
            queue = service.queue
            expiry_at = datetime(2026, 5, 15, 12, tzinfo=UTC)
            rows_before = retention_cutoffs(expiry_at).local_expiry_before
            deletion = SourceDeletion(pool, heavy, queue, maintenance)
            lifecycle = LifecycleService(
                pool, service, deletion, maintenance, enabled=True,
                clock=lambda: expiry_at, freshness=_FreshnessReady(),
            )

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

            async def load(dataset, requested_revision=None):
                request = await service.enqueue_load(dataset, requested_revision)
                if request.load is not None:
                    return request.load
                _assert(request.job_id is not None, 'load returned neither selection nor job')
                return await finish(request.job_id, service.run_load)

            attributes = await pool.fetch(
                "SELECT atttypid::integer AS oid FROM pg_attribute "
                "WHERE attrelid=(SELECT to_regclass('public.measurement')) "
                'AND attnum>0 AND NOT attisdropped ORDER BY attnum')
            oids = tuple(row['oid'] for row in attributes)

            # ------------------------------------------------------------------
            # Four real quarterly exports against the actual fixture streams.
            # ------------------------------------------------------------------
            datasets = {}
            artifacts = {}
            for quarter in (1, 2, 3, 4):
                start, end = quarter_bounds(YEAR, quarter)
                window_start, window_end = start, end
                dataset = uuid4()
                datasets[quarter] = dataset
                await pool.execute(
                    "INSERT INTO archive_dataset(dataset_id,kind,calendar_year,quarter,room,available_from,available_to) "
                    "VALUES($1,'veg_quarter',2025,$2,'Veg Room',$3,$4)",
                    dataset, quarter, window_start, window_end,
                )
                sample_start = rows_before - timedelta(minutes=10) if quarter == 2 else start + timedelta(days=1)
                for sensor in (5, 6):
                    await pool.execute(
                        "INSERT INTO archive_scope_override(source_table,source_identity,room,start_at,end_at) "
                        "VALUES('measurement',jsonb_build_object('sensor_id',$1::integer),'Veg Room',$2,$3)",
                        sensor, window_start, window_end,
                    )
                    for offset, value in enumerate((-0.0, 1.5, 2.5, 3.5)):
                        await pool.execute(
                            "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,$2,$3,'ok')",
                            sample_start + timedelta(minutes=offset), sensor, value,
                        )
                    if quarter == 2:
                        await pool.execute(
                            "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,$2,$3,'ok')",
                            rows_before + timedelta(minutes=5), sensor, 9.5,
                        )
                artifacts[quarter] = await export(datasets[quarter])
                print(f'annual-correction: Q{quarter} export complete', flush=True)
                _assert(await pool.fetchval('SELECT count(*) FROM archive_artifact '
                                            'WHERE dataset_id=$1 AND state=$2', datasets[quarter],
                                            'verified') == 1,
                        f'quarter {quarter} did not publish exactly one verified artifact')
            before_source = await source_bags(pool)
            original_revisions = {
                quarter: await pool.fetchval(
                    'SELECT data_revision FROM archive_dataset WHERE dataset_id=$1', dataset)
                for quarter, dataset in datasets.items()
            }
            repository = ArchiveRepository(pool)
            direct_catalog_page = await repository.list_entries('Veg Room', None, 50)
            direct_catalog = next(item for item in direct_catalog_page.items
                                  if item['dataset_id'] == datasets[1])
            direct_download = await repository.download_metadata(
                artifacts[1], datasets[1], original_revisions[1])
            _assert(direct_download.label == direct_catalog['download_label'] == 'Download verified ZIP',
                    f'direct download label differed from catalog: {direct_download.label!r}')


            # ------------------------------------------------------------------
            # One annual container from the four verified quarter artifacts.
            # ------------------------------------------------------------------
            annual_id = uuid4()
            year_start, _ = quarter_bounds(YEAR, 1)
            _, year_end = quarter_bounds(YEAR, 4)
            await pool.execute(
                "INSERT INTO archive_dataset(dataset_id,kind,calendar_year,room,available_from,available_to) "
                "VALUES($1,'veg_year',2025,'Veg Room',$2,$3)", annual_id, year_start, year_end,
            )
            annual_job = await queue.enqueue(annual_id, 'annual_merge', 1, required_closed=False)
            annual_artifact = await finish(annual_job.job_id, service.merge_annual)
            print('annual-correction: annual merge complete', flush=True)
            annual_manifest = await service.reverify_artifact(annual_artifact)
            original_annual_revision = await pool.fetchval(
                'SELECT data_revision FROM archive_dataset WHERE dataset_id=$1', annual_id)
            _assert(await source_bags(pool) == before_source,
                    'initial annual merge mutated the original source fields')
            _assert(annual_manifest.data['observation_counts']['measurement'] == 34,
                    'annual member totals changed during merge')
            _assert(await pool.fetchval('SELECT count(*) FROM archive_component WHERE parent_artifact_id=$1',
                                        annual_artifact) == 4, 'annual child catalog bindings missing')
            annual_remote_path = await pool.fetchval(
                'SELECT remote_path FROM archive_artifact WHERE artifact_id=$1', annual_artifact)
            _assert(annual_remote_path and f'/Veg/{YEAR}/' in annual_remote_path,
                    f'annual download path is not the yearly Veg prefix: {annual_remote_path}')
            child_hashes = {}
            for quarter in (1, 2, 3, 4):
                child, prefix = annual_component(annual_manifest, datasets[quarter])
                _assert(prefix == f'Q{quarter}/', 'annual child prefix resolution failed')
                component_entry = next(entry for entry in annual_manifest.data['components']
                                       if entry['dataset_id'] == str(datasets[quarter]))
                child_hashes[quarter] = str(component_entry['sha256'])

            # ------------------------------------------------------------------
            # The standalone quarter copy retires: superseded, remote-deleted.
            # ------------------------------------------------------------------
            q2_remote_path = await pool.fetchval(
                'SELECT remote_path FROM archive_artifact WHERE artifact_id=$1', artifacts[2])
            q2_state = await pool.fetchval('SELECT state FROM archive_artifact WHERE artifact_id=$1',
                                           artifacts[2])
            _assert(q2_state == 'superseded', 'annual merge left the standalone quarter artifact verified')
            _assert(q2_remote_path in state.files or ('/dav/files/fixture/' + q2_remote_path) in state.files,
                    'fixture lost the standalone quarter remote path before retirement proof')
            _assert(await pool.fetchval(
                'SELECT parent_artifact_id FROM archive_component WHERE child_dataset_id=$1',
                datasets[2]) == annual_artifact, 'Q2 annual component binding missing')

            # ------------------------------------------------------------------
            # Explicit quarter load resolves the annual member prefix.
            # ------------------------------------------------------------------
            cache.limits = CacheLimits(file_bytes=10 * 1024 * 1024 * 1024,
                                       total_bytes=10 * 1024 * 1024 * 1024)
            second_pin = await load(datasets[2], 1)
            _assert(second_pin.artifact_id == annual_artifact, 'Q2 load did not resolve its annual container')
            _assert(second_pin.member_prefix == 'Q2/', 'Q2 selection resolved the wrong component')
            _assert(second_pin.source_kind == 'zip',
                    'Q2 load did not select the verified ZIP source')
            async with service.acquire_load(second_pin.load_id) as active:
                decoded = await child_member_rows(active.local_path, active.manifest, datasets[2], oids)
                start, _ = quarter_bounds(YEAR, 2)
                expected = Counter((row['payload'], 'observation') for row in await pool.fetch(
                    'SELECT record_send(m) AS payload FROM measurement m '
                    'WHERE time>=$1 AND time<$2', start, quarter_bounds(YEAR, 3)[0]))
                _assert(decoded == expected, 'Q2 annual member rows lost typed fields or multiplicity')
            await service.release_load(second_pin.load_id)

            # The correction must work after the standalone quarter files are
            # truly gone, not merely marked superseded in the catalog.
            retirement_job = await queue.enqueue(
                annual_id, 'artifact_cleanup', original_annual_revision, required_closed=False)
            await finish(retirement_job.job_id, service.cleanup_artifacts)
            retired_path = '/dav/files/fixture/' + q2_remote_path
            _assert(retired_path not in state.files,
                    'standalone Q2 still existed before its correction')
            _assert(await pool.fetchval(
                "SELECT count(*) FROM archive_artifact WHERE artifact_id=ANY($1::uuid[]) "
                "AND state='superseded' AND remote_path IS NULL", list(artifacts.values())) == 4,
                'annual adoption did not retire all four standalone quarters')

            # ------------------------------------------------------------------
            # Verified source expiry before correcting the Q2 time window.
            # ------------------------------------------------------------------
            q2_start, _ = quarter_bounds(YEAR, 2)
            q2_window_start = q2_start
            q2_window_end = quarter_bounds(YEAR, 3)[0]
            q2_rows_before_expiry = await pool.fetch(
                'SELECT time,record_send(m) AS payload FROM measurement m '
                'WHERE sensor_id IN (5,6) AND time>=$1 AND time<$2',
                q2_window_start, q2_window_end)
            _assert(len(q2_rows_before_expiry) == 10, 'Q2 source fixture row count changed')
            expiry_job = await queue.enqueue(
                datasets[2], 'expire', original_revisions[2], required_closed=False)
            deleted = await finish(expiry_job.job_id, lifecycle.expire)
            remaining_q2_rows = int(await pool.fetchval(
                'SELECT count(*) FROM measurement WHERE sensor_id IN (5,6) AND time>=$1 AND time<$2',
                q2_window_start, q2_window_end))
            removed_q2_rows = len(q2_rows_before_expiry) - remaining_q2_rows
            _assert(removed_q2_rows == 6 and remaining_q2_rows == 4,
                    f'Veg expiry did not preserve only the Q2 anchors/newer rows: '
                    f'removed={removed_q2_rows}, remaining={remaining_q2_rows}')
            _assert(deleted >= removed_q2_rows,
                    f'verified Q2 expiry removed only {deleted} rows')
            print('annual-correction: Q2 verified deletion complete', flush=True)
            after_expiry = await source_bags(pool)
            _assert(after_expiry['measurement'] != before_source['measurement'],
                    'Q2 expiry did not remove source bytes')
            catalog_page = await ArchiveRepository(pool).list_entries('Veg Room', None, 50)
            catalog_item = next(item for item in catalog_page.items
                                if item['dataset_id'] == datasets[2])
            _assert(catalog_item['local_tier'] == 'zip_cache',
                    f'deleted Q2 source did not resolve through its cached ZIP: {catalog_item["local_tier"]}')


            # A reassignment touching any removed Q2 interval must fail closed.
            try:
                await ArchiveRepository(pool).confirm_scope_assignment(
                    datasets[2], original_revisions[2], 'measurement', {'sensor_id': 5},
                    rows_before - timedelta(minutes=10),
                    rows_before - timedelta(minutes=5), 'Veg Room',
                )
            except ArchiveError as error:
                _assert(error.code == 'archive_correction_requires_source'
                        and error.status_code == 409,
                        f'scope correction returned {error.code}/{error.status_code}')
            else:
                raise AssertionError('scope correction touching removed Q2 time was accepted')

            # Shrink the lower time bound so the first two timestamps are
            # excluded, while the retained anchor and newer source rows stay.
            # The rebuilt child must combine those source rows with the
            # time-filtered annual ZIP component.
            shrunken_start = rows_before - timedelta(minutes=8)
            expected_q2_rows = Counter(
                (row['payload'], 'observation') for row in q2_rows_before_expiry
                if row['time'] >= shrunken_start
            )
            _assert(sum(expected_q2_rows.values()) == 6,
                    'Q2 shrink fixture does not select the retained and newer source rows')
            await pool.execute(
                "UPDATE archive_dataset SET available_from=$2,data_revision=data_revision+1,"
                "state='dirty',updated_at=clock_timestamp() WHERE dataset_id=$1 AND data_revision=$3",
                datasets[2], shrunken_start, original_revisions[2],
            )
            corrected_revision = int(await pool.fetchval(
                'SELECT data_revision FROM archive_dataset WHERE dataset_id=$1', datasets[2]))
            _assert(corrected_revision == original_revisions[2] + 1,
                    'Q2 window shrink did not advance its revision')
            await pool.execute(
                "UPDATE archive_dataset SET data_revision=data_revision+1,state='dirty',"
                "updated_at=clock_timestamp() WHERE dataset_id=$1",
                annual_id,
            )
            annual_fence = int(await pool.fetchval(
                'SELECT data_revision FROM archive_dataset WHERE dataset_id=$1', annual_id))
            _assert(annual_fence > original_annual_revision,
                    'the containing annual container did not invalidate with its child')

            correction_job = await queue.enqueue(
                datasets[2], 'export', corrected_revision, required_closed=False)
            await finish(correction_job.job_id, service.export_dataset)
            print('annual-correction: Q2 time-window export complete', flush=True)
            corrected_state = await pool.fetchrow(
                'SELECT data_revision,published_revision,state FROM archive_dataset WHERE dataset_id=$1',
                datasets[2])
            _assert(int(corrected_state['published_revision']) == corrected_revision
                    and corrected_state['state'] == 'ready',
                    'corrected quarter did not republish its rebuilt revision with source remaining')
            _assert(await source_bags(pool) == after_expiry,
                    'time-window correction mutated the remaining source')
            prior = captured_priors[-1]
            _assert(prior is not None and prior.component_dataset_id == datasets[2],
                    'Q2 rebuild did not receive a PriorArchive bound to the annual component')
            _assert(prior.manifest.dataset_id == str(annual_id),
                    'Q2 rebuild prior was not the verified annual archive')

            # ------------------------------------------------------------------
            # Rebuild that quarter from its time-filtered annual prefix.
            # ------------------------------------------------------------------
            replacement_job = await queue.enqueue(
                annual_id, 'annual_merge', annual_fence, required_closed=False)
            replacement_artifact = await finish(replacement_job.job_id, service.merge_annual)
            replacement_manifest = await service.reverify_artifact(replacement_artifact)
            replacement_pin = await load(datasets[2], corrected_revision)
            _assert(replacement_pin.artifact_id == replacement_artifact
                    and replacement_artifact != annual_artifact,
                    'corrected quarter did not resolve the replacement annual container')
            _assert(replacement_pin.member_prefix == 'Q2/',
                    'replacement annual Q2 selection resolved the wrong component')
            async with service.acquire_load(replacement_pin.load_id) as active:
                rebuilt = await child_member_rows(active.local_path, active.manifest, datasets[2], oids)
                _assert(rebuilt == expected_q2_rows,
                        'rebuilt Q2 did not preserve exactly the time-filtered annual rows')
            await service.release_load(replacement_pin.load_id)

            # ------------------------------------------------------------------
            # Replacement annual preserves the other three child member hashes.
            # ------------------------------------------------------------------
            _assert(await pool.fetchval('SELECT count(*) FROM archive_component WHERE parent_artifact_id=$1',
                                        replacement_artifact) == 4,
                    'replacement annual lost a child component binding')
            for quarter in (1, 2, 3, 4):
                child, prefix = annual_component(replacement_manifest, datasets[quarter])
                _assert(prefix == f'Q{quarter}/', 'replacement child prefix resolution failed')
                replacement_entry = next(entry for entry in replacement_manifest.data['components']
                                         if entry['dataset_id'] == str(datasets[quarter]))
                if quarter == 2:
                    _assert(int(replacement_entry['revision']) == corrected_revision,
                            'corrected child kept its stale revision')
                    _assert(int(replacement_entry['manifest']['observation_counts']['measurement']) == 6,
                            'corrected Q2 component did not use the filtered prior plus current source')
                    child_manifest = replacement_entry['manifest']
                    _assert(child_manifest['available_from'] == shrunken_start.isoformat()
                            and child_manifest['available_to'] == quarter_bounds(YEAR, 3)[0].isoformat(),
                            'replacement Q2 component did not preserve the shrunken time window')
                else:
                    _assert(str(replacement_entry['sha256']) == child_hashes[quarter],
                            f'unchanged Q{quarter} member hash changed in the replacement annual')
                    _assert(int(replacement_entry['revision']) == original_revisions[quarter],
                            f'unchanged Q{quarter} child revision moved without a correction')
            _assert(replacement_manifest.data['available_from'] == year_start.isoformat()
                    and replacement_manifest.data['available_to'] == year_end.isoformat(),
                    'replacement annual availability stopped covering the calendar year')
            _assert(replacement_manifest.data['observation_counts']['measurement'] == 30,
                    'replacement annual observation totals wrong after the Q2 window shrink')
            for quarter in (1, 3, 4):
                replacement_entry = next(entry for entry in replacement_manifest.data['components']
                                         if entry['dataset_id'] == str(datasets[quarter]))
                _assert(int(replacement_entry['manifest']['observation_counts']['measurement']) == 8,
                        f'unchanged Q{quarter} member count changed in the replacement annual')

            # The superseded previous annual catalog row keeps the OLD hash set.
            for quarter in (1, 3, 4):
                old_entry = next(entry for entry in annual_manifest.data['components']
                                 if entry['dataset_id'] == str(datasets[quarter]))
                _assert(str(old_entry['sha256']) == child_hashes[quarter],
                        'original annual manifest was mutated in place')
            # ------------------------------------------------------------------
            # Truthful annual download label through real repository code.
            # ------------------------------------------------------------------
            catalog_items = (await repository.list_entries('Veg Room', None, 50)).items
            quarter_catalog = next(item for item in catalog_items
                                   if item['dataset_id'] == datasets[2])
            annual_catalog = next(item for item in catalog_items
                                  if item['dataset_id'] == annual_id)
            _assert(quarter_catalog['container'] is not None
                    and quarter_catalog['container']['dataset_id'] == annual_id,
                    'Q2 catalog entry did not report its annual container')
            parent_download = await repository.download_metadata(
                replacement_artifact, datasets[2], corrected_revision)
            annual_download = await repository.download_metadata(
                replacement_artifact, annual_id, annual_fence)
            _assert(parent_download.filename == annual_download.filename,
                    'quarter download did not return the whole annual container identity')
            _assert(parent_download.label == quarter_catalog['download_label']
                    == f'Download {YEAR} ZIP (all four quarters)',
                    f'annual parent download label differed from catalog: {parent_download.label!r}')
            _assert(annual_download.label == annual_catalog['download_label'] == 'Download verified ZIP',
                    f'direct annual download label differed from catalog: {annual_download.label!r}')

            # ------------------------------------------------------------------
            # Retirement cleanup removes the superseded annual, not its replacement.
            # ------------------------------------------------------------------
            cleanup_job = await queue.enqueue(
                annual_id, 'artifact_cleanup', annual_fence, required_closed=False)
            await finish(cleanup_job.job_id, service.cleanup_artifacts)
            _assert('/dav/files/fixture/' + annual_remote_path not in state.files,
                    'superseded annual remote was not retired after replacement')
            replacement_remote = await pool.fetchval(
                'SELECT remote_path FROM archive_artifact WHERE artifact_id=$1', replacement_artifact)
            _assert('/dav/files/fixture/' + replacement_remote in state.files,
                    'cleanup deleted the verified replacement annual remote')

            _assert(await source_bags(pool) == after_expiry,
                    'retirement cleanup mutated the raw source')
            print(json.dumps({
                'result': 'PASS',
                'year': YEAR,
                'annual_bytes': replacement_manifest.byte_size,
                'annual_sha256': replacement_manifest.sha256,
                'corrected_child_revision': corrected_revision,
                'checks': ['four_real_exports', 'annual_container', 'standalone_retired',
                           'verified_q2_expiry', 'removed_scope_correction_rejected',
                           'time_window_shrink_from_annual_component',
                           'other_three_hashes_preserved', 'replacement_counts_preserved',
                           'explicit_zip_prefix_load', 'truthful_download_label',
                           'retired_remote_cleanup', 'remaining_source_unchanged'],
            }, sort_keys=True))
    finally:
        await heavy.shutdown()
        await pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    asyncio.run(run(parser.parse_args().database_url))
