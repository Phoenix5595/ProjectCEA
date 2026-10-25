"""The recorded-control envelope build must never block the event loop."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest

from monitoring_service import control_history_repository
from monitoring_service.control_history_repository import ControlHistoryRepository
from monitoring_service.control_models import ControlHistoryEnvelope, ControlHistoryRange

NOW = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
HISTORY_RANGE = ControlHistoryRange(start=NOW - timedelta(hours=1), end=NOW)


class EmptyHistoryDatabase:
    """Answer watermark probes and return no recorded rows."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch(self, query: str, *_arguments: object) -> list[dict[str, object]]:
        self.queries.append(query)
        if "AS materialization_watermark" in query:
            return [{"materialization_watermark": NOW}]
        return []


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_history_build_runs_in_a_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a recording wrapper around the real envelope build
    build = control_history_repository.build_control_history_envelope
    build_threads: list[int] = []

    def recording_build(*args: object, **kwargs: object) -> ControlHistoryEnvelope:
        build_threads.append(threading.get_ident())
        return build(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        control_history_repository, "build_control_history_envelope", recording_build
    )

    # When: a room history read builds its envelope
    envelope = await ControlHistoryRepository(EmptyHistoryDatabase()).read(
        "Flower Room", HISTORY_RANGE
    )

    # Then: the CPU-bound build ran off the event loop thread
    assert isinstance(envelope, ControlHistoryEnvelope)
    assert build_threads
    assert build_threads[0] != threading.get_ident()


@pytest.mark.anyio
async def test_history_build_keeps_the_event_loop_serving_other_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a build that cannot finish until another coroutine gets the loop
    build = control_history_repository.build_control_history_envelope
    build_started = threading.Event()
    loop_served = threading.Event()

    def blocking_build(*args: object, **kwargs: object) -> ControlHistoryEnvelope:
        build_started.set()
        if not loop_served.wait(5.0):
            raise AssertionError("the envelope build held the event loop")
        return build(*args, **kwargs)  # type: ignore[arg-type]

    async def watcher() -> None:
        while not build_started.is_set():
            await asyncio.sleep(0.001)
        loop_served.set()

    monkeypatch.setattr(
        control_history_repository, "build_control_history_envelope", blocking_build
    )

    # When: a history read runs alongside another loop task
    envelope, _ = await asyncio.gather(
        ControlHistoryRepository(EmptyHistoryDatabase()).read("Flower Room", HISTORY_RANGE),
        watcher(),
    )

    # Then: the other task progressed while the build was in flight
    assert isinstance(envelope, ControlHistoryEnvelope)
