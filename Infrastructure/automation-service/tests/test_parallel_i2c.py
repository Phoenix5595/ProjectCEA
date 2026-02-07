"""Tests for parallel I2C hardware batch execution."""

from __future__ import annotations
import sys
import pathlib
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules["shared"] = MagicMock()
sys.modules["shared.logging"] = MagicMock()
sys.modules["shared.logging"].get_logger = MagicMock(return_value=MagicMock())

import asyncio
import pytest
from unittest.mock import patch

from app.control import hardware_batch as hb_module
from app.control.hardware_batch import (
    HardwareBatchExecutor,
    BatchResult,
    RelayOperation,
    DimmerOperation,
    DeviceOperationChain,
)


class TestBatchResult:
    def test_batch_result_defaults(self) -> None:
        result = BatchResult()
        assert result.success_count == 0
        assert result.failure_count == 0
        assert result.results == {}
        assert result.errors == {}
        assert result.timing_ms == 0.0

    def test_batch_result_with_values(self) -> None:
        result = BatchResult(
            success_count=3,
            failure_count=1,
            results={"dev1": True, "dev2": False},
            errors={"dev2": "timeout"},
            timing_ms=150.5,
        )
        assert result.success_count == 3
        assert result.failure_count == 1
        assert result.results["dev1"] is True
        assert result.errors["dev2"] == "timeout"


class TestDeviceOperationChain:
    def test_chain_creation(self) -> None:
        chain = DeviceOperationChain(device_key="Flower Room/main/light1")
        assert chain.device_key == "Flower Room/main/light1"
        assert chain.operations == []

    def test_add_relay_operation(self) -> None:
        chain = DeviceOperationChain(device_key="test/main/dev")
        mock_manager = MagicMock()
        op = RelayOperation(
            location="test",
            cluster="main",
            device_name="dev",
            state=1,
            relay_manager=mock_manager,
        )
        chain.add_relay(op)
        assert len(chain.operations) == 1
        assert chain.operations[0] == op

    def test_add_dimmer_operation(self) -> None:
        chain = DeviceOperationChain(device_key="test/main/dev")
        mock_manager = MagicMock()
        op = DimmerOperation(
            board_id="DFR0971_1",
            channel=0,
            intensity=75,
            dfr0971_manager=mock_manager,
        )
        chain.add_dimmer(op)
        assert len(chain.operations) == 1
        assert chain.operations[0] is op


class TestHardwareBatchExecutor:
    def test_executor_initialization(self) -> None:
        executor = HardwareBatchExecutor()
        assert executor.pending_count == 0
        assert executor.pending_operations == 0

    def test_queue_light_on(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()
        mock_dimmer = MagicMock()

        executor.queue_light_on(
            location="Flower Room",
            cluster="main",
            device_name="grow_light_1",
            intensity=75,
            relay_manager=mock_relay,
            dfr0971_manager=mock_dimmer,
            board_id="DFR0971_1",
            dimming_channel=0,
            relay_channel=5,
        )

        assert executor.pending_count == 1
        assert executor.pending_operations == 2

    def test_queue_light_off(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()
        mock_dimmer = MagicMock()

        executor.queue_light_off(
            location="Flower Room",
            cluster="main",
            device_name="grow_light_1",
            relay_manager=mock_relay,
            dfr0971_manager=mock_dimmer,
            board_id="DFR0971_1",
            dimming_channel=0,
            relay_channel=5,
        )

        assert executor.pending_count == 1
        assert executor.pending_operations == 2

    def test_queue_binary_device(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()

        executor.queue_binary_device(
            location="Flower Room",
            cluster="main",
            device_name="exhaust_fan",
            state=1,
            relay_manager=mock_relay,
        )

        assert executor.pending_count == 1
        assert executor.pending_operations == 1

    def test_clear_clears_all_chains(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()

        executor.queue_binary_device("Room", "main", "fan", 1, mock_relay)
        assert executor.pending_count == 1

        executor.clear()
        assert executor.pending_count == 0

    @pytest.mark.asyncio
    async def test_execute_empty_returns_empty_result(self) -> None:
        executor = HardwareBatchExecutor()
        result = await executor.execute()

        assert result.success_count == 0
        assert result.failure_count == 0

    @pytest.mark.asyncio
    async def test_execute_binary_device_success(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()
        mock_relay.set_device_state = MagicMock(return_value=(True, None))

        executor.queue_binary_device("Room", "main", "fan", 1, mock_relay)

        with patch.object(hb_module, "get_flag", return_value=True):
            result = await executor.execute()

        assert result.success_count == 1
        assert result.failure_count == 0
        assert result.results.get("Room/main/fan") is True

    @pytest.mark.asyncio
    async def test_execute_binary_device_failure(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()
        mock_relay.set_device_state = MagicMock(return_value=(False, "I2C error"))

        executor.queue_binary_device("Room", "main", "fan", 1, mock_relay)

        with patch.object(hb_module, "get_flag", return_value=True):
            result = await executor.execute()

        assert result.success_count == 0
        assert result.failure_count == 1
        assert "Room/main/fan" in result.errors

    @pytest.mark.asyncio
    async def test_execute_sequential_mode(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()
        mock_relay.set_device_state = MagicMock(return_value=(True, None))

        executor.queue_binary_device("Room", "main", "fan1", 1, mock_relay)
        executor.queue_binary_device("Room", "main", "fan2", 1, mock_relay)

        with patch.object(hb_module, "get_flag", return_value=False):
            result = await executor.execute()

        assert result.success_count == 2
        assert result.failure_count == 0


class TestLightSequencing:
    @pytest.mark.asyncio
    async def test_light_on_relay_before_dimmer(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()
        mock_dimmer = MagicMock()

        call_order = []

        def relay_side_effect(*args, **kwargs):
            call_order.append("relay")
            return (True, None)

        def dimmer_side_effect(*args, **kwargs):
            call_order.append("dimmer")
            return True

        mock_relay.set_device_state = MagicMock(side_effect=relay_side_effect)
        mock_dimmer.set_intensity = MagicMock(side_effect=dimmer_side_effect)

        executor.queue_light_on(
            "Room", "main", "light1", 75, mock_relay, mock_dimmer, "DFR0971_1", 0, 5
        )

        with patch.object(hb_module, "get_flag", return_value=False):
            await executor.execute()

        assert call_order == ["relay", "dimmer"]

    @pytest.mark.asyncio
    async def test_light_off_dimmer_before_relay(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()
        mock_dimmer = MagicMock()

        call_order = []

        def relay_side_effect(*args, **kwargs):
            call_order.append("relay")
            return (True, None)

        def dimmer_side_effect(*args, **kwargs):
            call_order.append("dimmer")
            return True

        mock_relay.set_device_state = MagicMock(side_effect=relay_side_effect)
        mock_dimmer.set_intensity = MagicMock(side_effect=dimmer_side_effect)

        executor.queue_light_off(
            "Room", "main", "light1", mock_relay, mock_dimmer, "DFR0971_1", 0, 5
        )

        with patch.object(hb_module, "get_flag", return_value=False):
            await executor.execute()

        assert call_order == ["dimmer", "relay"]


class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_multiple_devices_execute_in_parallel(self) -> None:
        executor = HardwareBatchExecutor()
        mock_relay = MagicMock()

        execution_times = []

        async def slow_relay(*args, **kwargs):
            start = asyncio.get_event_loop().time()
            await asyncio.sleep(0.05)
            execution_times.append(asyncio.get_event_loop().time() - start)
            return (True, None)

        mock_relay.set_device_state = MagicMock(return_value=(True, None))

        executor.queue_binary_device("Room", "main", "fan1", 1, mock_relay)
        executor.queue_binary_device("Room", "main", "fan2", 1, mock_relay)
        executor.queue_binary_device("Room", "main", "fan3", 1, mock_relay)

        with patch.object(hb_module, "get_flag", return_value=True):
            result = await executor.execute()

        assert result.success_count == 3
        assert result.failure_count == 0
