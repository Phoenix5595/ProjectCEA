from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import HTTPException
import pytest

from app.events import ConfigChangeEvent
from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.routes import pid, system_config
from app.routes.climate_periods import save_climate_periods
from app.schemas.climate_periods import PeriodInput, PeriodsSaveRequest
from app.schemas.pid import PIDModeUpdate, PIDParameterUpdate
from app.schemas.system_config import ConfigUpdateRequest, TuningGroup
from app.services import climate_timeline_apply as climate_apply_module
from shared.redis_keys import climate_period_cache_key


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _ClimatePeriodsRepository:
    def __init__(self, saved_period: dict[str, object]) -> None:
        self.saved_period = saved_period

    def validate_24h_coverage(self, _periods: list[dict[str, object]]) -> tuple[bool, list[str]]:
        return True, []

    async def get_periods(
        self,
        _location: str,
        _cluster: str,
        mode_id: int | None = None,
        submode_id: int | None = None,
        conn: object = None,
    ) -> list[dict[str, object]]:
        """Exact-NULL-safe slice read: mode+NULL submode selects only the base profile."""
        del conn
        if mode_id is None:
            return []
        if mode_id != 1:
            return []
        return [
            {
                "period_name": "day",
                "start_time": "00:00",
                "end_time": "00:00",
                "ramp_minutes": 0,
                "heating_setpoint": 20.0,
                "cooling_setpoint": 25.0,
                "vpd_setpoint": 1.0,
                "co2_setpoint": 800,
            }
        ]

    async def delete_periods(self, *_args: object, **_kwargs: object) -> bool:
        return True

    async def save_period(self, **_kwargs: object) -> dict[str, object]:
        return self.saved_period


class _ConfigRepository:
    async def log_config_version(self, **_kwargs: object) -> int:
        return 1


class _RecordingInvalidationState:
    def __init__(self) -> None:
        self.deletes: list[str] = []

    async def delete(self, key: str, skip_redis: bool = False) -> bool:
        del skip_redis
        self.deletes.append(key)
        return True


class _RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[ConfigChangeEvent] = []

    async def publish(self, event: ConfigChangeEvent) -> bool:
        self.events.append(event)
        return True


def _recording_notification_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_RecordingInvalidationState, _RecordingEventBus]:
    """Route the post-commit invalidator and event bus to recording fakes."""
    state_manager = _RecordingInvalidationState()
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(climate_apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(climate_apply_module, "get_event_bus", lambda: event_bus)
    return state_manager, event_bus


class _PeriodsConnection:
    """asyncpg-shaped transaction connection recording the advisory lock."""

    def __init__(self) -> None:
        self.locks: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.locks.append((query, args))
        return "SELECT 1"

    @asynccontextmanager
    async def transaction(self):
        yield


class _PeriodsPool:
    def __init__(self) -> None:
        self.connection = _PeriodsConnection()

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


@pytest.mark.asyncio
async def test_climate_replacement_with_equal_count_emits_content_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one persisted period whose replacement changes only a setpoint.
    repository = _ClimatePeriodsRepository({"period_name": "day", "heating_setpoint": 21.0})
    state_manager, event_bus = _recording_notification_boundaries(monkeypatch)
    database = SimpleNamespace(
        climate_periods_repo=repository,
        config_repo=_ConfigRepository(),
        room_mode_repo=SimpleNamespace(
            get_active_mode=AsyncMock(
                return_value={"mode_id": 1, "submode_id": None, "mode_name": "veg"}
            )
        ),
        _get_pool=AsyncMock(return_value=_PeriodsPool()),
    )
    sink = _RecordingSink()

    # When: the replacement commits with the same number of rows.
    response = await save_climate_periods(
        "Veg Room",
        "main",
        PeriodsSaveRequest(
            mode_id=1,
            periods=[
                PeriodInput(
                    period_name="day",
                    start_time="00:00",
                    end_time="00:00",
                    heating_setpoint=21.0,
                    cooling_setpoint=25.0,
                    vpd_setpoint=1.0,
                    co2_setpoint=800,
                )
            ],
        ),
        database,
        MutationRequestContext.create(),
        sink,
    )

    # Then: the event describes the committed content change rather than only row count,
    # and the exact active profile keys invalidate after the commit.
    assert [change.key for change in sink.events[0].payload.changes] == ["periods_digest"]
    assert response["notification_warning"] is None
    assert climate_period_cache_key("Veg Room", "main", 1, None) in state_manager.deletes
    assert climate_period_cache_key("Veg Room", "main", 2, None) not in state_manager.deletes
    assert any(key.startswith("schedules:") for key in state_manager.deletes)


class _PidRepository:
    def __init__(self, before: dict[str, float], write_succeeds: bool) -> None:
        self.before = before
        self.write_succeeds = write_succeeds

    async def get_pid_parameters(self, *_args: object) -> dict[str, float]:
        return self.before

    async def set_pid_parameters(self, *_args: object, **_kwargs: object) -> bool:
        return self.write_succeeds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before_kp", "write_succeeds", "expected_events"),
    [(1.0, True, 1), (2.0, True, 0), (1.0, False, 0)],
)
async def test_pid_update_emits_only_after_changed_success(
    monkeypatch: pytest.MonkeyPatch,
    before_kp: float,
    write_succeeds: bool,
    expected_events: int,
) -> None:
    # Given: a PID repository with a known before image and a deterministic validator.
    monkeypatch.setattr(pid, "check_rate_limit", lambda *_args: True)
    monkeypatch.setattr(
        pid,
        "validate_pid_parameters",
        lambda *_args: (True, None, {"kp": 2.0, "ki": 0.1, "kd": 0.0}),
    )
    database = SimpleNamespace(
        pid_repo=_PidRepository({"kp": before_kp, "ki": 0.1, "kd": 0.0}, write_succeeds)
    )
    sink = _RecordingSink()

    # When: the route completes, repeats a value, or rejects repository persistence.
    if write_succeeds:
        await pid._update_pid_parameters(
            "Veg Room",
            "main",
            "heater",
            PIDParameterUpdate(kp=2.0),
            database,
            SimpleNamespace(_config={}),
            MutationRequestContext.create(),
            sink,
        )
    else:
        with pytest.raises(HTTPException) as error:
            await pid._update_pid_parameters(
                "Veg Room",
                "main",
                "heater",
                PIDParameterUpdate(kp=2.0),
                database,
                SimpleNamespace(_config={}),
                MutationRequestContext.create(),
                sink,
            )
        assert error.value.status_code == 500

    # Then: only a committed semantic change is observable.
    assert len(sink.events) == expected_events


@pytest.mark.asyncio
async def test_pid_validation_failure_emits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a request rejected before repository persistence.
    monkeypatch.setattr(pid, "check_rate_limit", lambda *_args: True)
    monkeypatch.setattr(pid, "validate_pid_parameters", lambda *_args: (False, "kp is invalid", {}))
    sink = _RecordingSink()

    # When: validation rejects the requested parameter value.
    with pytest.raises(HTTPException) as error:
        await pid._update_pid_parameters(
            "Veg Room",
            "main",
            "heater",
            PIDParameterUpdate(kp=2.0),
            SimpleNamespace(pid_repo=_PidRepository({"kp": 1.0, "ki": 0.1, "kd": 0.0}, True)),
            SimpleNamespace(_config={}),
            MutationRequestContext.create(),
            sink,
        )

    # Then: no persisted-mutation event is fabricated.
    assert error.value.status_code == 400
    assert sink.events == []


class _ConfigLoader:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self._config_lock = RLock()
        self.writes = 0

    def validate_in_memory(self, _raw: dict[str, object]) -> None:
        return None

    def write_full_config(self, raw: dict[str, object]) -> None:
        self.writes += 1
        self.config_path.write_text("control:\n  update_interval: 2\nhardware: {}\n")

    def reload(self) -> None:
        return None


@pytest.mark.asyncio
async def test_system_config_noop_does_not_emit_or_rewrite(tmp_path: Path) -> None:
    # Given: a persisted configuration equal to the requested update.
    config_path = tmp_path / "automation_config.yaml"
    config_path.write_text("control:\n  update_interval: 2\nhardware: {}\n")
    config = _ConfigLoader(config_path)
    sink = _RecordingSink()

    # When: the same update is submitted.
    await system_config.update_system_config(
        ConfigUpdateRequest(tuning=TuningGroup(update_interval=2)),
        config,
        MutationRequestContext(UUID("7552d5f1-0a9a-43e8-a63b-26a60d126c2e")),
        sink,
    )

    # Then: no write or fabricated mutation event occurs.
    assert config.writes == 0
    assert sink.events == []


class _PidModeRepository:
    def __init__(self, mode: str, write_succeeds: bool) -> None:
        self.mode = mode
        self.write_succeeds = write_succeeds

    async def get_pid_control_mode(self, *_args: object) -> dict[str, object]:
        return {
            "control_mode": self.mode,
            "hysteresis_high": 1.0,
            "hysteresis_low": 0.5,
        }

    async def set_pid_control_mode(self, *_args: object, **_kwargs: object) -> bool:
        return self.write_succeeds

    async def get_autotune_state(self, *_args: object) -> dict[str, bool]:
        return {"is_active": False}

    async def get_pid_parameters(self, *_args: object) -> dict[str, object]:
        return {"updated_at": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before_mode", "write_succeeds", "expected_events"),
    [("pid", True, 1), ("on_off", True, 0), ("pid", False, 0)],
)
async def test_pid_mode_update_emits_only_after_changed_success(
    before_mode: str,
    write_succeeds: bool,
    expected_events: int,
) -> None:
    # Given: a mode repository with a known committed before-image.
    repository = _PidModeRepository(before_mode, write_succeeds)
    sink = _RecordingSink()

    # When: a mode update changes, repeats, or fails its database write.
    if write_succeeds:
        await pid._set_pid_mode(
            "Veg Room",
            "main",
            "heater",
            PIDModeUpdate(mode="on_off", hysteresis_high=1.0, hysteresis_low=0.5),
            SimpleNamespace(pid_repo=repository),
            MutationRequestContext.create(),
            sink,
        )
    else:
        with pytest.raises(HTTPException) as error:
            await pid._set_pid_mode(
                "Veg Room",
                "main",
                "heater",
                PIDModeUpdate(mode="on_off", hysteresis_high=1.0, hysteresis_low=0.5),
                SimpleNamespace(pid_repo=repository),
                MutationRequestContext.create(),
                sink,
            )

        assert error.value.status_code == 500

    # Then: only a changed, committed mode selection is visible.
    assert len(sink.events) == expected_events


class _PidResetRepository:
    def __init__(self, before_kp: float, write_succeeds: bool) -> None:
        self.before_kp = before_kp
        self.write_succeeds = write_succeeds

    async def get_pid_parameters(self, *_args: object) -> dict[str, float]:
        return {"kp": self.before_kp, "ki": 0.1, "kd": 0.0}

    async def set_pid_parameters(self, *_args: object, **_kwargs: object) -> bool:
        return self.write_succeeds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before_kp", "write_succeeds", "expected_events"),
    [(1.0, True, 1), (2.0, True, 0), (1.0, False, 0)],
)
async def test_pid_reset_emits_only_after_changed_success(
    before_kp: float,
    write_succeeds: bool,
    expected_events: int,
) -> None:
    # Given: PID defaults and a repository with a known current parameter set.
    repository = _PidResetRepository(before_kp, write_succeeds)
    sink = _RecordingSink()
    config = SimpleNamespace(
        get_pid_params_for_device=lambda _device_type: {"kp": 2.0, "ki": 0.1, "kd": 0.0}
    )

    # When: reset commits a change, repeats the defaults, or rejects persistence.
    if write_succeeds:
        await pid._reset_pid_parameters(
            "Veg Room",
            "main",
            "heater",
            SimpleNamespace(pid_repo=repository),
            config,
            MutationRequestContext.create(),
            sink,
        )
    else:
        with pytest.raises(HTTPException) as error:
            await pid._reset_pid_parameters(
                "Veg Room",
                "main",
                "heater",
                SimpleNamespace(pid_repo=repository),
                config,
                MutationRequestContext.create(),
                sink,
            )

        assert error.value.status_code == 500

    # Then: reset follows the same post-success and no-op-silence contract.
    assert len(sink.events) == expected_events
