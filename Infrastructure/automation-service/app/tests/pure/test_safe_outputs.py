from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.hardware.dfr0971 import DFR0971Driver, DFR0971Manager
from app.hardware.mcp23017 import MCP23017Driver


@dataclass
class _CharacterizationMcp(MCP23017Driver):
    received_states: list[bool] = field(default_factory=list)

    def set_all_channels(self, states: list[bool]) -> bool:
        self.received_states = states
        return True


@dataclass
class _DfrDriver(DFR0971Driver):
    commands: list[tuple[float, int, bool]] = field(default_factory=list)

    def set_intensity(
        self, intensity: float, channel: int = 0, store_to_eeprom: bool = False
    ) -> bool:
        self.commands.append((intensity, channel, store_to_eeprom))
        return True


@dataclass
class _CharacterizationDfrManager(DFR0971Manager):
    driver: DFR0971Driver

    def get_board(self, board_id: int) -> DFR0971Driver:
        return self.driver


def test_mcp_all_off_characterization_requests_sixteen_logical_off_states() -> None:
    # Given: a receiver for the existing MCP23017 all_off delegation
    receiver = _CharacterizationMcp()

    # When: the driver performs its established all-off operation
    success = receiver.all_off()

    # Then: it sends one logical OFF command for all sixteen relay channels
    assert success is True
    assert receiver.received_states == [False] * 16


def test_dfr_manager_set_intensity_characterization_delegates_to_the_selected_board() -> None:
    # Given: a configured DFR manager with one fake board
    driver = _DfrDriver()
    manager = _CharacterizationDfrManager(driver)

    # When: zero intensity is commanded through the manager
    success = manager.set_intensity(8, 1, 0.0)

    # Then: the selected board receives the zero command without EEPROM writes
    assert success is True
    assert driver.commands == [(0.0, 1, False)]


def test_force_all_outputs_safe_writes_complete_logical_proof(tmp_path: Path) -> None:
    from app.hardware.safe_outputs import force_all_outputs_safe

    # Given: six configured DFR outputs and a relay board that reports all OFF
    mcp = _FakeMcp()
    dfr = _FakeDfrManager()

    # When: the shutdown safety helper is invoked
    proof = force_all_outputs_safe(mcp, dfr, tmp_path)

    # Then: every output is commanded safe and the persisted proof is private
    assert mcp.all_off_calls == 1
    assert mcp.sample_calls == 1
    assert proof.mcp_logical_states == (False,) * 16
    assert len(proof.dfr_outputs) == 6
    assert all(output.command_succeeded and output.cached_zero for output in proof.dfr_outputs)
    assert (tmp_path / "automation-safe-output.json").stat().st_mode & 0o777 == 0o600


@dataclass
class _FakeMcp:
    all_off_calls: int = 0
    sample_calls: int = 0

    def all_off(self) -> bool:
        self.all_off_calls += 1
        return True

    def sample_all_channels(self) -> tuple[bool, ...]:
        self.sample_calls += 1
        return (False,) * 16


@dataclass
class _FakeDfrManager:
    commands: list[tuple[int, int, float]] = field(default_factory=list)

    def list_boards(self) -> list[dict[str, int]]:
        return [
            {"board_id": 8},
            {"board_id": 9},
            {"board_id": 10},
        ]

    def set_intensity(self, board_id: int, channel: int, intensity: float) -> bool:
        self.commands.append((board_id, channel, intensity))
        return True

    def get_intensity(self, board_id: int, channel: int) -> float:
        return 0.0
