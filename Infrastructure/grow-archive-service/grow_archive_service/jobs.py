"""The archive queue: fenced content revisions, renewable claims and bounded retries."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from grow_archive_service.models import ArchiveError, ArchiveJob, JobClaim, JobFence, JobKind

TRANSIENT_ERRORS = frozenset({
    'archive_remote_unavailable', 'archive_transport_failed', 'archive_local_space_insufficient',
    'archive_remote_quota', 'archive_database_unavailable', 'archive_lock_timeout',
    'archive_revision_pending', 'archive_artifact_pinned',
})


def _json(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError('archive job checkpoint must be an object')
    return dict(value)


def _job(row: Mapping[str, Any]) -> ArchiveJob:
    return ArchiveJob(
        job_id=row['job_id'], dataset_id=row['dataset_id'], kind=row['kind'],
        requested_revision=row['requested_revision'], state=row['state'],
        checkpoint=_json(row['checkpoint']), attempts=row['attempts'],
        next_attempt_at=row['next_attempt_at'], last_error_code=row['last_error_code'],
        created_at=row['created_at'], updated_at=row['updated_at'],
    )


class ArchiveJobQueue:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def enqueue(self, dataset_id: UUID, kind: JobKind, expected_revision: int,
                      *, required_closed: bool | None = None,
                      target_revision: int | None = None) -> ArchiveJob:
        async with self.pool.acquire() as conn, conn.transaction():
            dataset = await conn.fetchrow(
                'SELECT d.*, g.state AS lifecycle_state FROM archive_dataset d '
                'LEFT JOIN grow_lifecycle g USING (grow_plan_id) WHERE dataset_id=$1 FOR UPDATE OF d', dataset_id)
            if dataset is None:
                raise ArchiveError('archive_not_found', status_code=404)
            if dataset['data_revision'] != expected_revision:
                raise ArchiveError('archive_revision_conflict')
            closed = dataset['kind'] == 'grow' if required_closed is None else required_closed
            requested = expected_revision
            checkpoint_data = {'calendar_revision': dataset['calendar_revision'],
                               'data_revision': expected_revision, 'required_closed': closed, 'progress': {}}
            if kind == 'load':
                # A load pins one published artifact revision; its durable fence
                # stays on the dataset's current content revision.
                requested = target_revision if target_revision is not None else dataset['published_revision']
                if requested is None:
                    raise ArchiveError('archive_revision_pending')
                if requested != dataset['data_revision'] and requested != dataset['published_revision']:
                    raise ArchiveError('archive_revision_conflict')
                closed = False
                checkpoint_data['required_closed'] = False
                checkpoint_data['target_revision'] = requested
                # Reusing a published target must not return an active job
                # whose content/Calendar fence belongs to an older request.
                await conn.execute(
                    "UPDATE archive_job SET state='failed',last_error_code='archive_job_superseded',"
                    "lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() "
                    "WHERE dataset_id=$1 AND kind='load' AND requested_revision=$2 "
                    "AND state IN ('pending','running','blocked') AND ("
                    "(checkpoint->>'data_revision')::bigint<>$3 OR "
                    "(checkpoint->>'calendar_revision')::bigint<>$4)",
                    dataset_id, requested, dataset['data_revision'], dataset['calendar_revision'],
                )
            elif target_revision is not None:
                raise ValueError('target_revision is only valid for load jobs')
            if closed and dataset['lifecycle_state'] != 'closed':
                raise ArchiveError('grow_not_closed')
            checkpoint = json.dumps(checkpoint_data)
            row = await conn.fetchrow(
                "INSERT INTO archive_job(job_id,dataset_id,kind,requested_revision,state,checkpoint) "
                "VALUES($1,$2,$3,$4,'pending',$5::jsonb) "
                "ON CONFLICT(dataset_id,kind,requested_revision) WHERE state IN ('pending','running','blocked') "
                "DO UPDATE SET dataset_id=EXCLUDED.dataset_id RETURNING *",
                uuid4(), dataset_id, kind, requested, checkpoint)
            return _job(row)

    async def get(self, job_id: UUID) -> ArchiveJob:
        row = await self.pool.fetchrow('SELECT * FROM archive_job WHERE job_id=$1', job_id)
        if row is None:
            raise ArchiveError('archive_job_not_found', status_code=404)
        return _job(row)

    async def claim(self, owner: UUID, *, now: datetime, job_id: UUID | None = None) -> JobClaim | None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('archive clock must be aware')
        token = uuid4()
        async with self.pool.acquire() as conn, conn.transaction():
            # Expired claims retain progress; superseded revisions never retry.
            await conn.execute(
                "UPDATE archive_job j SET state='failed',last_error_code='archive_job_superseded',"
                "lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=$1 "
                "FROM archive_dataset d LEFT JOIN grow_lifecycle g USING(grow_plan_id) "
                "WHERE j.dataset_id=d.dataset_id AND j.state IN ('pending','running','blocked') "
                "AND ((CASE WHEN j.kind='load' THEN (j.checkpoint->>'data_revision')::bigint<>d.data_revision "
                "ELSE j.requested_revision<>d.data_revision END) OR "
                "(j.checkpoint->>'calendar_revision')::bigint<>d.calendar_revision OR "
                "(COALESCE((j.checkpoint->>'required_closed')::boolean,false) AND g.state IS DISTINCT FROM 'closed'))",
                now)
            row = await conn.fetchrow(
                "WITH due AS (SELECT job_id FROM archive_job WHERE "
                "((state='pending' AND next_attempt_at<=$1) OR (state='running' AND lease_until<=$1)) "
                "AND ($4::uuid IS NULL OR job_id=$4) "
                "ORDER BY next_attempt_at,created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1) "
                "UPDATE archive_job j SET state='running',lease_owner=$2,lease_token=$3,"
                "lease_until=$1+interval '60 seconds',attempts=attempts+1,updated_at=$1 "
                "FROM due WHERE j.job_id=due.job_id RETURNING j.*", now, owner, token, job_id)
            if row is None:
                return None
            checkpoint = _json(row['checkpoint'])
            fence_revision = (int(checkpoint.get('data_revision', row['requested_revision']))
                              if row['kind'] == 'load' else row['requested_revision'])
            fence = JobFence(row['dataset_id'], fence_revision,
                             checkpoint['calendar_revision'], checkpoint['required_closed'])
            return JobClaim(_job(row), owner, token, row['lease_until'], fence)

    async def check_fence(self, conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy, claim: JobClaim, *, lock: bool = False) -> None:
        suffix = ' FOR UPDATE OF d,j' if lock else ''
        row = await conn.fetchrow(
            "SELECT d.data_revision,d.calendar_revision,g.state AS lifecycle_state "
            "FROM archive_dataset d JOIN archive_job j USING(dataset_id) "
            "LEFT JOIN grow_lifecycle g USING(grow_plan_id) WHERE j.job_id=$1 "
            "AND j.state='running' AND j.lease_owner=$2 AND j.lease_token=$3 AND j.lease_until>clock_timestamp()" + suffix,
            claim.job.job_id, claim.owner, claim.token)
        if (row is None or row['data_revision'] != claim.fence.data_revision
                or row['calendar_revision'] != claim.fence.calendar_revision
                or (claim.fence.required_closed and row['lifecycle_state'] != 'closed')):
            raise ArchiveError('archive_job_superseded')

    async def checkpoint(self, claim: JobClaim, progress: Mapping[str, Any]) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await self.check_fence(conn, claim, lock=True)
            await conn.execute(
                "UPDATE archive_job SET checkpoint=jsonb_set(checkpoint,'{progress}',$4::jsonb),"
                "lease_until=clock_timestamp()+interval '60 seconds',updated_at=clock_timestamp() "
                "WHERE job_id=$1 AND lease_owner=$2 AND lease_token=$3",
                claim.job.job_id, claim.owner, claim.token, json.dumps(progress))


    async def renew(self, claim: JobClaim) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await self.check_fence(conn, claim, lock=True)
            await conn.execute(
                "UPDATE archive_job SET lease_until=clock_timestamp()+interval '60 seconds',updated_at=clock_timestamp() "
                "WHERE job_id=$1 AND lease_owner=$2 AND lease_token=$3", claim.job.job_id, claim.owner, claim.token)


    async def succeed(self, claim: JobClaim) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await self.check_fence(conn, claim, lock=True)
            await conn.execute(
                "UPDATE archive_job SET state='succeeded',lease_owner=NULL,lease_token=NULL,lease_until=NULL,"
                "last_error_code=NULL,updated_at=clock_timestamp() WHERE job_id=$1 AND lease_owner=$2 AND lease_token=$3",
                claim.job.job_id, claim.owner, claim.token)

    async def fail(self, claim: JobClaim, code: str) -> None:
        transient = code in TRANSIENT_ERRORS
        state = 'pending' if transient else ('failed' if code == 'archive_job_superseded' else 'blocked')
        delay = min(60 * (2 ** min(max(claim.job.attempts - 1, 0), 6)), 3600)
        await self.pool.execute(
            "UPDATE archive_job SET state=$4,last_error_code=$5,next_attempt_at=clock_timestamp()+$6*interval '1 second',"
            "lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() "
            "WHERE job_id=$1 AND lease_owner=$2 AND lease_token=$3 AND state='running'",
            claim.job.job_id, claim.owner, claim.token, state, code, delay)

    async def retry(self, job_id: UUID) -> ArchiveJob:
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                'SELECT j.*,d.data_revision,d.calendar_revision FROM archive_job j JOIN archive_dataset d USING(dataset_id) '
                'WHERE job_id=$1 FOR UPDATE OF j,d', job_id)
            if row is None:
                raise ArchiveError('archive_job_not_found', status_code=404)
            checkpoint = _json(row['checkpoint'])
            current = (int(checkpoint.get('data_revision', 0)) if row['kind'] == 'load'
                       else row['requested_revision'])
            if current != row['data_revision'] or checkpoint['calendar_revision'] != row['calendar_revision']:
                raise ArchiveError('archive_job_superseded')
            if row['state'] != 'failed' or row['last_error_code'] not in TRANSIENT_ERRORS:
                raise ArchiveError('archive_job_retry_not_allowed')
            row = await conn.fetchrow(
                "UPDATE archive_job SET state='pending',next_attempt_at=clock_timestamp(),updated_at=clock_timestamp() "
                'WHERE job_id=$1 RETURNING *', job_id)
            return _job(row)
