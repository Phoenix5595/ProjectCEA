"""Schedule CLI commands for config_cli (list, create, delete)."""

from __future__ import annotations

import os
import sys

from app.database import DatabaseManager


def validate_mode(mode: str) -> tuple[bool, str | None]:
    """Validate mode value for schedules.

    Returns:
        (is_valid, error_message)
    """
    if mode.upper() not in ["DAY", "NIGHT", "TRANSITION"]:
        return False, f"Invalid mode: {mode}. Valid modes: DAY, NIGHT, TRANSITION"
    return True, None


def validate_time(time_str: str) -> tuple:
    """Validate time string (HH:MM format).

    Returns:
        (is_valid, error_message)
    """
    try:
        parts = time_str.split(":")
        if len(parts) != 2:
            return False, "Time must be in HH:MM format"
        hour = int(parts[0])
        minute = int(parts[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return False, "Hour must be 0-23, minute must be 0-59"
        return True, None
    except ValueError:
        return False, "Time must be in HH:MM format (e.g., 06:00)"


async def check_schedule_conflicts(
    db: DatabaseManager,
    location: str,
    cluster: str,
    start_time: str,
    end_time: str,
    day_of_week: int | list[int] | None,
    mode: str | None,
    exclude_id: int | None = None,
) -> tuple[bool, str | None]:
    """Check for schedule conflicts.

    Returns:
        (has_conflict, error_message)
    """
    schedules = await db.schedule_repo.get_schedules(location, cluster)

    for schedule in schedules:
        if exclude_id and schedule["id"] == exclude_id:
            continue

        if not schedule.get("enabled", True):
            continue

        # Check if modes conflict (if mode-based)
        if mode and schedule.get("mode"):
            if mode != schedule["mode"]:
                continue  # Different modes don't conflict

        # Check day of week
        if day_of_week is not None and schedule.get("day_of_week") is not None:
            if day_of_week != schedule["day_of_week"]:
                continue  # Different days don't conflict
        elif day_of_week is not None or schedule.get("day_of_week") is not None:
            # One is daily, one is specific day - could conflict
            pass

        # Check time overlap
        sched_start = str(schedule["start_time"])
        sched_end = str(schedule["end_time"])

        # Simple overlap check (could be improved)
        if start_time < sched_end and end_time > sched_start:
            return True, f"Conflicts with schedule '{schedule['name']}' (ID: {schedule['id']})"

    return False, None


async def cmd_schedule_list(db: DatabaseManager, location: str | None, cluster: str | None):
    """List schedules."""
    schedules = await db.schedule_repo.get_schedules(location, cluster)

    if not schedules:
        print("No schedules found")
        return

    print(f"Schedules ({len(schedules)} total):")
    for sched in schedules:
        mode_str = f" [{sched.get('mode', 'N/A')}]" if sched.get("mode") else ""
        day_str = (
            f" (day {sched['day_of_week']})" if sched.get("day_of_week") is not None else " (daily)"
        )
        enabled_str = "" if sched.get("enabled", True) else " [DISABLED]"
        intensity_str = ""
        if sched.get("target_intensity") is not None:
            intensity_str = f" @ {sched['target_intensity']}%"
            if sched.get("ramp_up_duration") or sched.get("ramp_down_duration"):
                ramp_parts = []
                if sched.get("ramp_up_duration"):
                    ramp_parts.append(f"↑{sched['ramp_up_duration']}m")
                if sched.get("ramp_down_duration"):
                    ramp_parts.append(f"↓{sched['ramp_down_duration']}m")
                intensity_str += f" ({', '.join(ramp_parts)})"
        print(f"  ID {sched['id']}: {sched['name']}{mode_str}{day_str}{enabled_str}{intensity_str}")
        print(f"    {sched['location']}/{sched['cluster']}/{sched['device_name']}")
        print(f"    {sched['start_time']} - {sched['end_time']}")


async def cmd_schedule_create(
    db: DatabaseManager,
    name: str,
    location: str,
    cluster: str,
    device_name: str,
    start_time: str,
    end_time: str,
    mode: str | None,
    day_of_week: int | list[int] | None,
    enabled: bool,
    target_intensity: float | None,
    ramp_up_duration: int | None,
    ramp_down_duration: int | None,
    dry_run: bool,
    author: str | None,
):
    """Create a new schedule."""
    # Validate times
    is_valid, error = validate_time(start_time)
    if not is_valid:
        print(f"Invalid start_time: {error}")
        sys.exit(1)

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

    # Check conflicts
    has_conflict, conflict_msg = await check_schedule_conflicts(
        db, location, cluster, start_time, end_time, day_of_week, mode
    )
    if has_conflict:
        print(f"Schedule conflict: {conflict_msg}")
        sys.exit(1)

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

    print("Creating schedule:")
    print(f"  Name: {name}")
    print(f"  Location/Cluster: {location}/{cluster}")
    print(f"  Device: {device_name}")
    print(f"  Time: {start_time} - {end_time}")
    if mode:
        print(f"  Mode: {mode}")
    if day_of_week is not None:
        print(f"  Day of week: {day_of_week}")
    print(f"  Enabled: {enabled}")
    if target_intensity is not None:
        print(f"  Target intensity: {target_intensity}%")
    if ramp_up_duration is not None:
        print(f"  Ramp up duration: {ramp_up_duration} minutes")
    if ramp_down_duration is not None:
        print(f"  Ramp down duration: {ramp_down_duration} minutes")

    if dry_run:
        print("\n[DRY RUN] Schedule not created")
        return

    # Create
    final_day_of_week = [day_of_week] if isinstance(day_of_week, int) else day_of_week
    schedule_id = await db.schedule_repo.create_schedule(
        name,
        location,
        cluster,
        device_name,
        start_time,
        end_time,
        final_day_of_week,  # type: ignore
        enabled,
        mode or "light",  # type: ignore
        target_intensity,
        ramp_up_duration,
        ramp_down_duration,
    )

    if not schedule_id:
        print("Error: Failed to create schedule")
        sys.exit(1)

    # Log to config_versions
    await db.config_repo.log_config_version(
        config_type="schedule",
        author=author or os.getenv("USER", "unknown"),
        comment=f"Created schedule: {name}",
        location=location,
        cluster=cluster,
        changes={"schedule_id": schedule_id, "name": name, "mode": mode},
    )

    print(f"Schedule created with ID: {schedule_id}")


async def cmd_schedule_delete(
    db: DatabaseManager, schedule_id: int, dry_run: bool, author: str | None
):
    """Delete a schedule."""
    # Get existing schedule
    schedules = await db.schedule_repo.get_schedules()
    existing = next((s for s in schedules if s["id"] == schedule_id), None)
    if not existing:
        print(f"Schedule {schedule_id} not found")
        sys.exit(1)

    print("Deleting schedule:")
    print(f"  ID: {schedule_id}")
    print(f"  Name: {existing['name']}")
    print(f"  Location/Cluster: {existing['location']}/{existing['cluster']}")

    if dry_run:
        print("\n[DRY RUN] Schedule not deleted")
        return

    # Delete
    success = await db.schedule_repo.delete_schedule(schedule_id)

    if not success:
        print("Error: Failed to delete schedule")
        sys.exit(1)

    # Log to config_versions
    await db.config_repo.log_config_version(
        config_type="schedule",
        author=author or os.getenv("USER", "unknown"),
        comment=f"Deleted schedule: {existing['name']}",
        location=existing["location"],
        cluster=existing["cluster"],
        changes={"schedule_id": schedule_id, "action": "deleted"},
    )

    print("Schedule deleted successfully")
