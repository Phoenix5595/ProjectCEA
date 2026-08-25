from datetime import UTC, datetime, timedelta

import pytest

from app.container import ServiceContainer
from app.control.control_engine import ControlEngine
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.schemas.monitoring_models import Phase, RuntimeSnapshotVersion
from app.services.photoperiod_history_logger import (
    PhotoperiodHistoryLogger,
    PhotoperiodObservation,
)


class FakeStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.rows: list[tuple[PhotoperiodObservation, ...]] = []

    async def append(self, rows: tuple[PhotoperiodObservation, ...]) -> None:
        if self.fail:
            raise RuntimeError("monitoring table unavailable")
        self.rows.append(rows)


class FakeSink:
    def __init__(self) -> None:
        self.observations: list[tuple[PhotoperiodObservation, bool]] = []

    def enqueue_final_phase(
        self, observation: PhotoperiodObservation, *, force: bool = False
    ) -> None:
        self.observations.append((observation, force))


class LifecycleComponent:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def stop(self) -> None:
        self.calls.append(f"{self.name}-stop")

    async def close(self) -> None:
        self.calls.append(f"{self.name}-close")


def observation(minute: int = 0) -> PhotoperiodObservation:
    return PhotoperiodObservation(
        observed_at=datetime(2026, 8, 2, tzinfo=UTC) + timedelta(minutes=minute),
        location="Veg Room",
        cluster="main",
        phase=Phase.MOON,
        mode_id=6,
        submode_id=None,
        runtime_snapshot_version=RuntimeSnapshotVersion(3),
    )


@pytest.mark.asyncio
async def test_post_authority_transition_flushes_exact_observation() -> None:
    # Given: a logger with a fake append-only store.
    store = FakeStore()
    logger = PhotoperiodHistoryLogger(store)

    # When: the first post-authority observation is enqueued and flushed.
    logger.enqueue_final_phase(observation(), force=True)
    await logger.flush_once()

    # Then: its mode provenance and snapshot identity are preserved.
    assert store.rows == [(observation(),)]


@pytest.mark.asyncio
async def test_heartbeat_enqueues_once_per_sixty_seconds() -> None:
    # Given: two observations inside one heartbeat period and one on its boundary.
    store = FakeStore()
    logger = PhotoperiodHistoryLogger(store)

    # When: normal moon-authority observations are enqueued.
    logger.enqueue_final_phase(observation())
    logger.enqueue_final_phase(observation(0))
    logger.enqueue_final_phase(observation(1))
    await logger.flush_once()

    # Then: the transition/heartbeat cadence stores the first and 60-second records only.
    assert store.rows == [(observation(), observation(1))]


@pytest.mark.asyncio
async def test_lifecycle_flush_stops_after_pending_rows_are_persisted() -> None:
    # Given: a started logger with a pending phase.
    store = FakeStore()
    logger = PhotoperiodHistoryLogger(store)
    logger.enqueue_final_phase(observation(), force=True)
    await logger.start()

    # When: shutdown requests its bounded final flush.
    await logger.stop()

    # Then: the row is persisted before the worker is stopped.
    assert store.rows == [(observation(),)]


@pytest.mark.asyncio
async def test_empty_registry_logger_start_and_flush_are_noops() -> None:
    # Given: no registry rooms have produced a phase observation.
    store = FakeStore()
    logger = PhotoperiodHistoryLogger(store)

    # When: its worker starts, flushes, and stops.
    await logger.start()
    await logger.flush_once()
    await logger.stop()

    # Then: no history write is attempted.
    assert store.rows == []


@pytest.mark.asyncio
async def test_lifecycle_flush_stops_logger_before_database_close() -> None:
    # Given: the shutdown dependencies record their lifecycle calls.
    calls: list[str] = []
    container = object.__new__(ServiceContainer)
    container.__dict__["background_tasks"] = LifecycleComponent("control", calls)
    container.__dict__["photoperiod_history_logger"] = LifecycleComponent("logger", calls)
    container.__dict__["monitoring_publication_workers"] = LifecycleComponent("publication", calls)
    container.__dict__["database"] = LifecycleComponent("database", calls)
    container.mcp23017 = None
    container.dfr0971_manager = None
    container._initialized = True

    # When: the container shuts down without starting a production service.
    await container.shutdown()

    # Then: control and publication workers stop before the database closes.
    assert calls == ["control-stop", "logger-stop", "publication-stop", "database-close"]


@pytest.mark.asyncio
async def test_queue_capacity_drops_the_257th_observation() -> None:
    # Given: a full fixed-capacity logger queue.
    logger = PhotoperiodHistoryLogger(FakeStore())

    # When: unique forced transition observations exceed capacity.
    for minute in range(257):
        logger.enqueue_final_phase(observation(minute), force=True)

    # Then: capacity is bounded and the final row is observable as dropped.
    assert logger.pending_count == 256
    assert logger.flush_health()[0].dropped_rows == 1


@pytest.mark.asyncio
async def test_missing_table_backoff_does_not_tight_loop() -> None:
    # Given: an unavailable append store and a queued transition.
    store = FakeStore(fail=True)
    logger = PhotoperiodHistoryLogger(store)
    logger.enqueue_final_phase(observation(), force=True)

    # When: flushing is attempted twice during the backoff window.
    await logger.flush_once()
    await logger.flush_once()

    # Then: the failed batch is counted once and no immediate retry occurs.
    assert logger.failed_flushes == 1
    assert logger.flush_health()[0].healthy is False


def test_overflow_counter_is_exposed_through_health_metadata() -> None:
    # Given: a full logger queue.
    logger = PhotoperiodHistoryLogger(FakeStore())
    for minute in range(257):
        logger.enqueue_final_phase(observation(minute), force=True)

    # When: route-facing health metadata is read.
    metadata = logger.health_metadata()

    # Then: it reports the lost row and the oldest pending timestamp.
    assert metadata.dropped_rows == 1
    assert metadata.oldest_pending_at == observation().observed_at


def test_db_failure_does_not_change_control_transition_capture() -> None:
    # Given: a control engine with a synchronous sink backed by an unavailable store.
    sink = FakeSink()
    engine = object.__new__(ControlEngine)
    engine.photoperiod_observation_sink = sink

    # When: control records the final moon-authority phase.
    engine._enqueue_final_photoperiod_phase(
        location="Veg Room",
        cluster="main",
        active_mode={"mode_id": 6, "submode_id": None},
        phase=Phase.MOON,
        snapshot=RuntimeDeviceSnapshot.create(
            version=3,
            hierarchy={},
            mode_parameters={},
            light_intensities={},
            light_programs=[],
        ),
        observed_at=observation().observed_at,
        force=True,
    )

    # Then: control returns synchronously with exact phase provenance.
    assert sink.observations == [(observation(), True)]
