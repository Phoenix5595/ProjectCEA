"""Actual marked-DB queue proof, called by the lifecycle schema smoke."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from grow_archive_service.jobs import ArchiveJobQueue
from grow_archive_service.models import ArchiveError


async def exercise_queue(pool):
    queue = ArchiveJobQueue(pool)
    dataset = uuid4()
    await pool.execute(
        "INSERT INTO archive_dataset(dataset_id,kind,calendar_year,quarter,room,calendar_revision,data_revision,"
        "published_revision,state,available_from,available_to) VALUES($1,'veg_quarter',2040,1,'Veg Room',1,1,NULL,"
        "'pending','2040-01-01+00','2040-04-01+00')", dataset)
    now = datetime.now(UTC)
    first = await queue.enqueue(dataset, 'export', 1)
    repeated = await queue.enqueue(dataset, 'export', 1)
    assert first.job_id == repeated.job_id
    owner = uuid4()
    claimed = await queue.claim(owner, now=now + timedelta(seconds=1))
    assert claimed is not None and claimed.job.job_id == first.job_id
    assert await queue.claim(uuid4(), now=now + timedelta(seconds=2)) is None
    await queue.checkpoint(claimed, {'stream': 'measurement', 'last_occurrence': 7})
    await queue.renew(claimed)
    # Resume an abandoned claim, preserving its bounded capture checkpoint.
    await pool.execute("UPDATE archive_job SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=$1", first.job_id)
    resumed = await queue.claim(uuid4(), now=datetime.now(UTC))
    assert resumed is not None and resumed.token != claimed.token
    assert resumed.job.checkpoint['progress']['last_occurrence'] == 7
    try:
        async with pool.acquire() as conn:
            await queue.check_fence(conn, claimed)
    except ArchiveError as error:
        assert error.code == 'archive_job_superseded'
    else:
        raise AssertionError('expired owner could publish')
    await queue.fail(resumed, 'archive_transport_failed')
    pending = await queue.get(first.job_id)
    assert pending.state == 'pending'
    delay = (pending.next_attempt_at - datetime.now(UTC)).total_seconds()
    assert 118 < delay <= 121  # second claim: retry doubles from 60 seconds
    assert await queue.claim(uuid4(), now=datetime.now(UTC)) is None
    await pool.execute("UPDATE archive_job SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE job_id=$1", first.job_id)
    revised_claim = await queue.claim(uuid4(), now=datetime.now(UTC))
    assert revised_claim is not None
    await pool.execute('UPDATE archive_dataset SET data_revision=data_revision+1 WHERE dataset_id=$1', dataset)
    try:
        await queue.checkpoint(revised_claim, {'invalid': True})
    except ArchiveError as error:
        assert error.code == 'archive_job_superseded'
    else:
        raise AssertionError('old revision could publish')
    assert await queue.claim(uuid4(), now=datetime.now(UTC)) is None
    assert (await queue.get(first.job_id)).last_error_code == 'archive_job_superseded'
    blocked = await queue.enqueue(dataset, 'export', 2, required_closed=False)
    blocked_claim = await queue.claim(uuid4(), now=datetime.now(UTC) + timedelta(seconds=1))
    assert blocked_claim is not None and blocked_claim.job.job_id == blocked.job_id
    await queue.fail(blocked_claim, 'archive_integrity_failed')
    assert (await queue.get(blocked.job_id)).state == 'blocked'
    try:
        await queue.retry(blocked.job_id)
    except ArchiveError as error:
        assert error.code == 'archive_job_retry_not_allowed'
    else:
        raise AssertionError('integrity conflict was force retried')
    print('Durable queue replay/lease CAS/revision fence/backoff/conflict proof passed')
