"""DFR0971 passthrough contract: this system sends the finest unit the DAC accepts.

The scheduler computes a continuous intensity float; the only quantization
allowed is the driver's own 12-bit DAC truncation. A 1-LSB change (0.0244%)
must reach the hardware, and unchanged codes must not re-write the bus.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.control.device_controller import DeviceController
from app.control.hardware_batch import DimmerOperation, HardwareBatchExecutor


class FakeDfr:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float]] = []

    def set_intensity(self, board_id: str, channel: int, intensity: float) -> bool:
        self.calls.append((board_id, channel, intensity))
        return True


class FakeRelay:
    def __init__(self) -> None:
        self.states: list[int] = []

    async def set_device_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        state: int,
        mode: str = "auto",
        check_interlock: bool = True,
    ) -> tuple[bool, str | None]:
        self.states.append(state)
        return True, None


class NoTelemetryDatabase:
    _automation_redis = None


DEVICE = ("Veg Room", "main", "grow_light_1")
DEVICE_INFO = {"dimming_board_id": "board-88", "dimming_channel": 0, "channel": 3}


def _controller(relay: FakeRelay, dfr: FakeDfr) -> DeviceController:
    controller = DeviceController.__new__(DeviceController)
    controller.relay_manager = relay
    controller.database = NoTelemetryDatabase()
    controller.dfr0971_manager = dfr
    controller.binary_hysteresis = 0.1
    controller._last_light_command = {}
    controller._last_applied_light = {}
    return controller


@pytest.mark.asyncio
async def test_float_intensity_reaches_the_driver_unrounded() -> None:
    # Given: a light commanded at exactly 50.01% — a value integer rounding would destroy.
    relay, dfr = FakeRelay(), FakeDfr()
    controller = _controller(relay, dfr)

    # When: the controller drives the light.
    await controller._control_dimmable_light(*DEVICE, DEVICE_INFO, 0.5001)

    # Then: the driver receives the float percent, not a rounded 50.
    assert dfr.calls == [("board-88", 0, pytest.approx(50.01))]


@pytest.mark.asyncio
async def test_sub_percent_change_within_one_lsb_is_deduplicated() -> None:
    # Given: a light already commanded at 50% (DAC code int(0.5*4095) = 2047).
    relay, dfr = FakeRelay(), FakeDfr()
    controller = _controller(relay, dfr)
    await controller._control_dimmable_light(*DEVICE, DEVICE_INFO, 0.5)

    # When: the recomputed intensity drifts by less than one LSB (0.5001%).
    await controller._control_dimmable_light(*DEVICE, DEVICE_INFO, 0.5001)

    # Then: no redundant I2C write occurs.
    assert len(dfr.calls) == 1


@pytest.mark.asyncio
async def test_one_lsb_change_commands_the_driver_again() -> None:
    # Given: a light already commanded at 50% (code 2047).
    relay, dfr = FakeRelay(), FakeDfr()
    controller = _controller(relay, dfr)
    await controller._control_dimmable_light(*DEVICE, DEVICE_INFO, 0.5)

    # When: the recomputed intensity crosses the next DAC code (0.5010% -> code 2051).
    await controller._control_dimmable_light(*DEVICE, DEVICE_INFO, 0.5010)

    # Then: the finer step reaches the hardware immediately, every tick.
    assert len(dfr.calls) == 2
    assert dfr.calls[1][2] == pytest.approx(50.10)


@pytest.mark.asyncio
async def test_zero_sets_dimmer_before_relay_off() -> None:
    # Given: a light being switched off.
    relay, dfr = FakeRelay(), FakeDfr()
    controller = _controller(relay, dfr)
    order: list[str] = []
    original = dfr.set_intensity

    def recording(board_id: str, channel: int, intensity: float) -> bool:
        order.append("dimmer")
        return original(board_id, channel, intensity)

    dfr.set_intensity = recording  # type: ignore[method-assign]
    original_relay = relay.set_device_state

    async def relay_recording(*args: Any, **kwargs: Any) -> tuple[bool, str | None]:
        order.append("relay")
        return await original_relay(*args, **kwargs)

    relay.set_device_state = relay_recording  # type: ignore[method-assign]

    # When: the intensity reaches zero.
    await controller._control_dimmable_light(*DEVICE, DEVICE_INFO, 0.0)

    # Then: signal before power, with an exact zero to the driver.
    assert order == ["dimmer", "relay"]
    assert dfr.calls == [("board-88", 0, 0.0)]
    assert relay.states == [0]


def test_batch_dimmer_operation_carries_the_float_percent() -> None:
    # Given: a batched light command at 50.01%.
    executor = HardwareBatchExecutor()
    relay, dfr = FakeRelay(), FakeDfr()

    # When: the command is queued.
    executor.queue_light_on(
        location=DEVICE[0],
        cluster=DEVICE[1],
        device_name=DEVICE[2],
        intensity=50.01,
        relay_manager=relay,
        dfr0971_manager=dfr,
        board_id="board-88",
        dimming_channel=0,
        relay_channel=3,
    )

    # Then: the dimmer operation carries the float while telemetry keeps whole percent.
    chain = executor._chains["Veg Room/main/grow_light_1"]
    dimmer = next(op for op in chain.operations if isinstance(op, DimmerOperation))
    assert dimmer.intensity == pytest.approx(50.01)
    intent = executor._light_intents["Veg Room/main/grow_light_1"]
    assert intent["intensity_percent"] == 50
