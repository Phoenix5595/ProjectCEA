"""Tests for InterlockManager.

Covers Task 6 (relay-mcp-bugfix): dead interlock rule removed from
``automation_config.yaml``; WARNING logged at startup when no rules are
configured; ``check_interlock`` is a no-op (returns (True, None)) for all
devices when ``interlock_rules`` is empty.

Run with::

    python -m pytest tests/test_interlock_manager.py -v
"""

from __future__ import annotations

import logging

import pytest

from app.automation.interlock_manager import InterlockManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_device_config() -> dict:
    """Minimal device config with two rooms and a few devices per cluster."""
    return {
        "Flower Room": {
            "main": {
                "exhaust_fan": {"channel": 1, "device_type": "fan"},
                "Heater Flower": {"channel": 0, "device_type": "heating"},
            },
        },
        "Veg Room": {
            "main": {
                "exhaust_fan": {"channel": 9, "device_type": "fan"},
                "Heater Veg": {"channel": 6, "device_type": "heating"},
            },
        },
    }


# ---------------------------------------------------------------------------
# Task 6: WARNING log when no interlock rules are configured
# ---------------------------------------------------------------------------


class TestStartupWarning:
    """When ``interlock_rules`` is empty, ``InterlockManager.__init__`` must
    emit a WARNING that names the missing safety rule and references
    ``AGENTS.md`` so on-call engineers do not silently rely on un-enforced
    protection."""

    def test_warning_logged_when_no_rules(
        self,
        empty_device_config: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="app.automation.interlock_manager"):
            InterlockManager(
                device_config=empty_device_config,
                interlock_rules=[],
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "Expected a WARNING log when interlock_rules is empty"
        msg = warnings[0].getMessage()
        assert "No interlock rules configured" in msg
        assert "AGENTS.md" in msg
        assert "heating failure" in msg
        assert "exhaust inhibition" in msg

    def test_no_warning_logged_when_rules_present(
        self,
        empty_device_config: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        rules = [
            {
                "when_device": "exhaust_fan",
                "then_device": "Heater Flower",
                "action": "force_off",
            }
        ]
        with caplog.at_level(logging.WARNING, logger="app.automation.interlock_manager"):
            InterlockManager(
                device_config=empty_device_config,
                interlock_rules=rules,
            )

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "No interlock rules configured" in r.getMessage()
        ]
        assert not warnings, "Did not expect the empty-rules WARNING when rules exist"


# ---------------------------------------------------------------------------
# Task 6: check_interlock must be a no-op when no rules
# ---------------------------------------------------------------------------


class TestCheckInterlockNoRules:
    """With no interlock rules and no per-device ``interlock_with``, every
    device must be reported as allowed to operate (``(True, None)``)."""

    def test_returns_allowed_for_exhaust_fan(self, empty_device_config: dict) -> None:
        mgr = InterlockManager(
            device_config=empty_device_config,
            interlock_rules=[],
        )
        # exhaust_fan is ON in the state dict - in a buggy world this would
        # still block something. With no rules it must not block.
        device_states = {
            ("Flower Room", "main", "exhaust_fan"): 1,
            ("Flower Room", "main", "Heater Flower"): 0,
        }
        allowed, reason = mgr.check_interlock(
            "Flower Room",
            "main",
            "Heater Flower",
            device_states,
        )
        assert allowed is True
        assert reason is None

    def test_returns_allowed_for_heater_with_exhaust_on(self, empty_device_config: dict) -> None:
        mgr = InterlockManager(
            device_config=empty_device_config,
            interlock_rules=[],
        )
        # Simulate the exact scenario the deleted rule tried (and failed) to
        # cover: exhaust_fan ON, Heater Flower requesting to turn on.
        device_states = {
            ("Flower Room", "main", "exhaust_fan"): 1,
            ("Flower Room", "main", "Heater Flower"): 0,
        }
        allowed, reason = mgr.check_interlock(
            "Flower Room",
            "main",
            "Heater Flower",
            device_states,
            requested_load=80.0,
        )
        assert allowed is True
        assert reason is None

    def test_returns_allowed_for_all_devices_in_both_rooms(self, empty_device_config: dict) -> None:
        mgr = InterlockManager(
            device_config=empty_device_config,
            interlock_rules=[],
        )
        # Turn every device ON in both rooms; with no rules, nothing must
        # ever be blocked.
        for room in empty_device_config:
            for device in empty_device_config[room]["main"]:
                device_states = {(room, "main", d): 1 for d in empty_device_config[room]["main"]}
                allowed, reason = mgr.check_interlock(
                    room,
                    "main",
                    device,
                    device_states,
                    requested_load=100.0,
                )
                assert allowed is True, f"Unexpected block for {room}/{device}: {reason}"
                assert reason is None
