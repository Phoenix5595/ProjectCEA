"""Hardware shutdown proof for relay and dimming outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Final, Protocol

from ..control.runtime_device_snapshot import RuntimeDeviceSnapshot

SAFE_OUTPUT_PROOF_FILENAME: Final = "automation-safe-output.json"
DFR_CHANNELS: Final = (0, 1)


class SafeOutputError(RuntimeError):
    """Raised when an output cannot be proven safe during shutdown."""


class McpSafeOutputDriver(Protocol):
    """The MCP operations required to produce an output safety proof."""

    def all_off(self) -> bool: ...

    def sample_all_channels(self) -> tuple[bool, ...] | None: ...


class DfrSafeOutputManager(Protocol):
    """The DFR operations required to zero every configured output."""

    def list_boards(self) -> Sequence[Mapping[str, int | str | bool]]: ...

    def set_intensity(self, board_id: int, channel: int, intensity: float) -> bool: ...

    def get_intensity(self, board_id: int, channel: int) -> float | None: ...


@dataclass(frozen=True, slots=True)
class DfrOutputProof:
    """Proof of a successful DFR zero command, not analog physical readback."""

    board_id: int
    channel: int
    command_succeeded: bool
    cached_intensity: float | None
    cached_zero: bool


@dataclass(frozen=True, slots=True)
class SafeOutputProof:
    """Typed evidence that relay and DFR commands reached their safe state."""

    created_at: str
    mcp_all_off_command_succeeded: bool
    mcp_logical_states: tuple[bool, ...]
    dfr_outputs: tuple[DfrOutputProof, ...]

    def to_json_bytes(self) -> bytes:
        """Serialize the proof without claiming DFR analog hardware readback."""
        payload = {
            "created_at": self.created_at,
            "mcp": {
                "all_off_command_succeeded": self.mcp_all_off_command_succeeded,
                "logical_states": list(self.mcp_logical_states),
            },
            "dfr_outputs": [asdict(output) for output in self.dfr_outputs],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def force_all_outputs_safe(
    mcp23017: McpSafeOutputDriver,
    dfr0971_manager: DfrSafeOutputManager | None,
    redis_path: str | Path,
) -> SafeOutputProof:
    """Command every configured output off, verify relay GPIO, and persist proof."""
    mcp_states = require_mcp_all_off(mcp23017)

    dfr_outputs = _zero_configured_dfr_outputs(dfr0971_manager)
    failed_outputs = [output for output in dfr_outputs if not output.command_succeeded]
    uncached_outputs = [output for output in dfr_outputs if not output.cached_zero]
    if failed_outputs or uncached_outputs:
        raise SafeOutputError("DFR0971 zero command or cached zero state failed")

    proof = SafeOutputProof(
        created_at=_utc_timestamp(),
        mcp_all_off_command_succeeded=True,
        mcp_logical_states=mcp_states,
        dfr_outputs=tuple(dfr_outputs),
    )
    _write_proof(proof, Path(redis_path))
    return proof


def require_mcp_all_off(mcp23017: McpSafeOutputDriver) -> tuple[bool, ...]:
    """Command all relays OFF and require a complete logical-OFF readback."""
    if not mcp23017.all_off():
        raise SafeOutputError("MCP23017 rejected the all-off command")

    mcp_states = mcp23017.sample_all_channels()
    if mcp_states is None or len(mcp_states) != 16 or any(mcp_states):
        raise SafeOutputError("MCP23017 logical readback is not sixteen OFF states")
    return mcp_states


def zero_unassigned_dfr_outputs(
    dfr0971_manager: DfrSafeOutputManager | None,
    snapshot: RuntimeDeviceSnapshot,
) -> tuple[DfrOutputProof, ...]:
    """Volatile-zero DFR slots absent from the installed strict registry snapshot."""
    if dfr0971_manager is None:
        return ()

    assigned_slots = _assigned_dfr_slots(snapshot)
    proofs: list[DfrOutputProof] = []
    for board in dfr0971_manager.list_boards():
        board_id = board.get("board_id")
        if not isinstance(board_id, int) or isinstance(board_id, bool):
            raise SafeOutputError("DFR0971 configuration contains an invalid board_id")
        for channel in DFR_CHANNELS:
            if (board_id, channel) in assigned_slots:
                continue
            command_succeeded = dfr0971_manager.set_intensity(board_id, channel, 0.0)
            cached_intensity = dfr0971_manager.get_intensity(board_id, channel)
            proofs.append(
                DfrOutputProof(
                    board_id=board_id,
                    channel=channel,
                    command_succeeded=command_succeeded,
                    cached_intensity=cached_intensity,
                    cached_zero=command_succeeded and cached_intensity == 0.0,
                )
            )

    failed_outputs = [output for output in proofs if not output.command_succeeded]
    uncached_outputs = [output for output in proofs if not output.cached_zero]
    if failed_outputs or uncached_outputs:
        raise SafeOutputError("DFR0971 zero command or cached zero state failed")
    return tuple(proofs)


def _assigned_dfr_slots(snapshot: RuntimeDeviceSnapshot) -> set[tuple[int, int]]:
    """Return DFR slots owned by valid dimmable lights in the installed snapshot."""
    slots: set[tuple[int, int]] = set()
    for device in snapshot.device_info.values():
        if not device.get("dimming_enabled") or device.get("dimming_type") != "dfr0971":
            continue
        board_id = device.get("dimming_board_id")
        channel = device.get("dimming_channel")
        if (
            not isinstance(board_id, int)
            or isinstance(board_id, bool)
            or not isinstance(channel, int)
            or isinstance(channel, bool)
        ):
            raise SafeOutputError("Strict registry snapshot contains an invalid DFR assignment")
        slots.add((board_id, channel))
    return slots


def _zero_configured_dfr_outputs(
    dfr0971_manager: DfrSafeOutputManager | None,
) -> list[DfrOutputProof]:
    if dfr0971_manager is None:
        return []

    proofs: list[DfrOutputProof] = []
    for board in dfr0971_manager.list_boards():
        board_id = board.get("board_id")
        if not isinstance(board_id, int) or isinstance(board_id, bool):
            raise SafeOutputError("DFR0971 configuration contains an invalid board_id")
        for channel in DFR_CHANNELS:
            command_succeeded = dfr0971_manager.set_intensity(board_id, channel, 0.0)
            cached_intensity = dfr0971_manager.get_intensity(board_id, channel)
            proofs.append(
                DfrOutputProof(
                    board_id=board_id,
                    channel=channel,
                    command_succeeded=command_succeeded,
                    cached_intensity=cached_intensity,
                    cached_zero=command_succeeded and cached_intensity == 0.0,
                )
            )
    return proofs


def _write_proof(proof: SafeOutputProof, runtime_directory: Path) -> None:
    runtime_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = os.chmod(runtime_directory, 0o700)
    proof_path = runtime_directory / SAFE_OUTPUT_PROOF_FILENAME
    temporary_path = runtime_directory / f".{SAFE_OUTPUT_PROOF_FILENAME}.{os.getpid()}.tmp"
    file_descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(file_descriptor, "wb") as proof_file:
        _ = proof_file.write(proof.to_json_bytes())
        proof_file.flush()
        _ = os.fsync(proof_file.fileno())
    os.replace(temporary_path, proof_path)
    _ = os.chmod(proof_path, 0o600)


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
