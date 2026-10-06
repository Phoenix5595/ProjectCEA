from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import anyio
import pytest

from app.control.control_engine import ControlEngine
from app.control.current_publication_observer import build_current_snapshot
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.control.setpoint_manager import RampState
from app.monitoring_publication.current import CurrentPublicationPublisher
from shared.monitoring_contracts import CurrentSnapshot, PhotoperiodPhase

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


@dataclass
class RecordingObserver:
    events: list[str]
    snapshots: list[CurrentSnapshot] = field(default_factory=list)

    def offer(self, snapshot: CurrentSnapshot) -> None:
        self.events.append("observer")
        self.snapshots.append(snapshot)


class RaisingObserver:
    def offer(self, snapshot: CurrentSnapshot) -> None:
        del snapshot
        raise RuntimeError("observer failure")


class BlockingObserver:
    async def offer(self, snapshot: CurrentSnapshot) -> None:
        del snapshot
        await anyio.sleep_forever()


@dataclass
class RecordingWriter:
    snapshots: list[CurrentSnapshot] = field(default_factory=list)

    def write_current(self, location: str, snapshot: CurrentSnapshot) -> bool:
        del location
        self.snapshots.append(snapshot)
        return True


class FakeConfigCache:
    def get_sensor_mapping(self, loader: object) -> Mapping[str, str]:
        del loader
        return {}


class FakeConfig:
    def get_sensor_mapping(self) -> Mapping[str, str]:
        return {}


class FakeSensorReader:
    def __init__(self) -> None:
        self.on_read: Callable[[], Awaitable[None]] | None = None
        self.fail = False

    async def read_sensors(
        self, location: str, cluster: str, sensor_mapping: Mapping[str, str]
    ) -> Mapping[str, float]:
        del location, cluster, sensor_mapping
        if self.fail:
            raise RuntimeError("sensor read failed")
        if self.on_read is not None:
            await self.on_read()
        return {}


class FakeClimateResolver:
    def __init__(self) -> None:
        self.active_profiles: list[dict[str, object] | None] = []
        self.has_period = True

    async def resolve_period(
        self,
        location: str,
        cluster: str,
        current_time: object,
        database: object,
        *,
        active_profile: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        del location, cluster, current_time, database
        self.active_profiles.append(None if active_profile is None else dict(active_profile))
        if not self.has_period:
            return {
                "active_period": False,
                "current_period_name": None,
                "setpoint_data": None,
                "time_str": "12:00",
            }
        return {
            "active_period": True,
            "current_period_name": "day",
            "setpoint_data": {"target": 1.0},
            "time_str": "12:00",
        }

    def calculate_is_sun(self, current_time: object, location: str, cluster: str) -> bool:
        del current_time, location, cluster
        return True


class FakeSetpointCalculator:
    async def calculate_setpoints(self, *args: object) -> dict[str, float]:
        setpoint_data = args[5]
        assert isinstance(setpoint_data, Mapping)
        return {
            "effective_heating_setpoint": float(
                setpoint_data.get("tick_test_effective", 24.0)
            )
        }

    def add_current_vpd(
        self,
        effective_data: dict[str, float],
        location: str,
        cluster: str,
        sensor_values: Mapping[str, float],
        sensor_mapping: Mapping[str, str],
    ) -> dict[str, float]:
        del location, cluster, sensor_values, sensor_mapping
        return effective_data


@dataclass
class FakeDeviceProcessor:
    calls: list[tuple[object, ...]] = field(default_factory=list)

    async def process_devices(self, *args: object, **kwargs: object) -> None:
        self.calls.append((*args, kwargs))


class FakeRelayManager:
    def get_all_states(self) -> dict[tuple[str, str, str], int]:
        return {("Veg Room", "Main", "Heater"): 1}


def _snapshot(
    *,
    version: int = 7,
    active_profile: Mapping[str, object] | None = None,
) -> RuntimeDeviceSnapshot:
    return RuntimeDeviceSnapshot.create(
        version=version,
        hierarchy={"Veg Room": {"Main": {"Heater": {}}}},
        mode_parameters={},
        active_modes=(
            {} if active_profile is None else {("Veg Room", "Main"): dict(active_profile)}
        ),
        light_intensities={},
        light_programs=[],
    )


def _engine(
    observer: object | None, events: list[str]
) -> tuple[ControlEngine, FakeDeviceProcessor]:
    engine = ControlEngine.__new__(ControlEngine)
    processor = FakeDeviceProcessor()
    engine._ramps_restored = True
    engine._profiling_enabled = False
    engine._config_cache = FakeConfigCache()
    engine.config = FakeConfig()
    engine.sensor_reader = FakeSensorReader()
    engine.climate_resolver = FakeClimateResolver()
    engine.setpoint_calculator = FakeSetpointCalculator()
    engine.setpoint_manager = SimpleNamespace(ramp_manager=SimpleNamespace(active_ramps={}))
    engine.database = object()
    engine.photoperiod_observation_sink = None
    engine.scheduler = object()
    engine.device_processor = processor
    engine.relay_manager = FakeRelayManager()
    engine._current_period_name = {}
    engine._current_climate_mode = {}
    engine._moon_authority_forced_moon = set()
    engine._effective_setpoints = {}
    engine._tick_effective_setpoints = {}
    engine._automation_context = {("Veg Room", "Main", "Heater"): {"pid_output": 0.25}}
    engine._photoperiod_phases = {}
    engine._pending_db_writes = []
    engine._last_light_effective_log = {}
    engine._light_effective_log_interval_sec = 10
    engine._last_light_sun_schedule_gap_error = {}
    engine.control_tick_observer = observer
    engine._current_observer_failures = 0

    async def log_effective_setpoints(*args: object) -> None:
        del args

    async def log_automation_state(snapshot: RuntimeDeviceSnapshot) -> None:
        del snapshot
        events.append("automation-state")

    async def no_op() -> None:
        return None

    engine._log_effective_setpoints = log_effective_setpoints
    engine._log_automation_state = log_automation_state
    engine._expire_manual_overrides = no_op
    engine._expire_raw_channel_overrides = no_op
    return engine, processor


def _run_tick(
    engine: ControlEngine, snapshot: RuntimeDeviceSnapshot | None = None
) -> None:
    anyio.run(engine._run_control_loop_with_snapshot, snapshot or _snapshot())


def test_control_tick_offers_one_complete_snapshot_after_automation_state_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a successful one-room control tick and a synchronous recording observer.
    events: list[str] = []
    observer = RecordingObserver(events)
    engine, processor = _engine(observer, events)

    async def log_light(**kwargs: object) -> None:
        del kwargs

    monkeypatch.setattr(
        "app.control.control_engine.log_light_effective_intensities_for_cluster", log_light
    )

    # When: the tick completes all existing processing and automation-state logging.
    _run_tick(engine)

    # Then: one valid immutable current snapshot is offered after unchanged device processing.
    assert len(processor.calls) == 1
    assert events == ["automation-state", "observer"]
    assert observer.snapshots[0].version.config_version == 7
    assert observer.snapshots[0].observed_at.tzinfo == UTC
    assert observer.snapshots[0].valid_until > observer.snapshots[0].observed_at
    assert observer.snapshots[0].photoperiod is not None
    assert {point.series_id.value for point in observer.snapshots[0].series} == {
        "veg_room.main.setpoint.effective_heating_setpoint",
        "veg_room.main.device.heater.automation.pid_output",
        "veg_room.main.device.heater.relay_state",
    }


def test_control_tick_without_observer_preserves_processing_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: baseline and absent-observer control ticks with identical in-memory state.
    events: list[str] = []
    engine, processor = _engine(None, events)

    async def log_light(**kwargs: object) -> None:
        del kwargs

    monkeypatch.setattr(
        "app.control.control_engine.log_light_effective_intensities_for_cluster", log_light
    )

    # When: the tick succeeds without an observer.
    _run_tick(engine)

    # Then: existing processing and automation logging complete with no publication mutation.
    assert len(processor.calls) == 1
    assert events == ["automation-state"]
    assert engine._current_observer_failures == 0


def test_control_tick_contains_raising_or_async_observers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: observers that raise synchronously or return a never-completing awaitable.
    async def log_light(**kwargs: object) -> None:
        del kwargs

    for observer in (RaisingObserver(), BlockingObserver()):
        events: list[str] = []
        engine, processor = _engine(observer, events)
        monkeypatch.setattr(
            "app.control.control_engine.log_light_effective_intensities_for_cluster", log_light
        )

        # When: a successful tick reaches the observer boundary.
        _run_tick(engine)

        # Then: neither failure mode delays or changes completed control work.
        assert len(processor.calls) == 1
        assert events == ["automation-state"]
        assert engine._current_observer_failures == 1


def test_current_publisher_offer_replaces_memory_without_writer_io() -> None:
    # Given: the intended real observer and two snapshots from successful control ticks.
    writer = RecordingWriter()
    publisher = CurrentPublicationPublisher("Veg Room", writer)
    first_engine, _ = _engine(None, [])
    latest_engine, _ = _engine(None, [])
    first_engine._tick_effective_setpoints[("Veg Room", "Main")] = {
        "effective_heating_setpoint": 24.0
    }
    latest_engine._tick_effective_setpoints[("Veg Room", "Main")] = {
        "effective_heating_setpoint": 24.0
    }
    first_engine._photoperiod_phases[("Veg Room", "Main")] = PhotoperiodPhase.SUN
    latest_engine._photoperiod_phases[("Veg Room", "Main")] = PhotoperiodPhase.SUN

    # When: current facts are offered before the independent publisher flushes.
    first = first_engine._build_current_snapshot(_snapshot(), datetime.now(UTC))
    latest = latest_engine._build_current_snapshot(_snapshot(), datetime.now(UTC))
    assert first is not None
    assert latest is not None
    publisher.offer(first)
    publisher.offer(latest)

    # Then: latest memory replaces older facts and the control thread never invokes the writer.
    assert writer.snapshots == []
    assert publisher.health.pending is True
    assert publisher.health.replaced_snapshots == 1


def test_current_snapshot_normalizes_room_and_metric_segments_without_fake_names() -> None:
    # Given: noncanonical room and metric identifiers, including an empty metric.
    room = ("7 Flower/Room!!", "02 -- Main")
    current = build_current_snapshot(
        effective_setpoints={
            room: {
                "Effective---Heating": 22.0,
                "nominal_heating_setpoint": 24.0,
                "ramp_progress_heating": 0.5,
                "!!!": 99.0,
            }
        },
        automation_context={},
        relay_states={},
        photoperiod_phases={room: PhotoperiodPhase.SUN},
        runtime_snapshot_version=7,
        observed_at=NOW,
        active_profiles={room: {"mode_id": 11, "submode_id": None}},
        ramp_remaining_seconds={room: {}},
    )

    # Then: both room segments and metric names use the shared canonical form.
    assert current is not None
    series = {point.series_id.value: point.value for point in current.series}
    assert series == {
        "v_7_flower_room.v_02_main.setpoint.effective_heating": 22.0,
        "v_7_flower_room.v_02_main.setpoint.nominal_heating_setpoint": 24.0,
        "v_7_flower_room.v_02_main.setpoint.ramp_progress_heating": 0.5,
        "v_7_flower_room.v_02_main.setpoint.profile_mode_id": 11.0,
    }
    assert all("None" not in series_id for series_id in series)


def test_all_null_profile_publishes_identity_only_after_a_fresh_phase() -> None:
    # Given: a completed tick with a known base profile and no climate targets.
    room = ("Veg Room", "main")
    current = build_current_snapshot(
        effective_setpoints={},
        automation_context={},
        relay_states={},
        photoperiod_phases={room: PhotoperiodPhase.MOON},
        runtime_snapshot_version=7,
        observed_at=NOW,
        active_profiles={room: {"mode_id": 4, "submode_id": None}},
        ramp_remaining_seconds={},
    )

    # Then: the active identity and phase are factual without synthesized numeric setpoints.
    assert current is not None
    assert [point.series_id.value for point in current.series] == [
        "veg_room.main.setpoint.profile_mode_id"
    ]
    assert current.photoperiod is not None
    assert current.photoperiod.phase is PhotoperiodPhase.MOON


def test_unknown_or_invalid_profile_without_numeric_facts_produces_no_snapshot() -> None:
    # Given: no numeric tick facts and either no active identity or an invalid one.
    room = ("Veg Room", "main")
    common = {
        "effective_setpoints": {},
        "automation_context": {},
        "relay_states": {},
        "photoperiod_phases": {room: PhotoperiodPhase.SUN},
        "runtime_snapshot_version": 7,
        "observed_at": NOW,
        "ramp_remaining_seconds": {},
    }

    # Then: absence and invalid IDs never become a synthetic profile/base identity.
    assert build_current_snapshot(active_profiles={}, **common) is None
    invalid = build_current_snapshot(
        active_profiles={room: {"mode_id": 0, "submode_id": None}}, **common
    )
    assert invalid is None
    invalid_submode = build_current_snapshot(
        active_profiles={room: {"mode_id": 4, "submode_id": "unknown"}}, **common
    )
    assert invalid_submode is None

    ramp_only_inputs = common | {"ramp_remaining_seconds": {room: {"heating": 15.0}}}
    assert build_current_snapshot(active_profiles={}, **ramp_only_inputs) is None


def test_control_tick_keeps_captured_profile_when_registry_changes_during_sensor_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the registry changes while a sensor read awaits, between old and new ticks.
    events: list[str] = []
    observer = RecordingObserver(events)
    engine, _ = _engine(observer, events)

    class ModeAwareResolver(FakeClimateResolver):
        async def resolve_period(
            self,
            location: str,
            cluster: str,
            current_time: object,
            database: object,
            *,
            active_profile: Mapping[str, object] | None = None,
        ) -> dict[str, object]:
            result = await super().resolve_period(
                location,
                cluster,
                current_time,
                database,
                active_profile=active_profile,
            )
            assert active_profile is not None
            result["setpoint_data"] = {
                "tick_test_effective": float(active_profile["mode_id"])
            }
            return result

    resolver = ModeAwareResolver()
    engine.climate_resolver = resolver
    drying = _snapshot(
        version=11,
        active_profile={"mode_id": 2, "submode_id": None, "mode_name": "Drying"},
    )
    flower = _snapshot(
        version=12,
        active_profile={"mode_id": 3, "submode_id": 8, "mode_name": "Flower"},
    )
    registry = SimpleNamespace(snapshot=drying)
    engine.runtime_device_registry = registry

    async def replace_snapshot_during_read() -> None:
        await anyio.sleep(0)
        registry.snapshot = flower

    engine.sensor_reader.on_read = replace_snapshot_during_read

    async def log_light(**kwargs: object) -> None:
        del kwargs

    monkeypatch.setattr(
        "app.control.control_engine.log_light_effective_intensities_for_cluster", log_light
    )

    # When: the first tick completes on its entry snapshot, then a second tick starts fresh.
    _run_tick(engine, drying)
    engine.sensor_reader.on_read = None
    _run_tick(engine, flower)

    # Then: climate, identity and moon metadata stay paired with each captured snapshot.
    assert [profile["mode_id"] for profile in resolver.active_profiles if profile] == [2, 3]
    assert registry.snapshot is flower
    first, second = observer.snapshots
    first_points = {point.series_id.value: point.value for point in first.series}
    second_points = {point.series_id.value: point.value for point in second.series}
    assert first_points["veg_room.main.setpoint.profile_mode_id"] == 2.0
    assert "veg_room.main.setpoint.profile_submode_id" not in first_points
    assert first_points["veg_room.main.setpoint.effective_heating_setpoint"] == 2.0
    assert first.photoperiod is not None
    assert first.photoperiod.phase is PhotoperiodPhase.MOON
    assert second_points["veg_room.main.setpoint.profile_mode_id"] == 3.0
    assert second_points["veg_room.main.setpoint.profile_submode_id"] == 8.0
    assert second_points["veg_room.main.setpoint.effective_heating_setpoint"] == 3.0
    assert second.photoperiod is not None
    assert second.photoperiod.phase is PhotoperiodPhase.SUN


def test_missing_period_does_not_republish_previous_tick_setpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a first completed profile tick followed by a tick with no climate period.
    events: list[str] = []
    observer = RecordingObserver(events)
    engine, _ = _engine(observer, events)
    snapshot = _snapshot(active_profile={"mode_id": 4, "submode_id": None, "mode_name": "Veg"})

    async def log_light(**kwargs: object) -> None:
        del kwargs

    monkeypatch.setattr(
        "app.control.control_engine.log_light_effective_intensities_for_cluster", log_light
    )
    _run_tick(engine, snapshot)
    persisted_effective = engine._effective_setpoints[("Veg Room", "Main")]
    engine.climate_resolver.has_period = False
    _run_tick(engine, snapshot)

    # Then: persistent consumers retain their cache but the latest publication has no old target.
    assert engine._effective_setpoints[("Veg Room", "Main")] is persisted_effective
    latest_ids = {point.series_id.value for point in observer.snapshots[-1].series}
    assert "veg_room.main.setpoint.profile_mode_id" in latest_ids
    assert "veg_room.main.setpoint.effective_heating_setpoint" not in latest_ids
    assert engine._tick_effective_setpoints == {}


def test_failed_tick_clears_actual_facts_and_phase_before_next_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one successful tick has cached values before the next sensor read fails.
    events: list[str] = []
    observer = RecordingObserver(events)
    engine, _ = _engine(observer, events)
    snapshot = _snapshot()

    async def log_light(**kwargs: object) -> None:
        del kwargs

    monkeypatch.setattr(
        "app.control.control_engine.log_light_effective_intensities_for_cluster", log_light
    )
    _run_tick(engine, snapshot)
    previous_effective = engine._effective_setpoints[("Veg Room", "Main")]
    engine.sensor_reader.fail = True

    # When: the next tick fails before producing current values or a phase.
    with pytest.raises(RuntimeError, match="sensor read failed"):
        _run_tick(engine, snapshot)

    # Then: only the previous completed tick was offered; stale cache cannot build a snapshot.
    assert len(observer.snapshots) == 1
    assert engine._effective_setpoints[("Veg Room", "Main")] is previous_effective
    assert engine._tick_effective_setpoints == {}
    assert engine._photoperiod_phases == {}
    assert engine._build_current_snapshot(snapshot, NOW) is None


def test_ramp_remaining_uses_the_same_observation_time_and_known_nominals() -> None:
    # Given: an in-flight heating ramp and known nominal targets for cooling/co2 only.
    room = ("Veg Room", "Main")
    ramp = RampState("heating", 20.0, 24.0, 30.0, NOW - timedelta(minutes=10))
    engine, _ = _engine(None, [])
    engine.setpoint_manager = SimpleNamespace(
        ramp_manager=SimpleNamespace(active_ramps={(*room, "heating"): ramp})
    )
    engine._tick_effective_setpoints[room] = {
        "nominal_heating_setpoint": 24.0,
        "nominal_cooling_setpoint": 20.0,
        "nominal_co2_setpoint": 800.0,
    }
    engine._photoperiod_phases[room] = PhotoperiodPhase.SUN
    snapshot = _snapshot(active_profile={"mode_id": 5, "submode_id": None})

    # When: current facts are composed at the instant used to inspect RAM ramp deadlines.
    current = engine._build_current_snapshot(snapshot, NOW)

    # Then: active remaining time is exact; zero is emitted only for known targets with no ramp.
    assert current is not None
    points = {point.series_id.value: point for point in current.series}
    assert current.observed_at == NOW
    assert points["veg_room.main.setpoint.ramp_remaining_seconds_heating"].value == 1200.0
    assert points["veg_room.main.setpoint.ramp_remaining_seconds_cooling"].value == 0.0
    assert points["veg_room.main.setpoint.ramp_remaining_seconds_co2"].value == 0.0
    assert "veg_room.main.setpoint.ramp_remaining_seconds_vpd" not in points
    assert all(point.observed_at == NOW for point in points.values())
