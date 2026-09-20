from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
import pytest

from app.control.device_command_service import (
    AutoCommand,
    DeviceCommandHardwareError,
    DeviceCommandService,
    ManualOffCommand,
    TimedOnCommand,
)
from app.control.relay_manager import RelayManager
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.routes.hardware import RelayChannelControlRequest, set_relay_channel_state


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _Registry:
    def __init__(self) -> None:
        self.snapshot = RuntimeDeviceSnapshot.create(
            version=1,
            hierarchy={
                "Veg Room": {
                    "main": {
                        "heater_veg_1": {"device_id": 1, "channel": 2},
                        "fan_veg_1": {"device_id": 2, "channel": 3},
                    }
                }
            },
            mode_parameters={},
            light_intensities={},
            light_programs=[],
        )

    def subscribe(self, _consumer: Any) -> None:
        return None


class _Relay:
    def __init__(self) -> None:
        self.states = {
            ("Veg Room", "main", "heater_veg_1"): 0,
            ("Veg Room", "main", "fan_veg_1"): 0,
        }
        self.commands: list[tuple[str, str, str, int, str]] = []
        self.channel_commands: list[tuple[int, int]] = []
        self.succeeds = True

    def get_device_state(self, location: str, cluster: str, device_name: str) -> int | None:
        return self.states.get((location, cluster, device_name))

    async def set_device_state(
        self, location: str, cluster: str, device_name: str, state: int, mode: str
    ) -> tuple[bool, str | None]:
        if not self.succeeds:
            return False, "Hardware error"
        self.states[(location, cluster, device_name)] = state
        self.commands.append((location, cluster, device_name, state, mode))
        return True, None

    async def set_channel_state(self, channel: int, state: int) -> bool:
        self.channel_commands.append((channel, state))
        return True

    def record_command_state(
        self,
        _key: tuple[str, str, str],
        _mode: str,
        _expires_at: datetime | None,
        _prior_mode: str | None,
    ) -> None:
        return None


class _ControlHistory:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def log_control_action(self, *args: Any, **record: Any) -> bool:
        self.records.append({"args": args, **record})
        return True


class _RedisClient:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def get(self, _key: str) -> str | None:
        return None

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        return 1


class _AutomationRedis:
    def __init__(self, redis_client: _RedisClient) -> None:
        self.redis_client = redis_client


class _Mcp:
    def set_channel(self, _channel: int, _state: bool) -> bool:
        return True


class _Interlocks:
    def check_interlock(self, *_args: Any, **_kwargs: Any) -> tuple[bool, None]:
        return True, None


def _service() -> tuple[DeviceCommandService, _Clock, _Relay, _ControlHistory, _RedisClient]:
    clock = _Clock(datetime(2026, 7, 30, 12, 0, tzinfo=UTC))
    relay = _Relay()
    history = _ControlHistory()
    redis_client = _RedisClient()
    registry_port: Any = _Registry()
    relay_port: Any = relay
    history_port: Any = history
    automation_redis: Any = _AutomationRedis(redis_client)
    service = DeviceCommandService(
        runtime_device_registry=registry_port,
        relay_manager=relay_port,
        control_action_repository=history_port,
        automation_redis=automation_redis,
        now=clock,
    )
    return service, clock, relay, history, redis_client


@pytest.mark.asyncio
async def test_timed_on_is_one_atomic_command_and_records_one_audit_event() -> None:
    # Given: an assigned device in its startup AUTO command state.
    service, _clock, relay, history, _redis_client = _service()
    await service.initialize_startup()

    # When: the caller submits one timed-on command.
    result = await service.execute(
        "Veg Room", "main", "heater_veg_1", TimedOnCommand(duration_seconds=300)
    )

    # Then: the relay command, command authority, and audit transition agree.
    assert relay.commands == [("Veg Room", "main", "heater_veg_1", 1, "timed_on")]
    assert result.mode == "timed_on"
    assert result.expires_at == datetime(2026, 7, 30, 12, 5, tzinfo=UTC)
    assert len(history.records) == 1


PriorMode = Literal["auto", "scheduled", "manual_off"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_mode", ["auto", "scheduled", "manual_off"])
async def test_timer_expiry_restores_captured_prior_mode_without_database_state(
    prior_mode: PriorMode,
) -> None:
    # Given: a prior automatic or manual-off command state before a timed command.
    service, clock, relay, _history, _redis_client = _service()
    await service.initialize_startup()
    match prior_mode:
        case "auto" | "scheduled":
            service.record_automatic_mode("Veg Room", "main", "heater_veg_1", prior_mode)
        case "manual_off":
            await service.execute("Veg Room", "main", "heater_veg_1", ManualOffCommand())
        case unreachable:
            raise AssertionError(f"Unexpected prior mode fixture: {unreachable}")
    await service.execute("Veg Room", "main", "heater_veg_1", TimedOnCommand(duration_seconds=60))
    clock.now += timedelta(seconds=60)

    # When: the timer sweep reaches expiry.
    await service.expire_commands()

    # Then: the timer is gone and the captured authority is restored.
    state = service.get_command_state("Veg Room", "main", "heater_veg_1")
    assert state.mode == prior_mode
    assert state.expires_at is None
    assert relay.commands[-1] == ("Veg Room", "main", "heater_veg_1", 1, "timed_on")


@pytest.mark.asyncio
async def test_startup_returns_all_assigned_identities_to_auto_and_clears_exact_raw_keys() -> None:
    # Given: a service with assigned identities and an isolated Redis fake.
    service, _clock, _relay, _history, redis_client = _service()

    # When: startup state is initialized.
    await service.initialize_startup()

    # Then: every assigned identity starts AUTO and only sixteen known raw keys are deleted.
    assert service.get_command_state("Veg Room", "main", "heater_veg_1").mode == "auto"
    assert service.get_command_state("Veg Room", "main", "fan_veg_1").mode == "auto"
    assert redis_client.deleted == [f"cea:relay:manual_override:{channel}" for channel in range(16)]


@pytest.mark.asyncio
async def test_restart_discards_active_assigned_timer_before_control_resumes() -> None:
    # Given: an active assigned timer from before a process restart.
    service, _clock, _relay, _history, redis_client = _service()
    await service.initialize_startup()
    await service.execute("Veg Room", "main", "heater_veg_1", TimedOnCommand(duration_seconds=60))
    redis_client.deleted.clear()

    # When: startup initialization runs again for the new process lifecycle.
    await service.initialize_startup()

    # Then: the assigned timer is discarded and exactly the raw-key set is cleared.
    state = service.get_command_state("Veg Room", "main", "heater_veg_1")
    assert state.mode == "auto"
    assert state.expires_at is None
    assert redis_client.deleted == [f"cea:relay:manual_override:{channel}" for channel in range(16)]


@pytest.mark.asyncio
async def test_failed_hardware_command_retains_prior_authority_without_false_audit() -> None:
    # Given: an assigned AUTO device whose relay write fails.
    service, _clock, relay, history, _redis_client = _service()
    await service.initialize_startup()
    relay.succeeds = False

    # When: a timed-on command is attempted.
    with pytest.raises(DeviceCommandHardwareError):
        await service.execute(
            "Veg Room", "main", "heater_veg_1", TimedOnCommand(duration_seconds=60)
        )

    # Then: neither the command authority nor audit trail claims a false transition.
    assert service.get_command_state("Veg Room", "main", "heater_veg_1").mode == "auto"
    assert history.records == []


@pytest.mark.asyncio
async def test_raw_on_rejects_missing_duration_and_assigned_channel() -> None:
    # Given: a raw route sharing the assigned-channel command resolver.
    service, _clock, relay, _history, redis_client = _service()
    redis = _AutomationRedis(redis_client)
    relay_port: Any = relay
    redis_port: Any = redis

    # When / Then: unsafe raw ON forms are rejected before a relay write.
    with pytest.raises(HTTPException) as missing_duration:
        await set_relay_channel_state(
            8, RelayChannelControlRequest(state=1), relay_port, redis_port, service
        )
    assert "duration" in str(missing_duration.value.detail)
    with pytest.raises(HTTPException) as assigned_channel:
        await set_relay_channel_state(
            2,
            RelayChannelControlRequest(state=1, duration_seconds=60),
            relay_port,
            redis_port,
            service,
        )
    assert "assigned" in str(assigned_channel.value.detail)
    assert relay.channel_commands == []


@pytest.mark.asyncio
async def test_auto_and_manual_off_are_discriminated_commands() -> None:
    # Given: an assigned device with an active timed command.
    service, _clock, relay, _history, _redis_client = _service()
    await service.initialize_startup()
    await service.execute("Veg Room", "main", "heater_veg_1", TimedOnCommand(duration_seconds=60))

    # When: AUTO and MANUAL_OFF are each submitted as one command variant.
    auto_result = await service.execute("Veg Room", "main", "heater_veg_1", AutoCommand())
    manual_off_result = await service.execute(
        "Veg Room", "main", "heater_veg_1", ManualOffCommand()
    )

    # Then: AUTO releases authority and MANUAL_OFF safely writes OFF.
    assert auto_result.mode == "auto"
    assert manual_off_result.mode == "manual_off"
    assert relay.commands[-1] == ("Veg Room", "main", "heater_veg_1", 0, "manual_off")


@pytest.mark.asyncio
async def test_relay_manager_does_not_own_assigned_device_mode() -> None:
    # Given: an installed assigned identity and a successful relay driver.
    registry: Any = _Registry()
    mcp: Any = _Mcp()
    interlocks: Any = _Interlocks()
    relay_manager = RelayManager(mcp, registry, interlocks)

    # When: a relay write receives a legacy mode argument.
    success, _reason = await relay_manager.set_device_state(
        "Veg Room", "main", "heater_veg_1", 1, "manual"
    )

    # Then: physical state is updated but mode authority remains outside RelayManager.
    assert success is True
    assert relay_manager.get_device_mode("Veg Room", "main", "heater_veg_1") is None
