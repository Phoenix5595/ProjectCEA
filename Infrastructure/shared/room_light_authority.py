"""Room operating modes where scheduled light authority is always MOON (lights off / 0%)."""

from __future__ import annotations

from typing import Final

# Canonical DB/API names from room_modes.name (lowercase)
MOON_AUTHORITY_MODE_NAMES: Final[frozenset[str]] = frozenset({"drying", "sleep"})


def is_moon_authority_mode(mode_name: object | None) -> bool:
    """Return True when *mode_name* forces 24h MOON for automatic/scheduled lighting."""
    if mode_name is None:
        return False
    text = str(mode_name).strip().lower()
    if not text:
        return False
    return text in MOON_AUTHORITY_MODE_NAMES
