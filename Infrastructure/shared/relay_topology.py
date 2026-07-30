"""Canonical physical relay labels for the MCP23017 relay board."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class RelayTopologyEntry:
    """One physical relay's MCP channel and derived GPIO pin label."""

    physical_relay: int
    channel: int

    @property
    def pin_label(self) -> str:
        """Return the MCP23017 GPIO label for this channel."""
        return pin_label_for_channel(self.channel)


RELAY_TOPOLOGY: Final[tuple[RelayTopologyEntry, ...]] = (
    RelayTopologyEntry(1, 15),
    RelayTopologyEntry(2, 0),
    RelayTopologyEntry(3, 14),
    RelayTopologyEntry(4, 1),
    RelayTopologyEntry(5, 13),
    RelayTopologyEntry(6, 2),
    RelayTopologyEntry(7, 12),
    RelayTopologyEntry(8, 3),
    RelayTopologyEntry(9, 11),
    RelayTopologyEntry(10, 4),
    RelayTopologyEntry(11, 10),
    RelayTopologyEntry(12, 5),
    RelayTopologyEntry(13, 9),
    RelayTopologyEntry(14, 6),
    RelayTopologyEntry(15, 8),
    RelayTopologyEntry(16, 7),
)

_BY_CHANNEL: Final[dict[int, RelayTopologyEntry]] = {
    entry.channel: entry for entry in RELAY_TOPOLOGY
}


def entry_for_channel(channel: int) -> RelayTopologyEntry:
    """Return the canonical physical topology entry for an MCP channel."""
    return _BY_CHANNEL[channel]


def physical_relay_for_channel(channel: int) -> int:
    """Return the operator-facing physical relay number for an MCP channel."""
    return entry_for_channel(channel).physical_relay


def pin_label_for_channel(channel: int) -> str:
    """Derive an MCP23017 GPIO pin label directly from its channel number."""
    if channel < 8:
        return f"GPIOA{channel}"
    return f"GPIOB{channel - 8}"


__all__ = [
    "RELAY_TOPOLOGY",
    "RelayTopologyEntry",
    "entry_for_channel",
    "physical_relay_for_channel",
    "pin_label_for_channel",
]
