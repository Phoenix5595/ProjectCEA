"""Tests for relay-mcp-bugfix Task 8: set_channel diagnostic logging.

Verifies that ``MCP23017Driver.set_channel`` logs every valid call at
INFO level with channel, state, and caller information. This diagnostic
logging exists to investigate ch11 (R12) cycling in production and is
removed by Task 7 once R12 is observed steady.

These tests run with no real hardware: the driver is constructed against
a FakeSMBus so the underlying I2C bus is never touched. The
``simulation=True`` parameter was removed in Task 9 along with all other
simulation code paths.

Test layout note: this file sets up ``sys.path`` itself (rather than
relying on a global conftest) so it can be collected standalone.
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys
from unittest.mock import patch

# Path setup: ensure both ``automation-service/`` (for ``app.*``) and
# ``Infrastructure/`` (for ``shared.*``) are importable.
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_INFRA_ROOT = _SERVICE_ROOT.parent
for _p in (str(_SERVICE_ROOT), str(_INFRA_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.hardware.mcp23017 import MCP23017Driver  # noqa: E402


class _FakeSMBus:
    """Minimal in-memory stand-in for smbus2.SMBus.

    Records writes; reads return what was last written (or 0 for
    untouched registers). Enough surface to construct an MCP23017Driver
    without a real I2C bus.
    """

    def __init__(self, bus: int = 1) -> None:
        self.bus = bus
        self.regs: dict[tuple[int, int], int] = {}

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        self.regs[(addr, reg)] = value & 0xFF

    def read_byte_data(self, addr: int, reg: int) -> int:
        return self.regs.get((addr, reg), 0)

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_channel_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    """Filter caplog records to only the set_channel diagnostic lines."""
    return [r for r in records if "MCP23017Driver.set_channel called" in r.getMessage()]


class TestSetChannelDiagnosticLogging:
    """Task 8: verify set_channel emits the required INFO log."""

    def test_set_channel_emits_info_log_with_channel_state_caller(self, caplog):
        """A normal set_channel call must log channel, state, and caller at INFO."""
        with patch("smbus2.SMBus", new=_FakeSMBus):
            driver = MCP23017Driver(i2c_bus=1, i2c_address=0x20)
        with caplog.at_level(logging.INFO, logger="app.hardware.mcp23017"):
            result = driver.set_channel(11, True)

        assert result is True

        records = _set_channel_records(caplog.records)
        assert len(records) == 1, (
            f"expected exactly 1 set_channel INFO log, got {len(records)}: "
            f"{[r.getMessage() for r in records]}"
        )

        record = records[0]
        assert record.levelno == logging.INFO
        msg = record.getMessage()
        assert "channel=11" in msg
        assert "state=True" in msg
        # Caller must be a non-empty string; pytest's test method name is
        # the most likely caller in this unit-test context.
        assert "caller=" in msg
        # Sanity-check the caller token: should not be the diagnostic
        # method itself and not be "<unknown>" (we have a real caller).
        caller_token = next(tok for tok in msg.split() if tok.startswith("caller="))
        caller_value = caller_token.split("=", 1)[1]
        assert caller_value != "set_channel"
        assert caller_value != "<unknown>"

    def test_set_channel_off_state_is_logged(self, caplog):
        """The diagnostic must fire for state=False as well as True."""
        with patch("smbus2.SMBus", new=_FakeSMBus):
            driver = MCP23017Driver(i2c_bus=1, i2c_address=0x20)
        with caplog.at_level(logging.INFO, logger="app.hardware.mcp23017"):
            result = driver.set_channel(11, False)

        assert result is True
        records = _set_channel_records(caplog.records)
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "channel=11" in msg
        assert "state=False" in msg

    def test_set_channel_logs_each_call(self, caplog):
        """Three calls should produce three diagnostic log lines (1:1 ratio)."""
        with patch("smbus2.SMBus", new=_FakeSMBus):
            driver = MCP23017Driver(i2c_bus=1, i2c_address=0x20)
        with caplog.at_level(logging.INFO, logger="app.hardware.mcp23017"):
            driver.set_channel(0, True)
            driver.set_channel(11, True)
            driver.set_channel(11, False)

        records = _set_channel_records(caplog.records)
        assert len(records) == 3
        # Spot-check channels in order.
        assert "channel=0" in records[0].getMessage()
        assert "channel=11" in records[1].getMessage()
        assert "channel=11" in records[2].getMessage()
        assert "state=True" in records[1].getMessage()
        assert "state=False" in records[2].getMessage()

    def test_set_channel_invalid_channel_does_not_emit_diagnostic(self, caplog):
        """Invalid channels short-circuit before the diagnostic, so no INFO
        log should be emitted. The validation ``logger.error`` may still
        appear in caplog, but the diagnostic line must not."""
        with patch("smbus2.SMBus", new=_FakeSMBus):
            driver = MCP23017Driver(i2c_bus=1, i2c_address=0x20)
        with caplog.at_level(logging.INFO, logger="app.hardware.mcp23017"):
            result = driver.set_channel(99, True)

        assert result is False
        assert _set_channel_records(caplog.records) == []
