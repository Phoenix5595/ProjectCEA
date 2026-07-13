"""PID CLI commands for config_cli."""

from __future__ import annotations

import os
import sys

from app.database import DatabaseManager

PID_RANGES = {
    "heater": {"kp": (0.0, 100.0), "ki": (0.0, 1.0), "kd": (0.0, 10.0)},
    "co2": {"kp": (0.0, 50.0), "ki": (0.0, 0.5), "kd": (0.0, 5.0)},
}


def validate_pid(device_type: str, kp: float, ki: float, kd: float) -> tuple[bool, str | None]:
    """Validate PID parameters.

    Returns:
        (is_valid, error_message)
    """
    if device_type not in PID_RANGES:
        return (
            False,
            f"Unknown device type: {device_type}. Valid types: {', '.join(PID_RANGES.keys())}",
        )

    ranges = PID_RANGES[device_type]

    if kp < ranges["kp"][0] or kp > ranges["kp"][1]:
        return (
            False,
            f"Kp for {device_type} must be between {ranges['kp'][0]} and {ranges['kp'][1]}",
        )
    if ki < ranges["ki"][0] or ki > ranges["ki"][1]:
        return (
            False,
            f"Ki for {device_type} must be between {ranges['ki'][0]} and {ranges['ki'][1]}",
        )
    if kd < ranges["kd"][0] or kd > ranges["kd"][1]:
        return (
            False,
            f"Kd for {device_type} must be between {ranges['kd'][0]} and {ranges['kd'][1]}",
        )

    return True, None


async def cmd_pid_get(
    db: DatabaseManager, device_type: str, location: str = "Flower Room", cluster: str = "main"
):
    """Get PID parameters for a device type."""
    params = await db.pid_repo.get_pid_parameters(location, cluster, device_type)
    if not params:
        print(f"No PID parameters found for {device_type}")
        return

    print(f"PID parameters for {device_type}:")
    print(f"  Kp: {params['kp']}")
    print(f"  Ki: {params['ki']}")
    print(f"  Kd: {params['kd']}")
    if params.get("updated_at"):
        print(f"  Last updated: {params['updated_at']}")


async def cmd_pid_set(
    db: DatabaseManager,
    device_type: str,
    kp: float,
    ki: float,
    kd: float,
    dry_run: bool,
    author: str | None,
    location: str = "Flower Room",
    cluster: str = "main",
):
    """Set PID parameters for a device type."""
    # Validate
    is_valid, error = validate_pid(device_type, kp, ki, kd)
    if not is_valid:
        print(f"Validation error: {error}")
        sys.exit(1)

    # Get existing
    existing = await db.pid_repo.get_pid_parameters(location, cluster, device_type)

    # Show diff
    print("Changes to apply:")
    if existing:
        print(f"  Kp: {existing['kp']} → {kp}")
        print(f"  Ki: {existing['ki']} → {ki}")
        print(f"  Kd: {existing['kd']} → {kd}")
    else:
        print(f"  Kp: (not set) → {kp}")
        print(f"  Ki: (not set) → {ki}")
        print(f"  Kd: (not set) → {kd}")

    if dry_run:
        print("\n[DRY RUN] Changes not applied")
        return

    # Apply
    success = await db.pid_repo.set_pid_parameters(
        location,
        cluster,
        device_type,
        kp,
        ki,
        kd,
        updated_by=author or os.getenv("USER", "unknown"),
        source="cli",
    )

    if not success:
        print("Error: Failed to update PID parameters")
        sys.exit(1)

    # Log to config_versions
    await db.config_repo.log_config_version(
        config_type="pid",
        author=author or os.getenv("USER", "unknown"),
        comment=f"PID parameter update for {device_type}",
        changes={"device_type": device_type, "kp": kp, "ki": ki, "kd": kd},
    )

    print("PID parameters updated successfully")
