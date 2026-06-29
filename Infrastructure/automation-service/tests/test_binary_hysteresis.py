"""Tests for binary device hysteresis in DeviceController._control_binary_device.

Verifies the spec from relay-mcp-bugfix Task 5:
- _last_binary_state is tracked per (location, cluster, device_name).
- ON -> OFF only when output < (0.5 - band)
- OFF -> ON only when output > (0.5 + band)
- In the band, prior state is preserved.
- band defaults to 0.1; per-device override is read from device_info["binary_hysteresis"].

These tests run with no real hardware, database, or Redis (MagicMock / AsyncMock).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure service root + Infrastructure/ are importable so `from app...` and
# `from shared...` resolve. tests/ -> automation-service/ -> Infrastructure/.
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))
_INFRA_ROOT = _SERVICE_ROOT.parent
if str(_INFRA_ROOT) not in sys.path:
    sys.path.insert(0, str(_INFRA_ROOT))

from app.control.device_controller import DeviceController


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

def _make_controller(binary_hysteresis: float = 0.1) -> tuple[DeviceController, MagicMock]:
    """Build a DeviceController with mocked relay_manager and database."""
    relay_manager = MagicMock()
    relay_manager.set_channel_state = AsyncMock(return_value=True)
    relay_manager._channel_map = {7: ("Veg Room", "main", "Ivation 35 pints")}
    database = MagicMock()
    controller = DeviceController(
        relay_manager=relay_manager,
        database_manager=database,
        binary_hysteresis=binary_hysteresis,
    )
    return controller, relay_manager


def _call(
    controller: DeviceController,
    output: float,
    location: str = "Veg Room",
    cluster: str = "main",
    device: str = "Ivation 35 pints",
    channel: int = 7,
    device_type: str = "dehumidifier",
    device_info: dict | None = None,
):
    """Invoke _control_binary_device without the batch_executor path."""
    if device_info is None:
        device_info = {}
    return controller._control_binary_device(
        location,
        cluster,
        device,
        device_type,
        channel,
        output,
        batch_executor=None,
        device_info=device_info,
    )


# ---------------------------------------------------------------------------
# Default band (0.1)
# ---------------------------------------------------------------------------

class TestBinaryHysteresisDefaultBand:
    """band = 0.1 (default)."""

    @pytest.mark.asyncio
    async def test_initial_high_output_turns_on(self):
        """First command, output 0.6 > 0.5 → ON (no prior state yet)."""
        controller, rm = _make_controller()
        await _call(controller, output=0.6)
        rm.set_channel_state.assert_awaited_once_with(7, 1)

    @pytest.mark.asyncio
    async def test_initial_low_output_turns_off(self):
        """First command, output 0.3 < 0.5 → OFF."""
        controller, rm = _make_controller()
        await _call(controller, output=0.3)
        rm.set_channel_state.assert_awaited_once_with(7, 0)

    @pytest.mark.asyncio
    async def test_output_at_half_keeps_prior_on(self):
        """Prior ON, output=0.5 sits in band → keep ON, no hardware write."""
        controller, rm = _make_controller()
        await _call(controller, output=0.6)
        rm.set_channel_state.reset_mock()
        await _call(controller, output=0.5)
        assert rm.set_channel_state.await_count == 0
        assert controller._last_binary_state[("Veg Room", "main", "Ivation 35 pints")] == 1

    @pytest.mark.asyncio
    async def test_output_at_half_keeps_prior_off(self):
        """Prior OFF, output=0.5 sits in band → keep OFF, no hardware write."""
        controller, rm = _make_controller()
        await _call(controller, output=0.3)
        rm.set_channel_state.reset_mock()
        await _call(controller, output=0.5)
        assert rm.set_channel_state.await_count == 0
        assert controller._last_binary_state[("Veg Room", "main", "Ivation 35 pints")] == 0

    @pytest.mark.asyncio
    async def test_on_with_output_at_lower_boundary_keeps_on(self):
        """Prior ON, output = 0.5 - band = 0.4 → still ON (strict <)."""
        controller, rm = _make_controller()
        await _call(controller, output=0.6)
        rm.set_channel_state.reset_mock()
        # Boundary: output == 0.5 - band; strict <, so no transition
        await _call(controller, output=0.4)
        assert rm.set_channel_state.await_count == 0

    @pytest.mark.asyncio
    async def test_on_with_output_below_lower_band_turns_off(self):
        """Prior ON, output=0.3 < 0.5 - band (0.4) → OFF."""
        controller, rm = _make_controller()
        await _call(controller, output=0.6)
        rm.set_channel_state.reset_mock()
        await _call(controller, output=0.3)
        rm.set_channel_state.assert_awaited_once_with(7, 0)

    @pytest.mark.asyncio
    async def test_off_with_output_at_upper_boundary_keeps_off(self):
        """Prior OFF, output = 0.5 + band = 0.6 → still OFF (strict >)."""
        controller, rm = _make_controller()
        await _call(controller, output=0.3)
        rm.set_channel_state.reset_mock()
        # Boundary: output == 0.5 + band; strict >, so no transition
        await _call(controller, output=0.6)
        assert rm.set_channel_state.await_count == 0

    @pytest.mark.asyncio
    async def test_off_with_output_above_upper_band_turns_on(self):
        """Prior OFF, output=0.7 > 0.5 + band (0.6) → ON."""
        controller, rm = _make_controller()
        await _call(controller, output=0.3)
        rm.set_channel_state.reset_mock()
        await _call(controller, output=0.7)
        rm.set_channel_state.assert_awaited_once_with(7, 1)

    @pytest.mark.asyncio
    async def test_chatter_blocked_in_band(self):
        """Three consecutive 0.5 outputs after ON → still 1 call, then no more writes."""
        controller, rm = _make_controller()
        await _call(controller, output=0.6)  # ON
        rm.set_channel_state.reset_mock()
        # Three calls at 0.5 should each re-issue the same state? No — they're no-ops
        # because the band keeps prior state. The hardware is called only when state
        # actually changes. The first call after ON re-asserts 1 (the kept state), and
        # subsequent calls in the band skip hardware.
        await _call(controller, output=0.5)
        await _call(controller, output=0.5)
        await _call(controller, output=0.5)
        # Spec is: keep prior state — that means we DO send the same state to hardware
        # to maintain liveness? The simpler interpretation is "no transition" = skip
        # the hardware write to avoid chatter. Confirm what we want:
        #   The task says: "preventing chatter". The whole point is to NOT call hardware
        #   when the band keeps the state. So we expect the count to drop.
        assert rm.set_channel_state.await_count == 0, (
            f"Expected zero hardware calls in band, got {rm.set_channel_state.await_count}"
        )


# ---------------------------------------------------------------------------
# Per-device override
# ---------------------------------------------------------------------------

class TestBinaryHysteresisPerDeviceOverride:
    """Per-device binary_hysteresis overrides the class default."""

    @pytest.mark.asyncio
    async def test_per_device_band_wider(self):
        """device_info.binary_hysteresis=0.3 widens the band.

        Prior ON, output=0.25 > 0.5 - 0.3 = 0.2, so stays ON (no hardware write).
        """
        controller, rm = _make_controller(binary_hysteresis=0.1)
        await _call(controller, output=0.6, device_info={"binary_hysteresis": 0.3})
        rm.set_channel_state.reset_mock()
        await _call(controller, output=0.25, device_info={"binary_hysteresis": 0.3})
        assert rm.set_channel_state.await_count == 0

    @pytest.mark.asyncio
    async def test_per_device_band_triggers_earlier(self):
        """device_info.binary_hysteresis=0.3 — output=0.15 < 0.2 → OFF."""
        controller, rm = _make_controller(binary_hysteresis=0.1)
        await _call(controller, output=0.6, device_info={"binary_hysteresis": 0.3})
        rm.set_channel_state.reset_mock()
        await _call(controller, output=0.15, device_info={"binary_hysteresis": 0.3})
        rm.set_channel_state.assert_awaited_once_with(7, 0)

    @pytest.mark.asyncio
    async def test_default_band_used_when_device_info_absent(self):
        """No per-device override → fall back to class default (0.1)."""
        controller, rm = _make_controller(binary_hysteresis=0.1)
        await _call(controller, output=0.6)  # ON
        rm.set_channel_state.reset_mock()
        # output=0.35 < 0.4 (default 0.5 - 0.1) → OFF
        await _call(controller, output=0.35)
        rm.set_channel_state.assert_awaited_once_with(7, 0)


# ---------------------------------------------------------------------------
# State tracking is per (location, cluster, device_name)
# ---------------------------------------------------------------------------

class TestBinaryHysteresisStateIsolation:
    """Different devices have independent hysteresis state."""

    @pytest.mark.asyncio
    async def test_two_devices_track_state_independently(self):
        """Device A going OFF does not affect device B's state."""
        rm = MagicMock()
        rm.set_channel_state = AsyncMock(return_value=True)
        rm._channel_map = {
            7: ("Veg Room", "main", "Ivation 35 pints"),
            9: ("Veg Room", "main", "exhaust_fan"),
        }
        database = MagicMock()
        controller = DeviceController(rm, database, binary_hysteresis=0.1)

        # Turn device A ON
        await controller._control_binary_device(
            "Veg Room", "main", "Ivation 35 pints", "dehumidifier", 7, 0.6
        )
        # Keep device B OFF
        await controller._control_binary_device(
            "Veg Room", "main", "exhaust_fan", "fan", 9, 0.3
        )
        rm.set_channel_state.reset_mock()

        # Now feed both a band value (0.5). Both should keep their prior state
        # independently. A → ON (1), B → OFF (0).
        await controller._control_binary_device(
            "Veg Room", "main", "Ivation 35 pints", "dehumidifier", 7, 0.5
        )
        await controller._control_binary_device(
            "Veg Room", "main", "exhaust_fan", "fan", 9, 0.5
        )
        assert rm.set_channel_state.await_count == 0, (
            "Both devices are in the band — neither should call hardware"
        )

        # Now drop A below band. Only A should re-issue.
        await controller._control_binary_device(
            "Veg Room", "main", "Ivation 35 pints", "dehumidifier", 7, 0.2
        )
        rm.set_channel_state.assert_awaited_once_with(7, 0)
