"""Bounded heavy-work coordination for synchronous Parquet/ZIP jobs.

All synchronous archive work (Parquet encoding and ZIP assembly) runs through
one bounded worker lane so the asyncio loop keeps
serving progress and graph APIs. Work pauses cooperatively at batch
boundaries while the publication-freshness guard reports stale monitoring
publications, and cancels cooperatively on shutdown so the owner can join the
writer before deleting its staging directory.

The worker callable receives a :class:`HeavyWorkControl` and must call
``control.checkpoint()`` between bounded batches. ``checkpoint()`` blocks
while a pause is requested and raises :class:`HeavyWorkCancelled` once
cancellation is requested.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
import asyncio
import threading
from collections.abc import Callable
from typing import Any, Final, TypeVar, final

from shared.infra_logging import get_logger

logger = get_logger(__name__)

INITIAL_PAUSE_REASON: Final = "freshness_pending"
_CANCEL_POLL_SECONDS: Final = 0.5

_PRIORITY_WARNING_LOCK = threading.Lock()
_PRIORITY_WARNING_LOGGED = False


def _lower_priority() -> None:
    global _PRIORITY_WARNING_LOGGED
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (PermissionError, OSError) as error:
        with _PRIORITY_WARNING_LOCK:
            if not _PRIORITY_WARNING_LOGGED:
                logger.warning(
                    "could not lower archive worker priority; duty-cycle throttling remains active: %s",
                    error,
                )
                _PRIORITY_WARNING_LOGGED = True

T = TypeVar("T")


class HeavyWorkCancelled(RuntimeError):
    """Raised inside the worker thread when cooperative cancellation is observed."""


@final
class HeavyWorkControl:
    """Thread-safe cooperative pause/cancel checkpoint for heavy archive work.

    The event loop (freshness guard, lifecycle jobs) mutates the state through
    :meth:`request_pause`, :meth:`allow_resume` and :meth:`request_cancel`; the
    worker thread observes it through :meth:`checkpoint`.

    The control starts paused with :data:`INITIAL_PAUSE_REASON` so no heavy
    work runs before the freshness guard has seen three consecutive fresh
    publications.
    """

    def __init__(
        self, *, duty_cycle: float = 1.0,
        initial_pause_reason: str | None = INITIAL_PAUSE_REASON,
    ) -> None:
        if not 0.05 <= duty_cycle <= 1.0:
            raise ValueError("archive duty cycle must be between 0.05 and 1.0")
        self._lock = threading.Lock()
        self._resume = threading.Event()
        self._cancelled = threading.Event()
        self._pause_reason = initial_pause_reason
        self._duty_cycle = duty_cycle
        self._busy_since = time.monotonic()
        if initial_pause_reason is None:
            self._resume.set()

    def request_pause(self, reason: str) -> None:
        """Pause heavy work at the next batch boundary."""
        with self._lock:
            self._pause_reason = reason
            self._resume.clear()

    def allow_resume(self) -> None:
        """Release a requested pause so heavy work may continue."""
        with self._lock:
            self._pause_reason = None
            self._resume.set()

    def request_cancel(self) -> None:
        """Cancel heavy work; also wakes a paused worker so it observes it."""
        self._cancelled.set()
        self._resume.set()

    def reset_for_new_job(self) -> None:
        """Clear a joined job's cancellation without changing freshness state."""
        with self._lock:
            self._cancelled.clear()
            if self._pause_reason is None:
                self._resume.set()
            else:
                self._resume.clear()
            self._busy_since = time.monotonic()

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._pause_reason is not None

    @property
    def pause_reason(self) -> str | None:
        with self._lock:
            return self._pause_reason

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def checkpoint(self) -> None:
        """Observe pause/cancel and cap worker CPU at a cooperative boundary."""
        paused = False
        while True:
            if self._cancelled.is_set():
                raise HeavyWorkCancelled("heavy work cancelled")
            if self._resume.is_set():
                break
            paused = True
            self._resume.wait(timeout=_CANCEL_POLL_SECONDS)

        now = time.monotonic()
        if paused:
            # A freshness pause already supplied idle time; exclude it from the
            # busy interval measured for the next duty-cycle wait.
            self._busy_since = now
            return
        busy = max(0.0, now - self._busy_since)
        delay = busy * (1.0 / self._duty_cycle - 1.0)
        if delay <= 0:
            self._busy_since = now
            return

        deadline = now + delay
        while True:
            if self._cancelled.is_set():
                raise HeavyWorkCancelled("heavy work cancelled")
            if not self._resume.is_set():
                while not self._resume.is_set():
                    if self._cancelled.is_set():
                        raise HeavyWorkCancelled("heavy work cancelled")
                    self._resume.wait(timeout=_CANCEL_POLL_SECONDS)
                self._busy_since = time.monotonic()
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._busy_since = time.monotonic()
                return
            # The cancellation event wakes this wait immediately; pause state
            # is checked at most half a second later.
            self._cancelled.wait(timeout=min(_CANCEL_POLL_SECONDS, remaining))


@final
class HeavyWorkExecutor:
    """Own one bounded worker thread for synchronous archive work.

    ``max_workers`` is the configured ``ARCHIVE_WORKER_CONCURRENCY`` (one).
    Submissions queue on the single lane; heavy work is sequential by design.
    """

    def __init__(self, *, max_workers: int = 1, duty_cycle: float = 1.0) -> None:
        if max_workers != 1:
            raise ValueError("archive heavy work requires exactly one worker")
        if not 0.05 <= duty_cycle <= 1.0:
            raise ValueError("archive duty cycle must be between 0.05 and 1.0")
        self._control = HeavyWorkControl(duty_cycle=duty_cycle)
        self._duty_cycle = duty_cycle
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="archive-heavy", initializer=_lower_priority,
        )
        self._lane = asyncio.Semaphore(1)
        self._lock = threading.Lock()
        self._closed = False
        self._running: asyncio.Future[Any] | None = None

    @property
    def control(self) -> HeavyWorkControl:
        """The cooperative pause/cancel checkpoint passed to worker callables."""
        return self._control

    async def run(self, work: Callable[[HeavyWorkControl], T]) -> T:
        """Offload one synchronous job to the bounded worker thread.

        The callable receives the :class:`HeavyWorkControl` and must checkpoint
        between bounded batches. Cancelling the awaiting task does not
        interrupt the thread: the job keeps running until it observes
        cancellation through the control, so callers must join via
        :meth:`wait_idle` before deleting the job's staging directory.
        """
        async with self._lane:
            if self._closed:
                raise HeavyWorkCancelled("heavy worker shut down")
            self._control.reset_for_new_job()
            future = asyncio.get_running_loop().run_in_executor(
                self._pool, work, self._control,
            )
            with self._lock:
                self._running = future
            future.add_done_callback(self._on_done)
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                self._control.request_cancel()
                await self.wait_idle()
                raise

    async def throttle_async(self, busy_seconds: float) -> None:
        """Yield database-side work in proportion to its measured wall time."""
        if self._control.cancelled:
            raise HeavyWorkCancelled("heavy work cancelled")
        if self._duty_cycle >= 1.0 or busy_seconds <= 0:
            return
        remaining = busy_seconds * (1.0 / self._duty_cycle - 1.0)
        while remaining > 0:
            if self._control.cancelled:
                raise HeavyWorkCancelled("heavy work cancelled")
            delay = min(_CANCEL_POLL_SECONDS, remaining)
            await asyncio.sleep(delay)
            remaining -= delay
        if self._control.cancelled:
            raise HeavyWorkCancelled("heavy work cancelled")

    async def wait_idle(self) -> None:
        """Wait for the currently running job to finish (join before cleanup).

        The job's own result/exception is delivered to whoever awaited
        :meth:`run`; a failed or cancelled job does not make the join itself
        fail, so callers can safely join before deleting staging.
        """
        with self._lock:
            running = self._running
        if running is None:
            return
        try:
            await asyncio.shield(running)
        except Exception:
            logger.debug("heavy work failed while joining", exc_info=True)

    async def shutdown(self) -> None:
        """Cancel running work, join the worker thread and release the executor."""
        self._closed = True
        self._control.request_cancel()
        await self.wait_idle()
        self._pool.shutdown(wait=True)

    def _on_done(self, future: asyncio.Future[Any]) -> None:
        # Retrieve the result/exception so a cancelled-and-abandoned job does
        # not log "exception was never retrieved", then clear the join handle.
        try:
            future.exception()
        except asyncio.CancelledError:
            pass
        with self._lock:
            if self._running is future:
                self._running = None
