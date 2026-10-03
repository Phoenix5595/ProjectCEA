"""Room-mode restrictions enforced at API and persistence boundaries."""

from __future__ import annotations


def validate_room_mode_choice(
    location: str, mode_name: str, submode_name: str | None = None
) -> None:
    """Keep Veg Room in Veg mode, without Flower submodes."""
    if location == "Veg Room" and (mode_name != "veg" or submode_name):
        raise ValueError("Veg Room only supports Veg mode")
