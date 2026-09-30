from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from Infrastructure.shared.db_batch_writer import BatchQueue

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SERVICE_ROOT.parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_SERVICE_ROOT))

_WRITER_PATH = Path(__file__).resolve().parents[1] / "app" / "writer.py"
_SPEC = importlib.util.spec_from_file_location("_can_processor_writer_test", _WRITER_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"cannot load CAN writer from {_WRITER_PATH}")
_CAN_WRITER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _CAN_WRITER
_SPEC.loader.exec_module(_CAN_WRITER)


class FakeConnection:
    closed: bool

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class BatchQueueShutdownTests(unittest.TestCase):
    def test_data_writer_close_waits_for_active_and_queued_flushes(self) -> None:
        flush_started = threading.Event()
        release_flush = threading.Event()
        close_finished = threading.Event()
        flushed: list[object] = []

        def flush(items: list[object]) -> None:
            flush_started.set()
            if not release_flush.wait(5):
                raise TimeoutError("test did not release the blocked flush")
            flushed.extend(items)

        batch_queue = BatchQueue(flush, flush_threshold=1, flush_interval_sec=0.01)
        writer = _CAN_WRITER.DataWriter.__new__(_CAN_WRITER.DataWriter)
        connection = FakeConnection()
        metadata_worker_stop = threading.Event()
        metadata_worker = SimpleNamespace(
            stop=lambda: metadata_worker_stop.set(),
        )
        writer.__dict__.update(
            _metadata_worker=metadata_worker,
            _batch_queue=batch_queue,
            db_conn=connection,
            redis_client=None,
            redis_state_client=None,
            _redis_stream_pool=None,
            _redis_state_pool=None,
        )

        batch_queue.start()
        self.assertTrue(batch_queue.put("active measurement"))
        self.assertTrue(flush_started.wait(1))
        self.assertTrue(batch_queue.put("queued measurement"))

        with patch.object(_CAN_WRITER, "close_sync"):
            closer = threading.Thread(target=lambda: (writer.close(), close_finished.set()))
            closer.start()
            try:
                self.assertFalse(
                    close_finished.wait(2.2),
                    "DataWriter.close returned while a flush was still pending",
                )
                self.assertFalse(connection.closed, "database closed before all flushes completed")
            finally:
                release_flush.set()
                closer.join(2)

        self.assertFalse(closer.is_alive())
        self.assertTrue(close_finished.is_set())
        self.assertTrue(connection.closed)
        self.assertEqual(flushed, ["active measurement", "queued measurement"])
        self.assertEqual(batch_queue.stats()["flushed"], 2)
        self.assertEqual(batch_queue.stats()["dropped"], 0)

    def test_flush_failure_is_dropped_not_counted_as_flushed(self) -> None:
        def fail_flush(items: list[object]) -> None:
            self.assertEqual(items, ["measurement"])
            raise RuntimeError("database write failed")

        batch_queue = BatchQueue(fail_flush, flush_threshold=1, flush_interval_sec=0.01)
        batch_queue.start()
        self.assertTrue(batch_queue.put("measurement"))
        batch_queue.stop()

        stats = batch_queue.stats()
        self.assertEqual(stats["queued"], 1)
        self.assertEqual(stats["flushed"], 0)
        self.assertEqual(stats["dropped"], 1)
        self.assertEqual(stats["in_queue"], 0)


class FakeClock:
    """Scripted time module: independent monotonic/wall ticks per read."""

    def __init__(
        self,
        monotonic_ticks: list[float],
        wall_ticks: list[float],
        reads: list[tuple[str, float]],
    ) -> None:
        self._monotonic_ticks = list(monotonic_ticks)
        self._wall_ticks = list(wall_ticks)
        self._reads = reads

    def monotonic(self) -> float:
        value = self._monotonic_ticks.pop(0) if self._monotonic_ticks else 1.0
        self._reads.append(("monotonic", value))
        return value

    def time(self) -> float:
        value = self._wall_ticks.pop(0) if self._wall_ticks else 1.0
        self._reads.append(("time", value))
        return value


class BatchQueueDeadlineTests(unittest.TestCase):
    """Deadline semantics of BatchQueue._drain_up_to_threshold (step 5).

    The module-level ``time`` object is replaced by a scripted fake clock
    (restored in a finally), so no test sleeps and no wall-clock race exists.
    """

    def setUp(self) -> None:
        import shared.db_batch_writer as mod

        self._mod = mod
        self._saved_time = mod.time

    def tearDown(self) -> None:
        self._mod.time = self._saved_time

    def _run_drain(
        self,
        monotonic_ticks: list[float],
        wall_ticks: list[float],
        items_to_preload: list[str],
    ) -> tuple[list[str], list[tuple[str, float]]]:
        import queue as queue_mod

        reads: list[tuple[str, float]] = []
        q: queue_mod.Queue[str] = queue_mod.Queue()
        for item in items_to_preload:
            q.put(item)
        drain = BatchQueue.__new__(BatchQueue)
        drain._q = q
        drain._flush_threshold = 5
        drain._flush_interval_sec = 0.1
        self._mod.time = FakeClock(monotonic_ticks, wall_ticks, reads)
        try:
            return list(drain._drain_up_to_threshold()), reads
        finally:
            self._mod.time = self._saved_time

    def test_scripted_monotonic_progression_keeps_loop_open(self) -> None:
        # Given: interval 0.1, monotonic 0.00 -> 0.04 (remaining 0.06) and a
        # far-past-budget read afterwards; threshold never reached.
        items, _reads = self._run_drain(
            monotonic_ticks=[0.0, 0.04, 0.04, 1.0],
            wall_ticks=[1_000_000.0] * 4,
            items_to_preload=["a", "b"],
        )
        # Then: the loop keeps waiting past 0.04 and drains both items.
        self.assertEqual(items, ["a", "b"])

    def test_wall_clock_jump_does_not_change_timeout_decision(self) -> None:
        # Given: wall clock jumps forward 9s between the start read and the
        # post-get read (pre-edit time.time() expired early and dropped items).
        items, reads = self._run_drain(
            monotonic_ticks=[0.0, 0.0, 1.0],
            wall_ticks=[1_000.0, 1_009.0, 1_000.0],
            items_to_preload=["early"],
        )
        # Then: the monotonic deadline ignores the wall jump and keeps waiting
        # (the item IS drained), and the decision used monotonic reads only.
        self.assertEqual(items, ["early"])
        self.assertTrue(all(kind == "monotonic" for kind, _value in reads))

    def test_wall_clock_backward_jump_does_not_extend_budget(self) -> None:
        # Given: wall clock jumps backward between reads (pre-edit time.time()
        # inflated remaining by +5s and could wait far beyond the interval).
        items, reads = self._run_drain(
            monotonic_ticks=[0.0, 0.0, 1.0],
            wall_ticks=[1_000.0, 995.0, 1_000.0],
            items_to_preload=["only"],
        )
        # Then: the drain decision is the same monotonic behavior; the wall
        # ticks are never consulted for the deadline.
        self.assertEqual(items, ["only"])
        self.assertTrue(all(kind == "monotonic" for kind, _value in reads))

    def test_empty_queue_exits_at_first_past_budget_read(self) -> None:
        # Given: nothing queued; monotonic reads start 0.0 then past budget.
        items, _reads = self._run_drain(
            monotonic_ticks=[0.0, 1.0],
            wall_ticks=[1_000.0, 1_000.0],
            items_to_preload=[],
        )
        # Then: no items and one timed get attempt that raised Empty.
        self.assertEqual(items, [])

    def test_threshold_exit_drains_only_threshold_items(self) -> None:
        # Given: queue preloaded above the threshold; reads stay in-budget.
        items, _reads = self._run_drain(
            monotonic_ticks=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            wall_ticks=[1_000.0] * 6,
            items_to_preload=["a", "b", "c", "d", "e", "f"],
        )
        # Then: exactly threshold items drain in one pass.
        self.assertEqual(items, ["a", "b", "c", "d", "e"])


if __name__ == "__main__":
    _ = unittest.main()
