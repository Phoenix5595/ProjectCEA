from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.hardware.dfr0971 import DFR0971Driver, DFR0971Manager
from app.hardware.mcp23017 import MCP23017Driver
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot


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


def test_zero_unassigned_dfr_outputs_skips_assigned_snapshot_slot() -> None:
    from app.hardware.safe_outputs import zero_unassigned_dfr_outputs

    # Given: one strict-snapshot DFR assignment on a manager with all six slots
    snapshot = RuntimeDeviceSnapshot.create(
        version=1,
        hierarchy={
            "Veg Room": {
                "main": {
                    "light_v_1": {
                        "device_id": 42,
                        "device_type": "light",
                        "dimming_enabled": True,
                        "dimming_type": "dfr0971",
                        "dimming_board_id": 9,
                        "dimming_channel": 0,
                    }
                }
            }
        },
        mode_parameters={},
        light_intensities={},
        light_programs=[],
    )
    dfr = _FakeDfrManager()

    # When: startup clears only unassigned volatile DFR outputs
    proofs = zero_unassigned_dfr_outputs(dfr, snapshot)

    # Then: the assigned slot is untouched and every other configured slot is acknowledged at zero
    assert {(proof.board_id, proof.channel) for proof in proofs} == {
        (8, 0),
        (8, 1),
        (9, 1),
        (10, 0),
        (10, 1),
    }
    assert all(proof.command_succeeded and proof.cached_zero for proof in proofs)
    assert dfr.commands == [
        (8, 0, 0.0),
        (8, 1, 0.0),
        (9, 1, 0.0),
        (10, 0, 0.0),
        (10, 1, 0.0),
    ]


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
