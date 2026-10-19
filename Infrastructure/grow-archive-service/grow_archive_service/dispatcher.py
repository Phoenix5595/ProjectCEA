"""Single sequential dispatcher for the archive-specific durable job queue.

The dispatcher claims one fenced job at a time and routes all five job kinds to
real LifecycleService/ExportService handlers. ZIP Loads and explicit exports
remain available with lifecycle disabled; annual/expiry and remote cleanup work
is not selected until activation is enabled. Nongrow expiry uses the same lane
between durable jobs, driven by its SQL forward cursor.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from .jobs import ArchiveJobQueue
from .lifecycle import DISPATCH_POLL_SECONDS, LIFECYCLE_TICK_SECONDS, LifecycleService
from .models import ArchiveError, JobClaim, JobKind
from shared.infra_logging import get_logger

logger = get_logger(__name__)
Clock = Callable[[], datetime]

_ALL_KINDS: tuple[JobKind, ...] = (
    "export", "annual_merge", "load", "expire", "artifact_cleanup",
)
_DISABLED_KINDS: tuple[JobKind, ...] = ("load", "export")


def _aware_utc(clock: Clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("archive dispatcher clock must return an aware datetime")
    return value.astimezone(UTC)


class LifecycleScheduler:
    """One-minute durable-job planner; never performs source deletion."""

    def __init__(
        self,
        lifecycle: LifecycleService,
        *,
        interval_seconds: float = LIFECYCLE_TICK_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("lifecycle scheduler interval must be positive")
        self.lifecycle = lifecycle
        self.interval_seconds = float(interval_seconds)
        self.clock = clock or lifecycle.clock

    async def run(self, stop_event: asyncio.Event) -> None:
        """Sweep idle loads and plan on a monotonic one-minute cadence."""
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        while not stop_event.is_set():
            # Load expiry is housekeeping, not source retention. It remains
            # active with lifecycle disabled and while the job lane is busy.
            try:
                await self.lifecycle.export_service.expire_loads()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = getattr(error, "code", "archive_load_expiry_failed")
                logger.warning("archive load expiry failed code=%s", code)
            try:
                await self.lifecycle.run_lifecycle_tick(_aware_utc(self.clock))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = getattr(error, "code", "archive_lifecycle_tick_failed")
                logger.warning("lifecycle scheduler tick failed code=%s", code)
            deadline += self.interval_seconds
            now_mono = loop.time()
            if deadline <= now_mono:
                deadline = now_mono + self.interval_seconds
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(0.0, deadline - now_mono))
            except TimeoutError:
                pass


class ArchiveDispatcher:
    """One bounded sequential executor for export/annual/load/expire/cleanup."""

    def __init__(
        self,
        lifecycle: LifecycleService,
        *,
        owner: UUID | None = None,
        clock: Clock | None = None,
        poll_seconds: float = DISPATCH_POLL_SECONDS,
        nongrow_interval_seconds: float = LIFECYCLE_TICK_SECONDS,
    ) -> None:
        if poll_seconds <= 0 or nongrow_interval_seconds <= 0:
            raise ValueError("dispatcher intervals must be positive")
        self.lifecycle = lifecycle
        self.pool = lifecycle.pool
        self.queue: ArchiveJobQueue = lifecycle.queue
        self.export_service = lifecycle.export_service
        self.owner = owner or uuid4()
        self.clock = clock or lifecycle.clock
        self.poll_seconds = float(poll_seconds)
        self.nongrow_interval_seconds = float(nongrow_interval_seconds)
        self._next_nongrow_monotonic = 0.0
        self.handlers: Mapping[JobKind, Callable[[JobClaim], Awaitable[Any]]] = {
            "export": lifecycle.execute_export,
            "annual_merge": lifecycle.execute_annual_merge,
            "load": self.export_service.run_load,
            "expire": lifecycle.expire,
            "artifact_cleanup": lifecycle.execute_artifact_cleanup,
        }
        missing = [kind for kind in _ALL_KINDS if not callable(self.handlers.get(kind))]
        if missing:
            raise ValueError(f"archive dispatcher handlers are required for {missing}")

    async def run(self, stop_event: asyncio.Event) -> None:
        """Poll and execute at most one archive job/expiry batch at a time."""
        while not stop_event.is_set():
            if self.lifecycle._freshness_paused():
                await self._wait(stop_event)
                continue
            now = _aware_utc(self.clock)
            if self.lifecycle.enabled and asyncio.get_running_loop().time() >= self._next_nongrow_monotonic:
                self._next_nongrow_monotonic = (
                    asyncio.get_running_loop().time() + self.nongrow_interval_seconds
                )
                try:
                    result = await self.lifecycle.run_nongrow_expiry(now)
                    if result.deleted_rows or result.held:
                        logger.info(
                            "nongrow expiry deleted=%s held=%s",
                            result.deleted_rows, len(result.held),
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    code = getattr(error, "code", "archive_nongrow_expiry_failed")
                    logger.warning("nongrow expiry pass failed code=%s", code)
            try:
                claim = await self._claim_next(now)
            except asyncio.CancelledError:
                raise
            except asyncpg.PostgresError:
                logger.warning("archive queue claim failed code=archive_database_unavailable")
                await self._wait(stop_event)
                continue
            if stop_event.is_set():
                return
            if claim is None:
                await self._wait(stop_event)
                continue
            await self._run_claim(claim)

    async def _claim_next(self, now: datetime) -> JobClaim | None:
        if self.lifecycle.enabled:
            return await self.queue.claim(self.owner, now=now)
        # Keep queue.claim's ordinary unfiltered behavior unchanged. While the
        # destructive lifecycle is disabled, its optional job_id selects only
        # the explicit ZIP load/export operations.
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT job_id FROM public.archive_job WHERE "
                "((state='pending' AND next_attempt_at<=$1) OR (state='running' AND lease_until<=$1)) "
                "AND kind=ANY($2::text[]) ORDER BY next_attempt_at,created_at,job_id LIMIT 1",
                now, list(_DISABLED_KINDS),
            )
        if row is None:
            return None
        return await self.queue.claim(self.owner, now=now, job_id=row["job_id"])

    async def _run_claim(self, claim: JobClaim) -> None:
        handler = self.handlers.get(claim.job.kind)
        if handler is None:
            await self._mark_failed(claim, "archive_job_kind_invalid")
            return
        renewal_stop = asyncio.Event()
        renewal = asyncio.create_task(
            self._renew_lease(claim, renewal_stop),
            name=f"archive-lease-{claim.job.job_id}",
        )
        try:
            await handler(claim)
            await self.queue.succeed(claim)
        except asyncio.CancelledError:
            # Preserve the durable checkpoint; the 60-second lease expires and
            # the next process resumes from the last committed batch.
            raise
        except ArchiveError as error:
            await self._mark_failed(claim, error.code)
            logger.warning("archive job failed kind=%s code=%s", claim.job.kind, error.code)
        except (asyncpg.PostgresError, OSError):
            await self._mark_failed(claim, "archive_database_unavailable")
            logger.warning("archive job failed kind=%s code=archive_database_unavailable", claim.job.kind)
        except Exception as error:
            reported = getattr(error, "code", None)
            code = reported if isinstance(reported, str) and reported.replace("_", "").isalnum() else "archive_worker_failed"
            await self._mark_failed(claim, code)
            logger.warning("archive job failed kind=%s code=%s", claim.job.kind, code)
        finally:
            renewal_stop.set()
            await asyncio.gather(renewal, return_exceptions=True)

    async def _mark_failed(self, claim: JobClaim, code: str) -> None:
        try:
            await self.queue.fail(claim, code)
        except asyncio.CancelledError:
            raise
        except asyncpg.PostgresError:
            logger.warning("archive job failure persistence deferred kind=%s code=%s", claim.job.kind, code)

    async def _renew_lease(self, claim: JobClaim, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=20.0)
            except TimeoutError:
                try:
                    await self.queue.renew(claim)
                except ArchiveError as error:
                    logger.warning("archive lease renewal stopped code=%s", error.code)
                    return
                except asyncpg.PostgresError:
                    logger.warning("archive lease renewal stopped code=archive_database_unavailable")
                    return

    async def _wait(self, stop_event: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=self.poll_seconds)
        except TimeoutError:
            pass
