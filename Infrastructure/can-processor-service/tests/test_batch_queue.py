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


if __name__ == "__main__":
    _ = unittest.main()
