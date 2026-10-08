"""Pure behavior tests for the changes-only photoperiod history writer.

The real ``PhotoperiodHistoryLogger`` and the real ``ControlEngine`` control-tick
path run against an in-memory store with stubbed sensor/climate/device boundaries.
No hardware manager, container, or production database participates.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.control.control_engine import ControlEngine
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.schemas.monitoring_models import (
    Phase,
    PhotoperiodObservationSink,
    RuntimeSnapshotVersion,
)
from app.services.photoperiod_history_logger import (
    BACKOFF_INTERVAL,
    QUEUE_CAPACITY,
    PhotoperiodCoverageObservation,
    PhotoperiodHistoryLogger,
    PhotoperiodHistoryRow,
    PhotoperiodObservation,
    PhotoperiodRecorderState,
)

T0 = datetime(2026, 8, 2, tzinfo=UTC)
ROOM = "Veg Room"
CLUSTER = "main"
FLOWER = "Flower Room"


class _SignalClock:
    """Injectable wall clock for deterministic observation times."""

    def __init__(self, value: datetime = T0) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def observation(
    offset_seconds: float = 0.0,
    *,
    phase: Phase = Phase.SUN,
    mode_id: int | None = 6,
    submode_id: int | None = None,
    version: int = 3,
    location: str = ROOM,
    cluster: str = CLUSTER,
) -> PhotoperiodObservation:
    return PhotoperiodObservation(
        observed_at=T0 + timedelta(seconds=offset_seconds),
        location=location,
        cluster=cluster,
        phase=phase,
        mode_id=mode_id,
        submode_id=submode_id,
        runtime_snapshot_version=RuntimeSnapshotVersion(version),
    )


def coverage_row(
    offset_seconds: float,
    state: str,
    reason: str,
    *,
    version: int = 3,
    location: str = ROOM,
    cluster: str = CLUSTER,
) -> PhotoperiodCoverageObservation:
    return PhotoperiodCoverageObservation(
        observed_at=T0 + timedelta(seconds=offset_seconds),
        location=location,
        cluster=cluster,
        state=state,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
        runtime_snapshot_version=RuntimeSnapshotVersion(version),
    )


def recorder_state(
    *,
    phase: Phase | None = Phase.SUN,
    phase_at: float | None = 0.0,
    coverage_state: str | None = "available",
    coverage_at: float | None = 0.0,
    version: int = 3,
    location: str = ROOM,
    cluster: str = CLUSTER,
) -> PhotoperiodRecorderState:
    return PhotoperiodRecorderState(
        location=location,
        cluster=cluster,
        phase=phase,
        phase_observed_at=(
            None if phase is None else T0 + timedelta(seconds=phase_at or 0.0)
        ),
        coverage_state=coverage_state,  # type: ignore[arg-type]
        coverage_observed_at=(
            None
            if coverage_state is None
            else T0 + timedelta(seconds=coverage_at or 0.0)
        ),
        runtime_snapshot_version=RuntimeSnapshotVersion(version),
    )


class FakeStore:
    """In-memory behavior stand-in for the append-only history store."""

    def __init__(
        self,
        *,
        records: tuple[PhotoperiodRecorderState, ...] = (),
        fail_state_read: bool = False,
        fail_append: bool = False,
    ) -> None:
        self.records = list(records)
        self.fail_state_read = fail_state_read
        self.fail_append = fail_append
        self.state_read_calls = 0
        self.append_calls: list[tuple[PhotoperiodHistoryRow, ...]] = []

    async def append(self, rows: tuple[PhotoperiodHistoryRow, ...]) -> None:
        if self.fail_append:
            raise RuntimeError("monitoring tables unavailable")
        self.append_calls.append(rows)

    async def read_state(self) -> tuple[PhotoperiodRecorderState, ...]:
        self.state_read_calls += 1
        if self.fail_state_read:
            raise RuntimeError("photoperiod history state read unavailable")
        return tuple(self.records)

    def flat_rows(self) -> list[PhotoperiodHistoryRow]:
        return [row for batch in self.append_calls for row in batch]


def _start_test_logger(
    store: FakeStore,
    *,
    clock: _SignalClock | None = None,
    shutdown_timeout: float = 5.0,
) -> PhotoperiodHistoryLogger:
    return PhotoperiodHistoryLogger(
        store,
        now=clock if clock is not None else _SignalClock(T0),
        shutdown_timeout=shutdown_timeout,
    )


async def await_seeded(logger: PhotoperiodHistoryLogger) -> None:
    """Yield until the worker has adopted persisted state before its first append."""
    for _ in range(30_000):
        if getattr(logger, "_seeded", False):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("photoperiod history worker never finished seeding")


def assert_queue_bounded(logger: PhotoperiodHistoryLogger) -> None:
    """Required invariant: queued/in-flight rows never exceed the 256-row bound."""
    assert logger.pending_count <= QUEUE_CAPACITY


@pytest.mark.asyncio
async def test_repeated_same_phase_across_minute_and_day_yields_one_initial_phase() -> None:
    # Given: a logger whose store has never seen a new-source phase for the room.
    store = FakeStore()
    logger = _start_test_logger(store)

    # When: the same SUN phase is resolved across a minute and a day boundary.
    logger.enqueue_final_phase(observation(0))
    logger.enqueue_final_phase(observation(61))
    logger.enqueue_final_phase(observation(24 * 3600))
    await logger.start()
    await await_seeded(logger)
    await logger.flush_once()
    await logger.stop()

    # Then: only the initial coverage boundary and the one initial phase exist.
    assert store.flat_rows() == [
        coverage_row(0, "available", "initial"),
        observation(0),
        coverage_row(0, "unavailable", "stopped"),
    ]


@pytest.mark.asyncio
async def test_two_genuine_phase_changes_one_second_apart_both_survive() -> None:
    store = FakeStore()
    logger = _start_test_logger(store)

    logger.enqueue_final_phase(observation(0))
    logger.enqueue_final_phase(observation(1, phase=Phase.MOON))
    logger.enqueue_final_phase(observation(2))
    await logger.start()
    await await_seeded(logger)
    await logger.flush_once()
    await logger.stop()

    assert store.flat_rows() == [
        coverage_row(0, "available", "initial"),
        observation(0),
        observation(1, phase=Phase.MOON),
        observation(2),
        coverage_row(0, "unavailable", "stopped"),
    ]


@pytest.mark.asyncio
async def test_same_phase_mode_submode_and_config_changes_write_no_phase_row() -> None:
    store = FakeStore()
    logger = _start_test_logger(store)

    logger.enqueue_final_phase(observation(0))
    logger.enqueue_final_phase(observation(10, mode_id=7, submode_id=2))
    logger.enqueue_final_phase(observation(20, mode_id=7, version=4))
    await logger.start()
    await await_seeded(logger)
    await logger.flush_once()
    await logger.stop()

    assert store.flat_rows() == [
        coverage_row(0, "available", "initial"),
        observation(0),
        coverage_row(0, "unavailable", "stopped"),
    ]


@pytest.mark.asyncio
async def test_unknown_phase_offer_only_closes_coverage_without_phase_row() -> None:
    store = FakeStore()
    logger = _start_test_logger(store)

    logger.enqueue_final_phase(observation(0))
    logger.enqueue_final_phase(observation(60, phase=Phase.UNKNOWN, mode_id=None))
    logger.enqueue_final_phase(observation(120))
    await logger.start()
    await await_seeded(logger)
    await logger.flush_once()
    await logger.stop()

    assert store.flat_rows() == [
        coverage_row(0, "available", "initial"),
        observation(0),
        coverage_row(60, "unavailable", "control_failure"),
        coverage_row(120, "available", "started"),
        coverage_row(0, "unavailable", "stopped"),
    ]


@pytest.mark.asyncio
async def test_seeded_same_phase_restart_writes_no_phase_duplicate() -> None:
    store = FakeStore(
        records=(
            recorder_state(phase=Phase.SUN),  # phase at T0 and coverage at T0
        ),
    )
    logger = _start_test_logger(store)

    logger.enqueue_final_phase(observation(120))
    logger.enqueue_final_phase(observation(121, phase=Phase.MOON))
    await logger.start()
    await await_seeded(logger)
    await logger.flush_once()
    await logger.stop()

    assert store.flat_rows() == [
        coverage_row(0, "unavailable", "unclean_restart"),
        coverage_row(120, "available", "started"),
        observation(121, phase=Phase.MOON),
        coverage_row(0, "unavailable", "stopped"),
    ]


@pytest.mark.asyncio
async def test_idle_start_flush_and_stop_write_nothing() -> None:
    store = FakeStore()
    logger = _start_test_logger(store)

    await logger.start()
    await await_seeded(logger)
    await logger.flush_once()
    await logger.stop()

    assert store.append_calls == []


@pytest.mark.asyncio
async def test_empty_run_does_not_close_seeded_rooms_it_never_observed() -> None:
    store = FakeStore(records=(recorder_state(),))
    logger = _start_test_logger(store)

    await logger.start()
    await await_seeded(logger)
    await logger.stop()

    assert store.flat_rows() == []


@pytest.mark.asyncio
async def test_stop_persists_pending_phase_and_closes_room_coverage() -> None:
    store = FakeStore()
    clock = _SignalClock(T0 + timedelta(hours=2))
    logger = _start_test_logger(store, clock=clock)

    logger.enqueue_final_phase(observation(0))
    await logger.start()
    await await_seeded(logger)
    await logger.stop()

    assert store.flat_rows() == [
        coverage_row(0, "available", "initial"),
        observation(0),
        coverage_row(2 * 3600, "unavailable", "stopped"),
    ]
    assert logger.pending_count == 0
    assert logger.flush_health()[0].healthy is True


@pytest.mark.asyncio
async def test_failed_state_read_blocks_appends_but_keeps_rows_queued() -> None:
    store = FakeStore(fail_state_read=True)
    logger = _start_test_logger(store, shutdown_timeout=0.1)

    await logger.start()
    await await_state_read_attempted(store)
    logger.enqueue_final_phase(observation(0))
    logger.enqueue_final_phase(observation(60, phase=Phase.MOON))
    await logger.flush_once()

    # Then: nothing is fabricated (no append, no seed) while facts stay queued.
    assert store.append_calls == []
    assert store.state_read_calls >= 1
    assert logger.pending_count >= 1
    assert getattr(logger, "_seeded", False) is False
    await logger.stop()
    assert store.append_calls == []


@pytest.mark.asyncio
async def test_failed_append_retains_batch_then_retries_in_order() -> None:
    store = FakeStore()
    clock = _SignalClock(T0)
    logger = _start_test_logger(store, clock=clock)

    await logger.start()
    await await_seeded(logger)
    store.fail_append = True
    logger.enqueue_final_phase(observation(0))
    await logger.flush_once()
    logger.enqueue_final_phase(observation(1, phase=Phase.MOON))

    # Then: the failed attempt retained both rows and rejected nothing.
    assert logger.pending_count == 3
    assert_queue_bounded(logger)
    assert logger.failed_flushes == 1
    assert logger.flush_health()[0].dropped_rows == 0
    assert store.append_calls == []

    # When: the outage clears and the retry window opens.
    store.fail_append = False
    clock.advance(BACKOFF_INTERVAL.total_seconds() + 1)
    await logger.flush_once()

    assert store.flat_rows() == [
        coverage_row(0, "available", "initial"),
        observation(0),
        observation(1, phase=Phase.MOON),
    ]
    assert logger.pending_count == 0
    await logger.stop()


@pytest.mark.asyncio
async def test_capacity_rejection_queues_gap_boundaries_atomically() -> None:
    store = FakeStore(
        records=(recorder_state(phase=Phase.SUN),),
    )
    clock = _SignalClock(T0)
    logger = _start_test_logger(store, clock=clock)
    await logger.start()
    await await_seeded(logger)

    # When: genuine transitions fill the bounded queue while persistence cannot
    # run, the first rejected observation is remembered as the explicit gap.
    phase = Phase.MOON
    offset = 1
    offered = 0
    while logger.pending_count < QUEUE_CAPACITY:
        logger.enqueue_final_phase(observation(offset, phase=phase))
        offered += 1
        phase = Phase.SUN if phase == Phase.MOON else Phase.MOON
        offset += 1
        assert_queue_bounded(logger)
    assert logger.pending_count == QUEUE_CAPACITY
    assert logger.flush_health()[0].dropped_rows == 0

    first_rejected_at = offset
    logger.enqueue_final_phase(observation(first_rejected_at, phase=phase))
    assert logger.flush_health()[0].dropped_rows == 1
    logger.enqueue_final_phase(observation(first_rejected_at + 1, phase=phase))
    assert logger.flush_health()[0].dropped_rows == 2
    assert logger.pending_count == QUEUE_CAPACITY
    assert_queue_bounded(logger)

    # When: space exists again, the recovery packet is complete or nothing.
    await logger.flush_once()
    assert_queue_bounded(logger)
    recovery_offset = first_rejected_at + 2
    logger.enqueue_final_phase(observation(recovery_offset, phase=phase))
    while logger.pending_count > 0:
        await logger.flush_once()
    clock.value = T0 + timedelta(seconds=recovery_offset + 1)
    await logger.stop()

    flat = store.flat_rows()
    gap_positions = [
        index
        for index, row in enumerate(flat)
        if isinstance(row, PhotoperiodCoverageObservation)
        and row.reason == "recording_gap"
    ]
    assert len(gap_positions) == 1
    gap_index = gap_positions[0]
    assert flat[gap_index : gap_index + 3] == [
        coverage_row(first_rejected_at, "unavailable", "recording_gap"),
        observation(recovery_offset, phase=Phase.MOON),
        coverage_row(recovery_offset, "available", "recovered"),
    ]
    assert flat[-1] == coverage_row(recovery_offset + 1, "unavailable", "stopped")
    rejected_times = {
        T0 + timedelta(seconds=first_rejected_at),
        T0 + timedelta(seconds=first_rejected_at + 1),
    }
    assert not any(
        isinstance(row, PhotoperiodObservation) and row.observed_at in rejected_times
        for row in flat
    )


@pytest.mark.asyncio
async def test_failed_shutdown_remains_bounded_and_reports_unhealthy() -> None:
    store = FakeStore(fail_append=True)
    clock = _SignalClock(T0)
    logger = _start_test_logger(store, clock=clock, shutdown_timeout=0.2)

    await logger.start()
    await await_seeded(logger)
    logger.enqueue_final_phase(observation(0))

    started = time.monotonic()
    await logger.stop()
    elapsed = time.monotonic() - started

    # Then: shutdown returned promptly, kept the rows queued, and exposed the
    # failure through health, without fabricating a persisted closure.
    assert elapsed < 2.0
    assert store.append_calls == []
    assert logger.pending_count == 3
    assert_queue_bounded(logger)
    assert logger.failed_flushes >= 1
    assert logger.flush_health()[0].healthy is False


class _WaitingStore(FakeStore):
    """Store whose first append blocks until released, for flush-order regressions."""

    def __init__(
        self,
        *,
        records: tuple[PhotoperiodRecorderState, ...] = (),
        fail_state_read: bool = False,
        fail_append: bool = False,
    ) -> None:
        super().__init__(
            records=records, fail_state_read=fail_state_read, fail_append=fail_append
        )
        self.release = asyncio.Event()
        self.append_started = asyncio.Event()
        self.append_calls: list[tuple[PhotoperiodHistoryRow, ...]] = []

    async def append(self, rows: tuple[PhotoperiodHistoryRow, ...]) -> None:
        if self.fail_append:
            raise RuntimeError("monitoring tables unavailable")
        self.append_started.set()
        await self.release.wait()
        await super().append(rows)


@pytest.mark.asyncio
async def test_manual_flush_never_duplicates_an_in_flight_worker_batch() -> None:
    store = _WaitingStore()
    logger = _start_test_logger(store)

    await logger.start()
    await await_seeded(logger)
    logger.enqueue_final_phase(observation(0))

    # When: the worker's append is in flight, a parallel manual flush waits on
    # the same batch instead of peeking a duplicate head.
    await asyncio.wait_for(store.append_started.wait(), 2.0)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(logger.flush_once(), 0.05)

    store.release.set()
    await logger.flush_once()

    # Then: the mixed flush attempt produced exactly one persisted batch.
    assert store.append_calls == [
        (coverage_row(0, "available", "initial"), observation(0)),
    ]
    assert logger.pending_count == 0
    assert logger.flush_health()[0].dropped_rows == 0
    await logger.stop()


@pytest.mark.asyncio
async def test_flush_never_splits_a_boundary_packet_across_batches() -> None:
    store = FakeStore()
    other_room = "Clone Room"
    logger = _start_test_logger(store)

    await logger.start()
    await await_seeded(logger)

    # Room A's first packet (coverage+phase) leaves the queue first.
    logger.enqueue_final_phase(observation(0))
    await logger.flush_once()
    for index in range(1, 64):
        phase = Phase.MOON if index % 2 else Phase.SUN
        logger.enqueue_final_phase(observation(index, phase=phase))
    # Room B's first offer is a two-row packet queued behind 63 single rows.
    logger.enqueue_final_phase(observation(0, location=other_room))

    await logger.flush_once()
    await logger.flush_once()

    # Then: batches are unions of whole packets; no coverage+phase pair splits.
    assert store.append_calls[0] == (coverage_row(0, "available", "initial"), observation(0))
    assert len(store.append_calls[1]) == 63
    assert all(row.location == ROOM for row in store.append_calls[1])
    for batch in store.append_calls:
        pair_positions = [
            index
            for index, row in enumerate(batch)
            if row.location == other_room
        ]
        assert len(pair_positions) in (0, 2)
        if len(pair_positions) == 2:
            assert pair_positions[1] == pair_positions[0] + 1
    assert logger.pending_count == 0
    await logger.stop()


@pytest.mark.asyncio
async def test_preseed_rejection_keeps_gap_alive_until_a_recovery_packet_fits() -> None:
    store = FakeStore(fail_state_read=True)
    logger = _start_test_logger(store, shutdown_timeout=0.2)

    await logger.start()
    await await_state_read_attempted(store)
    assert_queue_bounded(logger)

    # When: capacity rejects pre-seed intents, the intent is not treated as
    # queued and later repeats are rejected too, remembering the earliest gap.
    # The first observation reserves the max startup packet cost (three rows),
    # later buffered changes reserve one row; the loop fills by reservation.
    phase = Phase.MOON
    offset = 1
    while logger.pending_count < QUEUE_CAPACITY:
        logger.enqueue_final_phase(observation(offset, phase=phase))
        phase = Phase.SUN if phase == Phase.MOON else Phase.MOON
        offset += 1
        assert_queue_bounded(logger)
    assert logger.pending_count == QUEUE_CAPACITY
    logger.enqueue_final_phase(observation(offset, phase=phase))  # rejected
    assert logger.flush_health()[0].dropped_rows == 1
    assert_queue_bounded(logger)
    logger.enqueue_final_phase(observation(offset + 1, phase=phase))  # repeats are rejected
    assert logger.flush_health()[0].dropped_rows == 2
    assert_queue_bounded(logger)

    # When: seeding succeeds later, buffered facts replay and the gap survives;
    # all accepted intents materialize without extra loss.
    store.fail_state_read = False
    await await_seeded(logger)
    assert_queue_bounded(logger)
    while logger.pending_count > 0:
        await logger.flush_once()
        assert_queue_bounded(logger)
    assert logger.pending_count == 0
    flat = store.flat_rows()
    assert flat[0] == coverage_row(1, "available", "initial")
    # The store flushes batches in queue order; accepted facts are asserted by
    # content because deferred intents may persist out of arrival order.
    assert sorted(
        ((row.observed_at, row.phase) for row in flat if isinstance(row, PhotoperiodObservation)),
    ) == [
        (T0 + timedelta(seconds=number), Phase.MOON if number % 2 else Phase.SUN)
        for number in range(1, 255)
    ]

    # When: the first post-seed offer has space, the rejected future gap is
    # closed atomically and the recovery phase carries the real observation.
    recovery_offset = offset + 43
    logger.enqueue_final_phase(observation(recovery_offset, phase=phase))
    assert_queue_bounded(logger)
    await logger.flush_once()
    assert_queue_bounded(logger)
    flat = store.flat_rows()
    gap_positions = [
        index
        for index, row in enumerate(flat)
        if isinstance(row, PhotoperiodCoverageObservation)
        and row.reason == "recording_gap"
    ]
    assert len(gap_positions) == 1
    gap_index = gap_positions[0]
    assert flat[gap_index : gap_index + 3] == [
        coverage_row(offset, "unavailable", "recording_gap"),
        observation(recovery_offset, phase=Phase.MOON),
        coverage_row(recovery_offset, "available", "recovered"),
    ]
    rejected_times = {
        T0 + timedelta(seconds=number) for number in (offset, offset + 1)
    }
    assert not any(
        isinstance(row, PhotoperiodObservation) and row.observed_at in rejected_times
        for row in flat
    )
    assert logger.flush_health()[0].dropped_rows == 2
    await logger.stop()


@pytest.mark.asyncio
async def test_hanging_append_bounded_shutdown_reports_closure_omission() -> None:
    store = _WaitingStore()
    logger = _start_test_logger(store, shutdown_timeout=0.2)

    await logger.start()
    await await_seeded(logger)
    logger.enqueue_final_phase(observation(0))

    # When: the store hangs past the total shutdown deadline after capacity
    # rejection, the closure may be omitted; pending health stays failed.
    phase = Phase.MOON
    offset = 1
    while logger.pending_count < QUEUE_CAPACITY:
        # Persistence cannot run, so every genuine transition queues until full.
        logger.enqueue_final_phase(observation(offset, phase=phase))
        phase = Phase.SUN if phase == Phase.MOON else Phase.MOON
        offset += 1
    started = time.monotonic()
    await logger.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert store.append_calls == []
    assert logger.flush_health()[0].healthy is False

    # When: the outage heals after shutdown, a manual flush clears the queue,
    # but the omitted stop closure still keeps route-facing health failed.
    store.release.set()
    while logger.pending_count > 0:
        await logger.flush_once()
    assert logger.pending_count == 0
    assert logger.flush_health()[0].healthy is False


class _StubSensorReader:
    def __init__(self) -> None:
        self.failing: set[tuple[str, str]] = set()

    async def read_sensors(
        self, location: str, cluster: str, sensor_mapping: Any
    ) -> dict[str, float | None]:
        if (location, cluster) in self.failing:
            raise RuntimeError(f"sensor unavailable for {location}/{cluster}")
        return {}


class _StubClimateResolver:
    def __init__(self, *, is_sun: bool = True) -> None:
        self.is_sun = is_sun

    async def resolve_period(
        self,
        location: str,
        cluster: str,
        current_time: datetime,
        database: Any,
        *,
        active_profile: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "active_period": {"name": "Day"},
            "current_period_name": "Day",
            "setpoint_data": {},
        }

    def calculate_is_sun(self, current_time: datetime, location: str, cluster: str) -> bool:
        return self.is_sun


class _StubDeviceProcessor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def process_devices(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args[0], args[1]))


class _FakeRuntimeDatabase:
    _automation_redis = None


class _FakeRelayManager:
    def bind_snapshot(self, snapshot: RuntimeDeviceSnapshot) -> object:
        return object()

    async def retry_unresolved(self) -> None:
        return None

    def release_snapshot(self, token: object) -> None:
        return None

    def get_device_state(self, location: str, cluster: str, device_name: str) -> int | None:
        return None

    def get_device_mode(self, location: str, cluster: str, device_name: str) -> str:
        return "auto"


class _FakeScheduler:
    def bind_snapshot(self, snapshot: RuntimeDeviceSnapshot) -> object:
        return object()

    def release_snapshot(self, token: object) -> None:
        return None


class _FakeRuntimeRegistry:
    def __init__(self, snapshot: RuntimeDeviceSnapshot) -> None:
        self.snapshot = snapshot


def _room_snapshot(
    hierarchy: dict[str, dict[str, dict[str, dict[str, Any]]]],
    *,
    active_modes: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> RuntimeDeviceSnapshot:
    return RuntimeDeviceSnapshot.create(
        version=3,
        hierarchy=hierarchy,
        mode_parameters={},
        active_modes=active_modes or {},
        light_intensities={},
        light_programs=[],
    )


TEST_ROOMS = {"Flower Room": {"main": {}}, "Veg Room": {"main": {}}}


def _engine_with(
    sink: PhotoperiodObservationSink,
    snapshot: RuntimeDeviceSnapshot,
    *,
    is_sun: bool = True,
    sensor_reader: _StubSensorReader | None = None,
) -> ControlEngine:
    """Wire the real control-tick method against in-memory boundaries only."""
    engine = object.__new__(ControlEngine)
    engine.photoperiod_observation_sink = sink
    engine.control_tick_observer = None
    engine.relay_manager = _FakeRelayManager()
    engine.database = _FakeRuntimeDatabase()
    engine.config = SimpleNamespace(get_sensor_mapping=lambda: {})
    engine._config_cache = SimpleNamespace(get_sensor_mapping=lambda get: get())
    engine.scheduler = _FakeScheduler()
    engine.runtime_device_registry = _FakeRuntimeRegistry(snapshot)
    engine.sensor_reader = sensor_reader if sensor_reader is not None else _StubSensorReader()
    engine.climate_resolver = _StubClimateResolver(is_sun=is_sun)
    engine.device_processor = _StubDeviceProcessor()
    engine.device_command_service = None
    engine.relay_board_state_manager = None
    engine.alarm_manager = None
    engine._profiling_enabled = False
    engine._ramps_restored = True
    engine._automation_context = {}
    engine._photoperiod_phases = {}
    engine._current_climate_mode = {}
    engine._current_period_name = {}
    engine._moon_authority_forced_moon = set()
    engine._effective_setpoints = {}
    engine._tick_effective_setpoints = {}
    engine._last_light_effective_log = {}
    engine._light_effective_log_interval_sec = 10
    engine._last_light_sun_schedule_gap_error = {}
    engine._pending_db_writes = []
    engine._last_tick_rooms = None
    return engine


async def _start_engine_sink(store: FakeStore) -> PhotoperiodHistoryLogger:
    """Start and seed the real logger so worker flushes participate deterministically."""
    clock = _SignalClock(T0)
    sink = PhotoperiodHistoryLogger(store, now=clock)
    await sink.start()
    await await_seeded(sink)
    return sink


async def await_state_read_attempted(store: FakeStore) -> None:
    """Yield until the worker's first (possibly failing) persisted-state read ran."""
    for _ in range(2_000):
        if store.state_read_calls >= 1:
            return
        await asyncio.sleep(0.002)
    raise AssertionError("photoperiod history worker never attempted the state read")


@pytest.mark.asyncio
async def test_engine_offers_resolved_phase_for_veg_and_flower_rooms() -> None:
    store = FakeStore()
    sink = await _start_engine_sink(store)
    snapshot = _room_snapshot(TEST_ROOMS)
    resolver = _StubClimateResolver(is_sun=True)
    engine = _engine_with(sink, snapshot, is_sun=True)
    engine.climate_resolver = resolver

    # When: two ordinary Veg/Flower ticks resolve the same phase.
    await engine._run_control_loop_with_snapshot(snapshot)
    await sink.flush_once()
    await engine._run_control_loop_with_snapshot(snapshot)
    await sink.flush_once()

    phase_rows = [row for row in store.flat_rows() if isinstance(row, PhotoperiodObservation)]
    assert [(row.location, row.phase) for row in phase_rows] == [
        (FLOWER, Phase.SUN),
        (ROOM, Phase.SUN),
    ]

    # When: one room then the other flips to MOON one tick later.
    resolver.is_sun = False
    await engine._run_control_loop_with_snapshot(snapshot)
    await sink.flush_once()
    await engine._run_control_loop_with_snapshot(snapshot)
    await sink.flush_once()

    phase_rows = [
        row
        for row in store.flat_rows()[4:]
        if isinstance(row, PhotoperiodObservation)
    ]
    assert [(row.location, row.phase) for row in phase_rows] == [
        (FLOWER, Phase.MOON),
        (ROOM, Phase.MOON),
    ]
    coverage_rows = [
        row
        for row in store.flat_rows()
        if isinstance(row, PhotoperiodCoverageObservation)
    ]
    assert [
        (row.location, row.state, row.reason) for row in coverage_rows
    ] == [
        (FLOWER, "available", "initial"),
        (ROOM, "available", "initial"),
    ]
    await sink.stop()


@pytest.mark.asyncio
async def test_engine_sleep_authority_offers_moon_then_resolved_phase_after_exit() -> None:
    store = FakeStore()
    sink = await _start_engine_sink(store)
    room = "Drying Room"
    drying_snapshot = _room_snapshot(
        {room: {"main": {}}},
        active_modes={(room, "main"): {"mode_id": 6, "submode_id": None, "mode_name": "drying"}},
    )
    normal_snapshot = _room_snapshot(
        {room: {"main": {}}},
        active_modes={
            (room, "main"): {
                "mode_id": 5,
                "submode_id": None,
                "mode_name": "standard_veg",
            }
        },
    )
    registry = _FakeRuntimeRegistry(drying_snapshot)
    engine = _engine_with(sink, drying_snapshot, is_sun=True)
    engine.runtime_device_registry = registry

    # When: drying authority forces MOON for two consecutive ticks.
    await engine.run_control_loop()
    await sink.flush_once()
    await engine.run_control_loop()
    await sink.flush_once()

    # Then: one initial MOON phase exists; the second identical offer adds nothing.
    phase_rows = [row for row in store.flat_rows() if isinstance(row, PhotoperiodObservation)]
    assert [(row.location, row.phase) for row in phase_rows] == [(room, Phase.MOON)]

    # When: authority exits and the resolved daylight phase returns.
    registry.snapshot = normal_snapshot
    await engine.run_control_loop()
    await sink.flush_once()
    phase_rows = [row for row in store.flat_rows() if isinstance(row, PhotoperiodObservation)]
    assert [(row.location, row.phase) for row in phase_rows] == [
        (room, Phase.MOON),
        (room, Phase.SUN),
    ]
    await sink.stop()


@pytest.mark.asyncio
async def test_engine_tick_exception_marks_all_snapshot_rooms_unavailable() -> None:
    store = FakeStore()
    sink = await _start_engine_sink(store)
    snapshot = _room_snapshot(TEST_ROOMS)
    sensor_reader = _StubSensorReader()
    engine = _engine_with(sink, snapshot, sensor_reader=sensor_reader)

    # When: the first room's sensor read fails mid-tick.
    sensor_reader.failing = {(FLOWER, "main")}
    with pytest.raises(RuntimeError):
        await engine.run_control_loop()
    await sink.flush_once()

    # Then: both snapshot rooms record the outage before the failure propagates.
    flat = store.flat_rows()
    assert len(flat) == 2
    for row, location in zip(flat, (FLOWER, ROOM), strict=True):
        assert isinstance(row, PhotoperiodCoverageObservation)
        assert row.state == "unavailable"
        assert row.reason == "control_failure"
        assert row.location == location

    # When: the next tick succeeds again, recording resumes per room.
    sensor_reader.failing.clear()
    await engine.run_control_loop()
    await sink.flush_once()

    def row_key(row: PhotoperiodHistoryRow) -> tuple[str, str]:
        if isinstance(row, PhotoperiodObservation):
            return (row.location, row.phase.value)
        return (row.location, f"{row.state}:{row.reason}")

    assert [row_key(row) for row in store.flat_rows()[2:]] == [
        (FLOWER, "available:started"),
        (FLOWER, "SUN"),
        (ROOM, "available:started"),
        (ROOM, "SUN"),
    ]
    await sink.stop()


@pytest.mark.asyncio
async def test_engine_removal_marks_missing_room_unavailable() -> None:
    store = FakeStore()
    sink = await _start_engine_sink(store)
    snapshot = _room_snapshot(TEST_ROOMS)
    engine = _engine_with(sink, snapshot, is_sun=True)

    await engine.run_control_loop()
    await sink.flush_once()
    assert len(store.flat_rows()) == 4

    # When: the second room disappears from a later successful snapshot.
    reduced = _room_snapshot({FLOWER: {"main": {}}})
    engine.runtime_device_registry = _FakeRuntimeRegistry(reduced)
    await engine.run_control_loop()
    await sink.flush_once()

    closed = [
        row
        for row in store.flat_rows()[4:]
        if isinstance(row, PhotoperiodCoverageObservation)
    ]
    assert [(row.location, row.reason) for row in closed] == [(ROOM, "control_failure")]
    await sink.stop()


@pytest.mark.asyncio
async def test_engine_empty_hierarchy_writes_nothing() -> None:
    store = FakeStore()
    sink = await _start_engine_sink(store)
    snapshot = _room_snapshot({})
    engine = _engine_with(sink, snapshot, is_sun=True)

    await engine.run_control_loop()
    await sink.flush_once()
    await engine.run_control_loop()
    await sink.flush_once()

    assert store.flat_rows() == []
    await sink.stop()


@pytest.mark.asyncio
async def test_engine_tick_completes_while_state_read_fails_and_nothing_fabricates() -> None:
    store = FakeStore(fail_state_read=True)
    sink = _start_test_logger(store, shutdown_timeout=0.1)
    await sink.start()
    await await_state_read_attempted(store)
    snapshot = _room_snapshot(TEST_ROOMS)
    engine = _engine_with(sink, snapshot, is_sun=True)

    # When: hardware startup is unaffected by the failed persisted-state read.
    await engine.run_control_loop()

    # Then: control delivered decisions to every room and fabricated no coverage.
    assert [(call[0], call[1]) for call in engine.device_processor.calls] == [
        (FLOWER, "main"),
        (ROOM, "main"),
    ]
    assert store.append_calls == []
    assert store.state_read_calls >= 1
    await sink.stop()


class LifecycleComponent:
    """Existing container-shutdown stand-in recording ordered lifecycle calls."""

    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def stop(self) -> None:
        self.calls.append(f"{self.name}-stop")

    async def close(self) -> None:
        self.calls.append(f"{self.name}-close")

    async def drain(self) -> None:
        self.calls.append(f"{self.name}-drain")


@pytest.mark.asyncio
async def test_lifecycle_flush_stops_logger_before_database_close() -> None:
    # Given: the shutdown dependencies record their lifecycle calls.
    from app.container import ServiceContainer

    calls: list[str] = []
    container = object.__new__(ServiceContainer)
    container.__dict__["background_tasks"] = LifecycleComponent("control", calls)
    container.__dict__["photoperiod_history_logger"] = LifecycleComponent("logger", calls)
    container.__dict__["monitoring_publication_workers"] = LifecycleComponent("publication", calls)
    container.__dict__["operational_event_dispatcher"] = LifecycleComponent("events", calls)
    container.__dict__["database"] = LifecycleComponent("database", calls)
    container.__dict__["_operational_event_redis"] = None
    container.__dict__["_operational_event_pool"] = None
    container.__dict__["relay_observation_recorder"] = None
    container.mcp23017 = None
    container.dfr0971_manager = None
    container._initialized = True

    # When: the container shuts down without starting a production service.
    await container.shutdown()

    # Then: the history logger still stops before the database closes.
    assert calls == [
        "control-stop",
        "logger-stop",
        "publication-stop",
        "events-stop",
        "events-drain",
        "database-close",
    ]
