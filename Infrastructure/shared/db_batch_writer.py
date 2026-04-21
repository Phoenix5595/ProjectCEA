"""Shared batch-writing helpers for TimescaleDB.

Two orthogonal utilities in one module:

1. ``BatchQueue`` — a bounded-queue + background-thread flush primitive for
   high-rate SYNC producers (e.g. the CAN bus firehose). The caller supplies
   a ``flush_callback(items)`` that does the actual DB work; BatchQueue
   owns the queue lifecycle, flush timing, and drop/flush/queued metrics.

2. ``insert_measurements_async`` — an asyncpg helper that takes a pool and
   a list of ``(time, sensor_id, value, status)`` rows and does the canonical
   ``INSERT INTO measurement ... ON CONFLICT (time, sensor_id) DO UPDATE``
   via ``executemany``. Replaces the per-sensor ``conn.execute()`` loops
   in ``soil-sensor-service`` and ``weather-service``.

Why two utilities instead of one "universal batch writer":
- The services that NEED a queue/flush-thread (can-processor, ~12 inserts/s
  continuous) and the ones that don't (soil ~0.2/s, weather ~0.002/s) also
  happen to be split across the sync/async DB-driver boundary. Merging
  them forces either an awkward dual-API on the class or a thread-to-
  asyncio bridge neither side actually wants.
- Both utilities target the same fundamental win: turn N individual
  roundtrips into 1 ``executemany``. BatchQueue gathers the N; the soil /
  weather helper just formats the rows into executemany shape.

Usage — sync producer with queue + background flush (can-processor)::

    def _flush(items: list[DBWriteItem]) -> None:
        # prefetch FKs, do execute_batch(...), etc.
        ...

    queue_ = BatchQueue(
        flush_callback=_flush,
        max_queue=10_000,
        flush_threshold=50,
        flush_interval_sec=0.1,
        name="can-db",
    )
    queue_.start()
    try:
        for msg in producer():
            queue_.put(DBWriteItem(...))
    finally:
        queue_.stop()

Usage — async low-rate producer (soil, weather)::

    await insert_measurements_async(pool, [
        (ts, sensor_id_temp, 21.4, "ok"),
        (ts, sensor_id_rh,   47.8, "ok"),
        (ts, sensor_id_ec,   1.25, "ok"),
        (ts, sensor_id_ph,   6.30, "ok"),
    ])
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
import queue
import threading
import time
from typing import TYPE_CHECKING, Any, TypeVar

from shared.infra_logging import get_logger

# asyncpg is imported lazily inside insert_measurements_async(). The sync
# BatchQueue path does not need it, which matters for services like
# can-processor that install psycopg2 only.
if TYPE_CHECKING:
    import asyncpg

logger = get_logger(__name__)


T = TypeVar("T")


# ---------------------------------------------------------------------------
# BatchQueue (sync, thread-backed, for high-rate producers)
# ---------------------------------------------------------------------------


class BatchQueue:
    """Bounded in-memory queue with a background flush thread.

    Producers push individual items via :meth:`put`; a background worker
    drains up to ``flush_threshold`` items (or waits at most
    ``flush_interval_sec``) then calls ``flush_callback(items)``.

    Thread safety: :meth:`put` is safe to call from any thread. The
    background worker is the only caller of ``flush_callback``; the
    callback does NOT need to be thread-safe against itself.

    Overflow handling: if the queue is full when :meth:`put` is called,
    the item is dropped and the drop counter is incremented. This is
    intentional — it bounds memory even when the DB is unavailable, which
    is strictly preferable to an unbounded queue that eventually OOM-kills
    the service. Dropped items are also not retried (the assumption is
    that the producer either tolerates data loss or is running in a
    backpressure-friendly mode — e.g. CAN bus, where missing a sensor
    sample for a cycle is fine).

    Metrics: :meth:`stats` returns ``{"queued": N, "flushed": N, "dropped": N}``
    cumulative since construction.
    """

    def __init__(
        self,
        flush_callback: Callable[[list[Any]], None],
        *,
        max_queue: int = 10_000,
        flush_threshold: int = 50,
        flush_interval_sec: float = 0.1,
        name: str = "batch-queue",
    ) -> None:
        if max_queue <= 0:
            raise ValueError("max_queue must be > 0")
        if flush_threshold <= 0:
            raise ValueError("flush_threshold must be > 0")
        if flush_interval_sec <= 0:
            raise ValueError("flush_interval_sec must be > 0")

        self._flush_callback = flush_callback
        self._flush_threshold = flush_threshold
        self._flush_interval_sec = flush_interval_sec
        self._name = name

        self._q: queue.Queue[Any] = queue.Queue(maxsize=max_queue)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._queued_count = 0
        self._flushed_count = 0
        self._dropped_count = 0
        self._counter_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background flush worker. Idempotent."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()
        logger.info(
            f"BatchQueue[{self._name}] started "
            f"(threshold={self._flush_threshold}, interval={self._flush_interval_sec}s, "
            f"max_queue={self._q.maxsize})"
        )

    def stop(self, drain_timeout_sec: float = 5.0) -> None:
        """Signal the worker to stop, drain remaining items, and join.

        ``drain_timeout_sec`` bounds the total time spent draining so
        shutdown can't hang on a slow DB. Items still in the queue after
        the timeout are dropped (and counted).
        """
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=drain_timeout_sec)
        if thread.is_alive():
            logger.warning(
                f"BatchQueue[{self._name}] flush thread did not join within "
                f"{drain_timeout_sec}s; {self._q.qsize()} items in queue will be dropped"
            )
        # Count anything still in the queue as dropped on shutdown.
        with self._counter_lock:
            self._dropped_count += self._q.qsize()

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def put(self, item: Any) -> bool:
        """Enqueue an item without blocking.

        Returns True if queued, False if the queue was full and the item
        was dropped.
        """
        try:
            self._q.put_nowait(item)
        except queue.Full:
            with self._counter_lock:
                self._dropped_count += 1
            return False
        with self._counter_lock:
            self._queued_count += 1
        return True

    def stats(self) -> dict[str, int]:
        with self._counter_lock:
            return {
                "queued": self._queued_count,
                "flushed": self._flushed_count,
                "dropped": self._dropped_count,
                "in_queue": self._q.qsize(),
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                items = self._drain_up_to_threshold()
                if items:
                    self._flush(items)
            except Exception as e:  # pragma: no cover — flush loop must never die
                logger.error(f"BatchQueue[{self._name}] flush loop error: {e}")
                time.sleep(self._flush_interval_sec)

        # Final drain on shutdown: pull everything remaining and flush.
        # We do NOT honor drain_timeout_sec here — that's the caller's
        # concern (handled in stop() via thread.join timeout).
        tail: list[Any] = []
        while True:
            try:
                tail.append(self._q.get_nowait())
                self._q.task_done()
            except queue.Empty:
                break
        if tail:
            try:
                self._flush(tail)
            except Exception as e:
                logger.error(f"BatchQueue[{self._name}] final-drain flush error: {e}")

    def _drain_up_to_threshold(self) -> list[Any]:
        """Pull items from the queue until we have ``flush_threshold`` items
        or ``flush_interval_sec`` elapses, whichever comes first."""
        items: list[Any] = []
        start_ts = time.time()
        while len(items) < self._flush_threshold:
            remaining = self._flush_interval_sec - (time.time() - start_ts)
            if remaining <= 0:
                break
            try:
                item = self._q.get(timeout=remaining)
                items.append(item)
                self._q.task_done()
            except queue.Empty:
                break
        return items

    def _flush(self, items: list[Any]) -> None:
        try:
            self._flush_callback(items)
            with self._counter_lock:
                self._flushed_count += len(items)
        except Exception as e:
            # Callback failures count as drops — the flush thread keeps
            # running and the caller can see the failure via stats().
            logger.error(
                f"BatchQueue[{self._name}] flush callback raised: {e} "
                f"(dropping batch of {len(items)})"
            )
            with self._counter_lock:
                self._dropped_count += len(items)


# ---------------------------------------------------------------------------
# insert_measurements_async (async, for soil / weather / future asyncpg sites)
# ---------------------------------------------------------------------------

MEASUREMENT_UPSERT_SQL = """
INSERT INTO measurement (time, sensor_id, value, status)
VALUES ($1, $2, $3, $4)
ON CONFLICT (time, sensor_id) DO UPDATE
SET value = EXCLUDED.value, status = EXCLUDED.status
""".strip()


MeasurementRow = tuple[datetime, int, float, str]


async def insert_measurements_async(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    rows: Sequence[MeasurementRow] | Iterable[MeasurementRow],
) -> int:
    """Insert (or upsert) many measurement rows in a single roundtrip.

    ``rows`` is any iterable of ``(time, sensor_id, value, status)`` tuples.
    ``pool_or_conn`` may be either a :class:`asyncpg.Pool` (in which case
    a connection is acquired for the duration of the call) or an already-
    acquired :class:`asyncpg.Connection` (useful when the caller already
    holds a transaction).

    Returns the number of rows sent to the database. ``None``/skipped rows
    must be filtered out by the caller — this helper does not second-guess
    what a measurement is.

    Raises: anything asyncpg raises. Callers should wrap in try/except if
    they want the swallow-and-log semantics the pre-lift code had.
    """
    import asyncpg  # lazy import — see module-level comment

    rows_list: list[MeasurementRow] = list(rows)
    if not rows_list:
        return 0

    if isinstance(pool_or_conn, asyncpg.Pool):
        async with pool_or_conn.acquire() as conn:
            await conn.executemany(MEASUREMENT_UPSERT_SQL, rows_list)
    else:
        await pool_or_conn.executemany(MEASUREMENT_UPSERT_SQL, rows_list)

    return len(rows_list)


__all__ = [
    "BatchQueue",
    "MEASUREMENT_UPSERT_SQL",
    "MeasurementRow",
    "insert_measurements_async",
]
