from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.relay_observation_recorder import RelayObservation, RelayObservationRecorder
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot


BASE = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class FakeStore:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.attempts = 0
        self.rows: list[tuple[RelayObservation, ...]] = []
        self.exhausted = asyncio.Event()
        self.started = asyncio.Event()

    async def append(self, rows: tuple[RelayObservation, ...]) -> None:
        self.started.set()
        self.attempts += 1
        if self.failures:
            self.failures -= 1
            if self.failures == 0:
                self.exhausted.set()
            raise OSError("disposable fake database outage")
        self.rows.append(rows)


class BlockingStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def append(self, rows: tuple[RelayObservation, ...]) -> None:
        self.started.set()
        await self.release.wait()
        self.rows.append(rows)


class FakeRegistry:
    def __init__(self, snapshot: RuntimeDeviceSnapshot) -> None:
        self.current = snapshot
        self.consumer: Callable[[RuntimeDeviceSnapshot], None] | None = None

    def subscribe(self, consumer: Callable[[RuntimeDeviceSnapshot], None]) -> None:
        self.consumer = consumer
        consumer(self.current)

    def publish(self, snapshot: RuntimeDeviceSnapshot) -> None:
        self.current = snapshot
        assert self.consumer is not None
        self.consumer(snapshot)


def make_snapshot(
    version: int,
    assignments: dict[int, tuple[int, str, str, str, str]] | None = None,
) -> RuntimeDeviceSnapshot:
    hierarchy: dict[str, dict[str, dict[str, dict[str, object]]]] = {}
    for channel, (device_id, location, cluster, name, device_type) in (
        assignments or {}
    ).items():
        hierarchy.setdefault(location, {}).setdefault(cluster, {})[name] = {
            "device_id": device_id,
            "device_type": device_type,
            "channel": channel,
        }
    return RuntimeDeviceSnapshot.create(
        version=version,
        hierarchy=hierarchy,
        mode_parameters={},
        active_modes={},
        light_intensities={},
        light_programs=[],
    )


def collected(store: FakeStore) -> list[RelayObservation]:
    return [row for batch in store.rows for row in batch]


def heartbeat_rows(rows: list[RelayObservation]) -> list[RelayObservation]:
    return [row for row in rows if row.reason == "heartbeat"]


@pytest.mark.asyncio
async def test_initial_off_and_rapid_off_on_off_samples_preserve_every_transition() -> None:
    store = FakeStore()
    recorder = RelayObservationRecorder(store)
    registry = FakeRegistry(
        make_snapshot(3, {0: (17, "flower", "main", "heater", "heating")})
    )
    registry.subscribe(recorder.on_registry_snapshot)
    await recorder.start()

    off = (False,) * 16
    on = (True,) + (False,) * 15
    recorder.observe_sample(off, BASE)
    recorder.observe_sample(on, BASE + timedelta(seconds=1))
    recorder.observe_sample(off, BASE + timedelta(seconds=2))
    await recorder.stop()

    rows = collected(store)
    initial = [row for row in rows if row.reason == "initial"]
    transitions = [row for row in rows if row.reason == "state_changed"]
    assert len(initial) == 16
    assert all(row.observed_state is False for row in initial)
    assert initial[0].device_id == 17
    assert initial[0].device_type == "heating"
    assert initial[0].registry_version == 3
    assert [(row.observed_at, row.observed_state) for row in transitions] == [
        (BASE + timedelta(seconds=1), True),
        (BASE + timedelta(seconds=2), False),
    ]
    assert [(row.observed_at, row.channel, row.observed_state) for row in heartbeat_rows(rows)] == [
        (BASE, None, None)
    ]


@pytest.mark.asyncio
async def test_stale_is_emitted_once_and_recovery_starts_a_fresh_known_baseline() -> None:
    store = FakeStore()
    recorder = RelayObservationRecorder(store)
    FakeRegistry(make_snapshot(1)).subscribe(recorder.on_registry_snapshot)
    await recorder.start()

    on = (True,) * 16
    recorder.observe_sample(on, BASE)
    recorder.observe_sample(None, BASE + timedelta(seconds=2))
    recorder.observe_sample(None, BASE + timedelta(seconds=3))
    recorder.observe_sample(on, BASE + timedelta(seconds=5))
    await recorder.stop()

    rows = collected(store)
    stale = [row for row in rows if row.reason == "stale"]
    recovered = [row for row in rows if row.reason == "recovered"]
    assert len(stale) == 16
    assert {row.observed_at for row in stale} == {BASE + timedelta(seconds=2)}
    assert all(
        row.observed_state is None
        and row.device_id is None
        and row.device_name is None
        and row.device_type is None
        and row.location is None
        and row.cluster is None
        for row in stale
    )
    assert len(recovered) == 16
    assert all(row.observed_state is True for row in recovered)
    assert {row.observed_at for row in recovered} == {BASE + timedelta(seconds=5)}


@pytest.mark.asyncio
async def test_board_manager_forwards_failed_sample_stale_boundary_to_recorder() -> None:
    store = FakeStore()
    recorder = RelayObservationRecorder(store)
    FakeRegistry(make_snapshot(1)).subscribe(recorder.on_registry_snapshot)
    await recorder.start()

    sampler = FakeSampler()
    sampler.samples = [None, None, (True,) * 16]
    sample_times = iter((BASE + timedelta(seconds=2), BASE + timedelta(seconds=5)))
    board = RelayBoardStateManager(
        sampler,
        now=lambda: next(sample_times),
        observation_callback=recorder.observe_sample,
    )
    assert await board.sample() is False
    assert await board.sample() is False
    assert await board.sample() is True
    await recorder.stop()

    rows = collected(store)
    stale = [row for row in rows if row.reason == "stale"]
    recovered = [row for row in rows if row.reason == "recovered"]
    assert len(stale) == 16
    assert {row.observed_at for row in stale} == {BASE + timedelta(seconds=2)}
    assert len(recovered) == 16
    assert {row.observed_at for row in recovered} == {BASE + timedelta(seconds=5)}


@pytest.mark.asyncio
async def test_initial_persists_light_identity_and_unassigned_channel_facts() -> None:
    store = FakeStore()
    recorder = RelayObservationRecorder(store)
    snapshot = make_snapshot(
        4,
        {
            1: (30, "flower", "main", "grow-light", "light"),
            7: (31, "flower", "main", "heater", "heating"),
        },
    )
    FakeRegistry(snapshot).subscribe(recorder.on_registry_snapshot)
    await recorder.start()
    recorder.observe_sample((False,) * 16, BASE)
    await recorder.stop()

    initial = [row for row in collected(store) if row.reason == "initial"]
    assert len(initial) == 16
    assert (initial[1].device_id, initial[1].device_type) == (30, "light")
    assert (initial[7].device_id, initial[7].device_type) == (31, "heating")
    assert initial[0].device_id is None
    assert initial[0].device_name is None
    assert initial[0].location is None



@pytest.mark.asyncio
async def test_owner_change_while_on_uses_subscribed_snapshot_and_new_event_identity() -> None:
    store = FakeStore()
    recorder = RelayObservationRecorder(store, now=lambda: BASE + timedelta(seconds=4))
    registry = FakeRegistry(
        make_snapshot(10, {2: (20, "flower", "main", "heater-a", "heating")})
    )
    registry.subscribe(recorder.on_registry_snapshot)
    await recorder.start()

    on = (False, False, True) + (False,) * 13
    recorder.observe_sample(on, BASE)
    registry.publish(
        make_snapshot(11, {2: (21, "flower", "main", "heater-b", "heating")})
    )
    await recorder.stop()

    rows = collected(store)
    changed_owner = [row for row in rows if row.reason == "assignment_changed"]
    assert len(changed_owner) == 1
    assert changed_owner[0].channel == 2
    assert changed_owner[0].observed_state is True
    assert changed_owner[0].device_id == 21
    assert changed_owner[0].device_name == "heater-b"
    assert changed_owner[0].registry_version == 11
    assert not any(
        row.reason == "assignment_changed" and row.device_id == 20 for row in rows
    )


@pytest.mark.asyncio
async def test_assignment_change_while_stale_records_unknown_state_for_new_owner() -> None:
    store = FakeStore()
    recorder = RelayObservationRecorder(store, now=lambda: BASE + timedelta(seconds=3))
    registry = FakeRegistry(
        make_snapshot(1, {5: (40, "flower", "main", "heater-a", "heating")})
    )
    registry.subscribe(recorder.on_registry_snapshot)
    await recorder.start()

    recorder.observe_sample((False,) * 5 + (True,) + (False,) * 10, BASE)
    recorder.observe_sample(None, BASE + timedelta(seconds=1))
    registry.publish(
        make_snapshot(2, {5: (41, "flower", "main", "heater-b", "heating")})
    )
    await recorder.stop()

    assignment = [
        row for row in collected(store) if row.reason == "assignment_changed"
    ]
    assert len(assignment) == 1
    assert assignment[0].device_id == 41
    assert assignment[0].observed_state is None
    assert assignment[0].registry_version == 2


@pytest.mark.asyncio
async def test_heartbeat_cadence_confirms_successful_sampling_after_an_outage() -> None:
    store = FakeStore()
    recorder = RelayObservationRecorder(store)
    await recorder.start()

    state = (False,) * 16
    recorder.observe_sample(state, BASE)
    recorder.observe_sample(state, BASE + timedelta(seconds=29))
    recorder.observe_sample(state, BASE + timedelta(seconds=30))
    recorder.observe_sample(None, BASE + timedelta(seconds=31))
    recorder.observe_sample(None, BASE + timedelta(seconds=91))
    recorder.observe_sample(state, BASE + timedelta(seconds=92))
    await recorder.stop()

    rows = collected(store)
    assert [row.observed_at for row in heartbeat_rows(rows)] == [
        BASE,
        BASE + timedelta(seconds=30),
        BASE + timedelta(seconds=92),
    ]
    assert recorder.last_persisted_heartbeat_at == BASE + timedelta(seconds=92)


@pytest.mark.asyncio
async def test_unchanged_samples_do_not_duplicate_channel_state_facts() -> None:
    store = FakeStore()
    recorder = RelayObservationRecorder(store)
    await recorder.start()

    state = (False,) * 16
    recorder.observe_sample(state, BASE)
    recorder.observe_sample(state, BASE + timedelta(seconds=10))
    await recorder.stop()

    rows = collected(store)
    assert len([row for row in rows if row.reason == "initial"]) == 16
    assert not [row for row in rows if row.reason == "state_changed"]
    assert len(heartbeat_rows(rows)) == 1


@pytest.mark.asyncio
async def test_queue_overflow_requires_gap_markers_before_fresh_baselines() -> None:
    store = FakeStore()
    recorder = RelayObservationRecorder(store, queue_capacity=33)

    recorder.observe_sample((False,) * 16, BASE)
    recorder.observe_sample((True,) + (False,) * 15, BASE + timedelta(seconds=1))
    recorder.observe_sample((True,) * 16, BASE + timedelta(seconds=2))
    recorder.observe_sample((False,) * 16, BASE + timedelta(seconds=3))
    assert recorder.coverage_incomplete is True

    await recorder.start()
    await recorder._queue.join()
    recorder.observe_sample((True,) * 16, BASE + timedelta(seconds=4))
    await recorder.stop()

    rows = collected(store)
    gap_indexes = [index for index, row in enumerate(rows) if row.reason == "recording_gap"]
    recovered_indexes = [index for index, row in enumerate(rows) if row.reason == "recovered"]
    assert len(gap_indexes) == 16
    assert len(recovered_indexes) == 16
    assert max(gap_indexes) < min(recovered_indexes)
    assert {rows[index].channel for index in gap_indexes} == set(range(16))
    assert all(
        rows[index].observed_state is None
        and rows[index].device_id is None
        and rows[index].location is None
        and rows[index].cluster is None
        for index in gap_indexes
    )
    assert recorder.coverage_incomplete is False


@pytest.mark.asyncio
async def test_database_failure_discards_uncertain_rows_then_recovers_with_gap_and_baseline() -> None:
    store = FakeStore(failures=3)
    recorder = RelayObservationRecorder(store)
    await recorder.start()
    recorder.observe_sample((False,) * 16, BASE)

    await store.exhausted.wait()
    for _ in range(100):
        if recorder.coverage_incomplete:
            break
        await asyncio.sleep(0)
    assert recorder.coverage_incomplete is True

    recorder.observe_sample((True,) + (False,) * 15, BASE + timedelta(seconds=1))
    await recorder._queue.join()
    await recorder.stop()

    rows = collected(store)
    assert [row.reason for row in rows[:16]] == ["recording_gap"] * 16
    assert [row.reason for row in rows[16:32]] == ["recovered"] * 16
    assert all(row.observed_state is None for row in rows[:16])
    assert rows[16].observed_state is True
    assert rows[16].observed_at == BASE + timedelta(seconds=1)
    assert rows[0].observed_at == BASE
    assert rows[-1].reason == "heartbeat"
    assert store.attempts >= 4


@pytest.mark.asyncio
async def test_new_process_session_starts_a_new_initial_baseline_without_backfill() -> None:
    first_store = FakeStore()
    first = RelayObservationRecorder(first_store, session_id=UUID(int=1))
    await first.start()
    first.observe_sample((True,) * 16, BASE)
    await first.stop()

    second_store = FakeStore()
    second = RelayObservationRecorder(second_store, session_id=UUID(int=2))
    await second.start()
    second.observe_sample((True,) * 16, BASE + timedelta(days=1))
    await second.stop()

    first_rows = collected(first_store)
    second_rows = collected(second_store)
    assert {row.session_id for row in first_rows} == {UUID(int=1)}
    assert {row.session_id for row in second_rows} == {UUID(int=2)}
    assert len([row for row in second_rows if row.reason == "initial"]) == 16
    assert {row.observed_at for row in second_rows} == {BASE + timedelta(days=1)}


class FakeSampler:
    def __init__(self) -> None:
        self.samples = [(False,) * 16, (True,) + (False,) * 15]
        self.write_calls = 0

    def sample_all_channels(self) -> tuple[bool, ...] | None:
        return self.samples.pop(0)


@pytest.mark.asyncio
async def test_sampler_callback_never_waits_for_database_or_issues_a_relay_command() -> None:
    store = BlockingStore()
    recorder = RelayObservationRecorder(store)
    await recorder.start()
    times = iter((BASE, BASE + timedelta(seconds=1)))
    sampler = FakeSampler()
    board = RelayBoardStateManager(
        sampler,
        now=lambda: next(times),
        observation_callback=recorder.observe_sample,
    )

    assert await asyncio.wait_for(board.sample(), timeout=1) is True
    await asyncio.wait_for(store.started.wait(), timeout=1)
    assert await asyncio.wait_for(board.sample(), timeout=1) is True
    assert recorder.pending_count > 0
    assert sampler.write_calls == 0

    store.release.set()
    await recorder.stop()
    rows = collected(store)
    assert [row.observed_state for row in rows if row.reason == "state_changed"] == [True]

@pytest.mark.asyncio
async def test_raised_sampler_failure_marks_stale_with_event_time_identity() -> None:
    class RaisingSampler:
        def sample_all_channels(self) -> tuple[bool, ...] | None:
            raise OSError("disposable sampler failure")

    store = FakeStore()
    recorder = RelayObservationRecorder(store)
    FakeRegistry(
        make_snapshot(5, {0: (17, "flower", "main", "heater", "heating")})
    ).subscribe(recorder.on_registry_snapshot)
    await recorder.start()
    board = RelayBoardStateManager(
        RaisingSampler(),
        now=lambda: BASE,
        observation_callback=recorder.observe_sample,
    )

    with pytest.raises(OSError, match="disposable sampler failure"):
        await board.sample()
    await recorder.stop()

    stale = [row for row in collected(store) if row.reason == "stale"]
    assert board.get_freshness().status == "STALE"
    assert len(stale) == 16
    assert all(row.observed_at == BASE and row.observed_state is None for row in stale)
    assert (
        stale[0].device_id,
        stale[0].device_name,
        stale[0].device_type,
        stale[0].location,
        stale[0].cluster,
        stale[0].registry_version,
    ) == (17, "heater", "heating", "flower", "main", 5)
