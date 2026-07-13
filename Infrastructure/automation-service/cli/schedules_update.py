"""Schedule update CLI command for config_cli."""

from __future__ import annotations

import os
import sys

from app.database import DatabaseManager
from cli.schedules import check_schedule_conflicts, validate_mode, validate_time


async def cmd_schedule_update(
    db: DatabaseManager,
    schedule_id: int,
    name: str | None,
    start_time: str | None,
    end_time: str | None,
    mode: str | None,
    day_of_week: int | list[int] | None,
    enabled: bool | None,
    target_intensity: float | None,
    ramp_up_duration: int | None,
    ramp_down_duration: int | None,
    dry_run: bool,
    author: str | None,
):
    """Update a schedule."""
    # Get existing schedule
    schedules = await db.schedule_repo.get_schedules()
    existing = next((s for s in schedules if s["id"] == schedule_id), None)
    if not existing:
        print(f"Schedule {schedule_id} not found")
        sys.exit(1)

    # Validate times if provided
    if start_time:
        is_valid, error = validate_time(start_time)
        if not is_valid:
            print(f"Invalid start_time: {error}")
            sys.exit(1)

    if end_time:
        is_valid, error = validate_time(end_time)
        if not is_valid:
            print(f"Invalid end_time: {error}")
            sys.exit(1)

    # Validate mode if provided
    if mode:
        is_valid, error = validate_mode(mode)
        if not is_valid:
            print(f"Invalid mode: {error}")
            sys.exit(1)
        mode = mode.upper()

    # Check conflicts if time or mode changed
    if start_time or end_time or mode:
        final_start = start_time or str(existing["start_time"])
        final_end = end_time or str(existing["end_time"])
        final_mode = mode or existing.get("mode")
        final_day = day_of_week if day_of_week is not None else existing.get("day_of_week")

        has_conflict, conflict_msg = await check_schedule_conflicts(
            db,
            existing["location"],
            existing["cluster"],
            final_start,
            final_end,
            final_day,
            final_mode,
            exclude_id=schedule_id,
        )
        if has_conflict:
            print(f"Schedule conflict: {conflict_msg}")
            sys.exit(1)

    # Show changes
    changes = {}
    print("Changes to apply:")
    if name and name != existing["name"]:
        print(f"  name: {existing['name']} → {name}")
        changes["name"] = name
    if start_time and str(start_time) != str(existing["start_time"]):
        print(f"  start_time: {existing['start_time']} → {start_time}")
        changes["start_time"] = start_time
    if end_time and str(end_time) != str(existing["end_time"]):
        print(f"  end_time: {existing['end_time']} → {end_time}")
        changes["end_time"] = end_time
    if mode and mode != existing.get("mode"):
        print(f"  mode: {existing.get('mode', 'N/A')} → {mode}")
        changes["mode"] = mode
    if day_of_week is not None and day_of_week != existing.get("day_of_week"):
        print(f"  day_of_week: {existing.get('day_of_week', 'N/A')} → {day_of_week}")
        changes["day_of_week"] = day_of_week
    if enabled is not None and enabled != existing.get("enabled", True):
        print(f"  enabled: {existing.get('enabled', True)} → {enabled}")
        changes["enabled"] = enabled
    if target_intensity is not None and target_intensity != existing.get("target_intensity"):
        print(
            f"  target_intensity: {existing.get('target_intensity', 'N/A')} → {target_intensity}%"
        )
        changes["target_intensity"] = target_intensity
    if ramp_up_duration is not None and ramp_up_duration != existing.get("ramp_up_duration"):
        print(
            f"  ramp_up_duration: {existing.get('ramp_up_duration', 'N/A')} → {ramp_up_duration} minutes"
        )
        changes["ramp_up_duration"] = ramp_up_duration
    if ramp_down_duration is not None and ramp_down_duration != existing.get("ramp_down_duration"):
        print(
            f"  ramp_down_duration: {existing.get('ramp_down_duration', 'N/A')} → {ramp_down_duration} minutes"
        )
        changes["ramp_down_duration"] = ramp_down_duration

    # Validate ramp parameters if provided
    if target_intensity is not None:
        if target_intensity < 0 or target_intensity > 100:
            print("Error: target_intensity must be between 0 and 100")
            sys.exit(1)
    if ramp_up_duration is not None and ramp_up_duration < 0:
        print("Error: ramp_up_duration must be >= 0")
        sys.exit(1)
    if ramp_down_duration is not None and ramp_down_duration < 0:
        print("Error: ramp_down_duration must be >= 0")
        sys.exit(1)

    if not changes:
        print("No changes specified")
        return

    if dry_run:
        print("\n[DRY RUN] Changes not applied")
        return

    # Apply
    final_day_of_week = [day_of_week] if isinstance(day_of_week, int) else day_of_week
    success = await db.schedule_repo.update_schedule(
        schedule_id,
        name=name,
        start_time=start_time,
        end_time=end_time,
        day_of_week=final_day_of_week,
        enabled=enabled,
        mode=mode,
        target_intensity=target_intensity,
        ramp_up_duration=ramp_up_duration,
        ramp_down_duration=ramp_down_duration,
    )

    if not success:
        print("Error: Failed to update schedule")
        sys.exit(1)

    # Log to config_versions
    await db.config_repo.log_config_version(
        config_type="schedule",
        author=author or os.getenv("USER", "unknown"),
        comment=f"Updated schedule ID {schedule_id}",
        location=existing["location"],
        cluster=existing["cluster"],
        changes=changes,
    )

    print("Schedule updated successfully")
