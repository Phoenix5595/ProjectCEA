"""Disposable proof for source-digest fencing and concurrent post-check inserts."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from dav_fixture import loopback_dav
from smoke_derived_refresh import install_real_derived_schema
from smoke_lifecycle import _assert, _parse_database_url
import asyncpg
import httpx

from grow_archive_service.capture import SourceCapture
from grow_archive_service.derived_refresh import DerivedHistoryMaintenance
from grow_archive_service.deletion import SourceDeletion
from grow_archive_service.execution import HeavyWorkExecutor
from grow_archive_service.exporter import ExportService
from grow_archive_service.lifecycle import LifecycleService
from grow_archive_service.models import ArchiveError
from grow_archive_service.pg_fields import decode
from grow_archive_service.parquet_codec import iter_archive_binary_rows
from grow_archive_service.webdav import WebDAVArtifactClient
from shared.nextcloud_dav import discover_nextcloud

ANNIVERSARY = datetime(2026, 9, 2, 4, tzinfo=UTC)


async def _create_closed_grow(pool, *, label: str, sample_at: datetime, value: float):
    grow_id, dataset_id = uuid4(), uuid4()
    start, end = datetime(2025, 8, 1, tzinfo=UTC), datetime(2025, 9, 2, tzinfo=UTC)
    window_start = sample_at.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=1)
    batch = await pool.fetchval(
        "INSERT INTO crop_batch(crop_name,start_date,end_date,room_id) "
        "SELECT $1,$2,$3,room_id FROM room WHERE name='Flower Room' RETURNING batch_id",
        f'{label}-{grow_id}', start.date(), end.date())
    _assert(batch is not None, 'no Flower Room crop batch room found')
    await pool.execute(
        "INSERT INTO grow_lifecycle(grow_plan_id,crop_batch_id,state,original_drying_end,"
        "first_closed_at,closed_at) VALUES($1,$2,'closed',$3,$4,$4)",
        grow_id, batch, date(2025, 9, 1), datetime(2025, 9, 2, tzinfo=UTC))
    await pool.execute(
        "INSERT INTO archive_dataset(dataset_id,kind,grow_plan_id,room,calendar_revision,"
        "data_revision,published_revision,state,available_from,available_to,anniversary_at) "
        "VALUES($1,'grow',$2,'Flower Room',1,1,NULL,'pending',$3,$4,$5)",
        dataset_id, grow_id, window_start, window_end, ANNIVERSARY)
    event_id = await pool.fetchval("SELECT nextval(pg_get_serial_sequence('calendar_event','id'))")
    await pool.execute(
        "INSERT INTO archive_window(dataset_id,calendar_revision,event_id,phase_type,room,start_at,end_at) "
        "VALUES($1,1,$2,'drying','Flower Room',$3,$4)", dataset_id, event_id, window_start, window_end)
    await pool.execute(
        "INSERT INTO archive_scope_override(source_table,source_identity,room,start_at,end_at) "
        "VALUES('measurement',jsonb_build_object('sensor_id',1),'Flower Room',$1,$2)",
        window_start, window_end)
    await pool.execute(
        "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,1,$2::real,'ok')",
        sample_at, value)
    return dataset_id, sample_at


async def _finish(queue, job_id, handler):
    claim = await queue.claim(uuid4(), now=datetime.now(UTC), job_id=job_id)
    _assert(claim is not None, f'owned lifecycle job not claimable: {job_id}')
    try:
        result = await handler(claim)
        await queue.succeed(claim)
        return result
    except ArchiveError as error:
        await queue.fail(claim, error.code)
        raise


async def _export_planned(service, exporter, dataset_id, moment):
    tick = await service.run_lifecycle_tick(moment)
    jobs = [item for item in tick.planned if item.dataset_id == dataset_id and item.kind == 'export']
    _assert(len(jobs) == 1, f'planner did not schedule one export for {dataset_id}: {tick.planned}')
    artifact_id = await _finish(service.queue, jobs[0].job_id, exporter.export_dataset)
    _assert(await service.pool.fetchval(
        "SELECT state='verified' FROM archive_artifact WHERE artifact_id=$1", artifact_id),
        'source export did not publish a verified remote ZIP')
    return artifact_id


async def _due_expiry(service, dataset_id, moment):
    tick = await service.run_lifecycle_tick(moment)
    jobs = [item for item in tick.planned if item.dataset_id == dataset_id and item.kind == 'expire']
    _assert(len(jobs) == 1, f'anniversary planner did not schedule expiry for {dataset_id}: {tick.planned}')
    return jobs[0].job_id


class _InsertAfterPartCheck:
    """Temporarily intercept the deletion metric pass and commit via another connection."""

    def __init__(
        self, deletion: SourceDeletion, pool, sample_at: datetime, value: float, *, sensor_id: int = 1,
    ) -> None:
        self.deletion = deletion
        self.pool = pool
        self.sample_at = sample_at
        self.value = value
        self.sensor_id = sensor_id
        self.inserted = False

    async def delete_verified(self, *args, **kwargs):
        deletion = self.deletion
        original_heavy = deletion.heavy
        outer = self

        class HookedHeavy:
            def __getattr__(self, name):
                return getattr(original_heavy, name)

            async def run(self, work):
                result = await original_heavy.run(work)
                if not outer.inserted:
                    await asyncio.wait_for(outer.pool.execute(
                        "INSERT INTO measurement(time,sensor_id,value,status) "
                        "VALUES($1,$2,$3::real,'ok')", outer.sample_at, outer.sensor_id, outer.value), timeout=30)
                    outer.inserted = True
                return result

        deletion.heavy = HookedHeavy()
        try:
            return await deletion.delete_verified(*args, **kwargs)
        finally:
            deletion.heavy = original_heavy


async def _assert_archive_payload(exporter, pool, dataset_id, sample_at, sensor_id):
    dataset = await pool.fetchrow("SELECT * FROM archive_dataset WHERE dataset_id=$1", dataset_id)
    prior = await exporter.prior_archive_path(dataset)
    try:
        columns = prior.manifest.data['source_schema']['measurement']['columns']
        source = await pool.fetchrow(
            'SELECT * FROM measurement WHERE time=$1 AND sensor_id=$2', sample_at, sensor_id)
        _assert(source is not None, 'concurrent inserted source row disappeared before re-export proof')
        expected = tuple(source[column['name']] for column in columns)
        matches = 0
        for batch in iter_archive_binary_rows(prior.path, prior.manifest, 'measurement',
                                              component_dataset_id=prior.component_dataset_id):
            matches += sum(tuple(decode(field, column['type'])
                                 for field, column in zip(row, columns)) == expected
                           for row in batch)
        _assert(matches == 1, f're-export contains concurrent source payload {matches} times, expected once')
    finally:
        await exporter.release_prior_archive(prior.path)


async def run(database_url: str) -> None:
    _parse_database_url(database_url)
    _assert(os.getenv('MONITORING_HARNESS_INTERNAL') == '1', 'owned harness marker required')
    await install_real_derived_schema(database_url)
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3, command_timeout=30)
    heavy = HeavyWorkExecutor()
    heavy.control.allow_resume()
    try:
        _assert(await pool.fetchval(
            "SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
            "WHERE fixture_name='grow-data-lifecycle'") == 1, 'owned fixture marker missing')
        with tempfile.TemporaryDirectory(prefix='grow-deletion-fence-') as directory, \
                loopback_dav() as (origin, state):
            root = Path(directory)
            os.environ['CALDAV_ALLOW_HTTP_TEST'] = 'true'

            @asynccontextmanager
            async def dav_factory(instance_id=None):
                async with httpx.AsyncClient(auth=('fixture', 'fixture-only-password'), trust_env=False,
                                             follow_redirects=False, timeout=15) as client:
                    discovery = await discover_nextcloud(client, origin + '/dav')
                    if instance_id is not None:
                        _assert(discovery.instance_id == instance_id, 'Files instance changed')
                    yield WebDAVArtifactClient(discovery, client, instance_id=discovery.instance_id,
                                               scratch_root=root / 'staging' / 'http')

            policy_now = datetime(2026, 9, 1, 12, tzinfo=UTC)
            maintenance = DerivedHistoryMaintenance(pool, heavy)
            capture = SourceCapture(pool, heavy)
            exporter = ExportService(pool, root, heavy, capture, clock=lambda: policy_now,
                                     dav_factory=dav_factory)
            deletion = SourceDeletion(pool, heavy, exporter.queue, maintenance)
            service = LifecycleService(pool, exporter, deletion, maintenance, enabled=True,
                                       clock=lambda: policy_now, freshness=type('Freshness', (), {'paused': False})())

            # Stale-byte proof: a source insert after export must invalidate the ZIP fence.
            stale_dataset, stale_at = await _create_closed_grow(
                pool, label='owned-deletion-fence-stale',
                sample_at=datetime(2025, 8, 2, 1, 15, tzinfo=UTC), value=71.25)
            await _export_planned(service, exporter, stale_dataset, policy_now)
            before = await pool.fetchval(
                "SELECT count(*) FROM measurement WHERE time=$1 AND sensor_id=1", stale_at)
            _assert(before == 1, 'pre-anniversary archive export removed source rows')
            policy_now = ANNIVERSARY - timedelta(microseconds=1)
            early = await service.run_lifecycle_tick(policy_now)
            _assert(not any(item.dataset_id == stale_dataset and item.kind == 'expire' for item in early.planned),
                    'grow expiry was planned before its anniversary')
            policy_now = ANNIVERSARY
            expiry_id = await _due_expiry(service, stale_dataset, policy_now)
            await pool.execute(
                "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,1,72.5::real,'ok')",
                stale_at + timedelta(minutes=1))
            await _expect_source_changed(service, pool, expiry_id, stale_dataset, stale_at)
            next_tick = await service.run_lifecycle_tick(policy_now)
            export_jobs = [item for item in next_tick.planned
                           if item.dataset_id == stale_dataset and item.kind == 'export']
            _assert(len(export_jobs) == 1, f'dirty dataset did not plan re-export: {next_tick.planned}')

            # Concurrent insert proof: another connection commits immediately after the
            # deletion-side part digest has been checked, outside its REPEATABLE READ snapshot.
            race_at = datetime(2025, 8, 3, 2, 15, tzinfo=UTC)
            race_dataset, _ = await _create_closed_grow(
                pool, label='owned-deletion-fence-race', sample_at=race_at, value=81.25)
            await _export_planned(service, exporter, race_dataset, policy_now)
            race_expiry = await service.queue.enqueue(
                race_dataset, 'expire', 1, required_closed=True)
            race_expiry_id = race_expiry.job_id
            race_sample = race_at + timedelta(minutes=1)
            hook = _InsertAfterPartCheck(deletion, pool, race_sample, 82.5)
            try:
                service.deletion = hook
                try:
                    removed = await _finish(service.queue, race_expiry_id, service.expire)
                finally:
                    service.deletion = deletion
            except ArchiveError as error:
                _assert(error.code == 'archive_job_superseded',
                        f'post-check insert should supersede the stale expiry fence: {error.code}')
            else:
                _assert(removed == 1, f'deleting transaction removed wrong row count: {removed}')
            _assert(await pool.fetchval(
                'SELECT count(*) FROM measurement WHERE time=$1 AND sensor_id=1', race_at) == 0,
                'verified row was not deleted before post-check revision fencing')
            _assert(await pool.fetchval(
                'SELECT count(*) FROM measurement WHERE time=$1 AND sensor_id=1', race_sample) == 1,
                'concurrent post-check source row did not survive the deleting snapshot')
            _assert(hook.inserted, 'concurrent source insert hook did not run after the part re-check')
            dataset_state = await pool.fetchrow(
                "SELECT state,data_revision,published_revision FROM archive_dataset WHERE dataset_id=$1",
                race_dataset)
            _assert(dataset_state['state'] == 'dirty'
                    and dataset_state['data_revision'] == dataset_state['published_revision'] + 1,
                    f'post-snapshot insert did not dirty and advance the dataset revision: {dataset_state}')
            next_tick = await service.run_lifecycle_tick(policy_now)
            export_jobs = [item for item in next_tick.planned
                           if item.dataset_id == race_dataset and item.kind == 'export']
            _assert(len(export_jobs) == 1, f'post-check insert did not plan re-export: {next_tick.planned}')
            await _finish(service.queue, export_jobs[0].job_id, exporter.export_dataset)
            await _assert_archive_payload(exporter, pool, race_dataset, race_sample, 1)
            race_expiry = await service.queue.enqueue(
                race_dataset, 'expire', 2, required_closed=True)
            race_expiry_id = race_expiry.job_id
            removed = await _finish(service.queue, race_expiry_id, service.expire)
            _assert(removed == 1, f'second expiry did not delete the remaining concurrent row: {removed}')
            _assert(await pool.fetchval(
                "SELECT count(*) FROM measurement WHERE time=$1 AND sensor_id=1", race_sample) == 0,
                'second verified expiry retained the concurrent source row')

            print(json.dumps({'result': 'PASS', 'checks': [
                'preanniversary_export_kept_source_rows', 'source_mismatch_blocks_delete_and_marks_dirty',
                'dirty_revision_plans_export', 'post_check_insert_survives_and_dirties_revision',
                'reexport_contains_concurrent_row_once', 'second_verified_expiry_deletes_concurrent_row'],
                'stale_dataset': str(stale_dataset), 'race_dataset': str(race_dataset)}, sort_keys=True))
    finally:
        await heavy.shutdown()
        await pool.close()


async def _expect_source_changed(service, pool, job_id, dataset_id, sample_at):
    before = await pool.fetchval(
        "SELECT count(*) FROM measurement WHERE time >= $1 AND time < $1 + interval '1 hour' "
        "AND sensor_id=1", sample_at)
    try:
        await _finish(service.queue, job_id, service.expire)
    except ArchiveError as error:
        _assert(error.code == 'archive_source_changed', f'expected archive_source_changed, got {error.code}')
    else:
        raise AssertionError('source digest mismatch did not block expiry')
    after = await pool.fetchval(
        "SELECT count(*) FROM measurement WHERE time >= $1 AND time < $1 + interval '1 hour' "
        "AND sensor_id=1", sample_at)
    _assert(before == after == 2, f'source mismatch deleted or altered bytes: before={before}, after={after}')
    dataset = await pool.fetchrow(
        "SELECT state,data_revision,published_revision,last_error_code FROM archive_dataset WHERE dataset_id=$1",
        dataset_id)
    _assert(dataset['state'] == 'dirty' and dataset['data_revision'] == dataset['published_revision'] + 1
            and dataset['last_error_code'] == 'archive_source_changed',
            f'source mismatch did not dirty and advance the dataset: {dataset}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    asyncio.run(run(parser.parse_args().database_url))
