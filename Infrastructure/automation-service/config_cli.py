#!/usr/bin/env python3
"""CLI tool for managing automation service configuration in PostgreSQL.

This tool provides a safe, validated interface for editing runtime configuration
(setpoints, schedules, PID parameters, safety limits) stored in PostgreSQL.

The CLI never writes to Redis or interacts with actuators - it only modifies
the PostgreSQL Config Store. All changes are logged to config_versions table
for audit trail.

Usage:
    config-cli setpoint get <location> <cluster>
    config-cli setpoint set <location> <cluster> --temp <value> --humidity <value> --co2 <value> [--dry-run]
    config-cli pid get <device_type>
    config-cli pid set <device_type> --kp <value> --ki <value> --kd <value> [--dry-run]
    config-cli schedule list [--location <loc>] [--cluster <clust>]
    config-cli schedule create <name> <location> <cluster> <device> <start> <end> [--mode <mode>] [--dry-run]
    config-cli schedule update <id> [--name <name>] [--start <time>] [--end <time>] [--mode <mode>] [--enabled <bool>] [--dry-run]
    config-cli schedule delete <id> [--dry-run]
    config-cli config show <location> <cluster>
"""
import asyncio
import argparse
import sys
import os
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import DatabaseManager


# Validation ranges
SETPOINT_RANGES = {
    'temperature': (10.0, 35.0),  # °C
    'humidity': (30.0, 90.0),      # %
    'co2': (400.0, 2000.0),        # ppm
    'vpd': (0.0, 5.0)              # kPa
}

PID_RANGES = {
    'heater': {
        'kp': (0.0, 100.0),
        'ki': (0.0, 1.0),
        'kd': (0.0, 10.0)
    },
    'co2': {
        'kp': (0.0, 50.0),
        'ki': (0.0, 0.5),
        'kd': (0.0, 5.0)
    }
}

VALID_MODES = ['DAY', 'NIGHT', 'TRANSITION']


def validate_setpoint(name: str, value: float) -> tuple:
    """Validate a setpoint value.
    
    Returns:
        (is_valid, error_message)
    """
    if name not in SETPOINT_RANGES:
        return False, f"Unknown setpoint type: {name}"
    
    min_val, max_val = SETPOINT_RANGES[name]
    if value < min_val or value > max_val:
        return False, f"{name} must be between {min_val} and {max_val}"
    
    return True, None


def validate_pid(device_type: str, kp: float, ki: float, kd: float) -> tuple:
    """Validate PID parameters.
    
    Returns:
        (is_valid, error_message)
    """
    if device_type not in PID_RANGES:
        return False, f"Unknown device type: {device_type}. Valid types: {', '.join(PID_RANGES.keys())}"
    
    ranges = PID_RANGES[device_type]
    
    if kp < ranges['kp'][0] or kp > ranges['kp'][1]:
        return False, f"Kp for {device_type} must be between {ranges['kp'][0]} and {ranges['kp'][1]}"
    if ki < ranges['ki'][0] or ki > ranges['ki'][1]:
        return False, f"Ki for {device_type} must be between {ranges['ki'][0]} and {ranges['ki'][1]}"
    if kd < ranges['kd'][0] or kd > ranges['kd'][1]:
        return False, f"Kd for {device_type} must be between {ranges['kd'][0]} and {ranges['kd'][1]}"
    
    return True, None


def validate_mode(mode: str) -> tuple:
    """Validate mode value.
    
    Returns:
        (is_valid, error_message)
    """
    if mode.upper() not in VALID_MODES:
        return False, f"Invalid mode: {mode}. Valid modes: {', '.join(VALID_MODES)}"
    return True, None


def validate_time(time_str: str) -> tuple:
    """Validate time string (HH:MM format).
    
    Returns:
        (is_valid, error_message)
    """
    try:
        parts = time_str.split(':')
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
    day_of_week: Optional[int],
    mode: Optional[str],
    exclude_id: Optional[int] = None
) -> tuple:
    """Check for schedule conflicts.
    
    Returns:
        (has_conflict, error_message)
    """
    schedules = await db.get_schedules(location, cluster)
    
    for schedule in schedules:
        if exclude_id and schedule['id'] == exclude_id:
            continue
        
        if not schedule.get('enabled', True):
            continue
        
        # Check if modes conflict (if mode-based)
        if mode and schedule.get('mode'):
            if mode != schedule['mode']:
                continue  # Different modes don't conflict
        
        # Check day of week
        if day_of_week is not None and schedule.get('day_of_week') is not None:
            if day_of_week != schedule['day_of_week']:
                continue  # Different days don't conflict
        elif day_of_week is not None or schedule.get('day_of_week') is not None:
            # One is daily, one is specific day - could conflict
            pass
        
        # Check time overlap
        sched_start = str(schedule['start_time'])
        sched_end = str(schedule['end_time'])
        
        # Simple overlap check (could be improved)
        if (start_time < sched_end and end_time > sched_start):
            return True, f"Conflicts with schedule '{schedule['name']}' (ID: {schedule['id']})"
    
    return False, None


async def cmd_setpoint_get(db: DatabaseManager, location: str, cluster: str, mode: Optional[str] = None):
    """Get setpoints for a location/cluster.
    
    Args:
        db: Database manager
        location: Location name
        cluster: Cluster name
        mode: Optional mode (DAY/NIGHT/TRANSITION). If None, shows default/legacy setpoint.
    """
    if mode:
        # Get specific mode setpoint
        setpoint = await db.get_setpoint(location, cluster, mode)
    if not setpoint:
            print(f"No setpoints found for {location}/{cluster} (mode: {mode})")
            return
        
        print(f"Setpoints for {location}/{cluster} (mode: {mode}):")
    else:
        # Get default/legacy setpoint (mode=NULL)
        setpoint = await db.get_setpoint(location, cluster, None)
    if not setpoint:
            # Try to get all modes
            all_setpoints = await db.get_all_setpoints_for_location_cluster(location, cluster)
            if all_setpoints:
                print(f"Setpoints for {location}/{cluster} (all modes):")
                for sp in all_setpoints:
                    mode_str = sp.get('mode') or 'default'
                    print(f"\n  Mode: {mode_str}")
                    if sp.get('temperature') is not None:
                        print(f"    Temperature: {sp['temperature']}°C")
                    if sp.get('humidity') is not None:
                        print(f"    Humidity: {sp['humidity']}%")
                    if sp.get('co2') is not None:
                        print(f"    CO2: {sp['co2']} ppm")
                    if sp.get('vpd') is not None:
                        print(f"    VPD: {sp['vpd']} kPa")
                return
            else:
        print(f"No setpoints found for {location}/{cluster}")
        return
    
    print(f"Setpoints for {location}/{cluster}:")
    
    if setpoint.get('temperature') is not None:
        print(f"  Temperature: {setpoint['temperature']}°C")
    if setpoint.get('humidity') is not None:
        print(f"  Humidity: {setpoint['humidity']}%")
    if setpoint.get('co2') is not None:
        print(f"  CO2: {setpoint['co2']} ppm")
    if setpoint.get('vpd') is not None:
        print(f"  VPD: {setpoint['vpd']} kPa")
    if setpoint.get('mode') is not None:
        print(f"  Mode: {setpoint['mode']}")


async def cmd_setpoint_set(
    db: DatabaseManager,
    location: str,
    cluster: str,
    temperature: Optional[float],
    humidity: Optional[float],
    co2: Optional[float],
    vpd: Optional[float],
    mode: Optional[str],
    dry_run: bool,
    author: Optional[str]
):
    """Set setpoints for a location/cluster.
    
    Args:
        db: Database manager
        location: Location name
        cluster: Cluster name
        temperature: Temperature setpoint (optional)
        humidity: Humidity setpoint (optional)
        co2: CO2 setpoint (optional)
        vpd: VPD setpoint (optional)
        mode: Mode (DAY/NIGHT/TRANSITION) or None for legacy/default setpoint
        dry_run: If True, validate but don't apply changes
        author: Author name for config version logging
    """
    # Validate mode if provided
    if mode:
        is_valid, error = validate_mode(mode)
        if not is_valid:
            print(f"Validation error: {error}")
            sys.exit(1)
        mode = mode.upper()
    
    # Get existing values for this mode (or mode=NULL for legacy)
    existing = await db.get_setpoint(location, cluster, mode)
    
    # Validate new values
    changes = {}
    errors = []
    
    if temperature is not None:
        is_valid, error = validate_setpoint('temperature', temperature)
        if not is_valid:
            errors.append(error)
        else:
            changes['temperature'] = temperature
    
    if humidity is not None:
        is_valid, error = validate_setpoint('humidity', humidity)
        if not is_valid:
            errors.append(error)
        else:
            changes['humidity'] = humidity
    
    if co2 is not None:
        is_valid, error = validate_setpoint('co2', co2)
        if not is_valid:
            errors.append(error)
        else:
            changes['co2'] = co2
    
    if vpd is not None:
        is_valid, error = validate_setpoint('vpd', vpd)
        if not is_valid:
            errors.append(error)
        else:
            changes['vpd'] = vpd
    
    if mode is not None:
        changes['mode'] = mode
    
    if errors:
        print("Validation errors:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    
    if not changes:
        print("No changes specified")
        return
    
    # Show diff
    mode_str = f" (mode: {mode})" if mode else ""
    print(f"Changes to apply for {location}/{cluster}{mode_str}:")
    for key, value in changes.items():
        if key == 'mode':
            old_mode = existing.get('mode') if existing else None
            if old_mode is not None:
                print(f"  {key}: {old_mode} → {value}")
            else:
                print(f"  {key}: (not set) → {value}")
        else:
        old_val = existing.get(key) if existing else None
        if old_val is not None:
            print(f"  {key}: {old_val} → {value}")
        else:
            print(f"  {key}: (not set) → {value}")
    
    if dry_run:
        print("\n[DRY RUN] Changes not applied")
        return
    
    # Apply changes
    success = await db.set_setpoint(
        location, cluster,
        changes.get('temperature'),
        changes.get('humidity'),
        changes.get('co2'),
        changes.get('vpd'),
        mode,
        source='cli'
    )
    
    if not success:
        print("Error: Failed to update setpoints")
        sys.exit(1)
    
    # Log to config_versions
    await db.log_config_version(
        config_type='setpoint',
        author=author or os.getenv('USER', 'unknown'),
        comment=f"Setpoint update for {location}/{cluster}{mode_str}",
        location=location,
        cluster=cluster,
        changes=changes
    )
    
    print("Setpoints updated successfully")


async def cmd_pid_get(db: DatabaseManager, device_type: str):
    """Get PID parameters for a device type."""
    params = await db.get_pid_parameters(device_type)
    if not params:
        print(f"No PID parameters found for {device_type}")
        return
    
    print(f"PID parameters for {device_type}:")
    print(f"  Kp: {params['kp']}")
    print(f"  Ki: {params['ki']}")
    print(f"  Kd: {params['kd']}")
    if params.get('updated_at'):
        print(f"  Last updated: {params['updated_at']}")


async def cmd_pid_set(
    db: DatabaseManager,
    device_type: str,
    kp: float,
    ki: float,
    kd: float,
    dry_run: bool,
    author: Optional[str]
):
    """Set PID parameters for a device type."""
    # Validate
    is_valid, error = validate_pid(device_type, kp, ki, kd)
    if not is_valid:
        print(f"Validation error: {error}")
        sys.exit(1)
    
    # Get existing
    existing = await db.get_pid_parameters(device_type)
    
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
    success = await db.set_pid_parameters(
        device_type, kp, ki, kd,
        updated_by=author or os.getenv('USER', 'unknown'),
        source='cli'
    )
    
    if not success:
        print("Error: Failed to update PID parameters")
        sys.exit(1)
    
    # Log to config_versions
    await db.log_config_version(
        config_type='pid',
        author=author or os.getenv('USER', 'unknown'),
        comment=f"PID parameter update for {device_type}",
        changes={'device_type': device_type, 'kp': kp, 'ki': ki, 'kd': kd}
    )
    
    print("PID parameters updated successfully")


async def cmd_schedule_list(db: DatabaseManager, location: Optional[str], cluster: Optional[str]):
    """List schedules."""
    schedules = await db.get_schedules(location, cluster)
    
    if not schedules:
        print("No schedules found")
        return
    
    print(f"Schedules ({len(schedules)} total):")
    for sched in schedules:
        mode_str = f" [{sched.get('mode', 'N/A')}]" if sched.get('mode') else ""
        day_str = f" (day {sched['day_of_week']})" if sched.get('day_of_week') is not None else " (daily)"
        enabled_str = "" if sched.get('enabled', True) else " [DISABLED]"
        intensity_str = ""
        if sched.get('target_intensity') is not None:
            intensity_str = f" @ {sched['target_intensity']}%"
            if sched.get('ramp_up_duration') or sched.get('ramp_down_duration'):
                ramp_parts = []
                if sched.get('ramp_up_duration'):
                    ramp_parts.append(f"↑{sched['ramp_up_duration']}m")
                if sched.get('ramp_down_duration'):
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
    mode: Optional[str],
    day_of_week: Optional[int],
    enabled: bool,
    target_intensity: Optional[float],
    ramp_up_duration: Optional[int],
    ramp_down_duration: Optional[int],
    dry_run: bool,
    author: Optional[str]
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
            print(f"Error: target_intensity must be between 0 and 100")
            sys.exit(1)
    if ramp_up_duration is not None and ramp_up_duration < 0:
        print(f"Error: ramp_up_duration must be >= 0")
        sys.exit(1)
    if ramp_down_duration is not None and ramp_down_duration < 0:
        print(f"Error: ramp_down_duration must be >= 0")
        sys.exit(1)
    
    print(f"Creating schedule:")
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
    schedule_id = await db.create_schedule(
        name, location, cluster, device_name,
        start_time, end_time, day_of_week, enabled, mode,
        target_intensity, ramp_up_duration, ramp_down_duration
    )
    
    if not schedule_id:
        print("Error: Failed to create schedule")
        sys.exit(1)
    
    # Log to config_versions
    await db.log_config_version(
        config_type='schedule',
        author=author or os.getenv('USER', 'unknown'),
        comment=f"Created schedule: {name}",
        location=location,
        cluster=cluster,
        changes={'schedule_id': schedule_id, 'name': name, 'mode': mode}
    )
    
    print(f"Schedule created with ID: {schedule_id}")


async def cmd_schedule_update(
    db: DatabaseManager,
    schedule_id: int,
    name: Optional[str],
    start_time: Optional[str],
    end_time: Optional[str],
    mode: Optional[str],
    day_of_week: Optional[int],
    enabled: Optional[bool],
    target_intensity: Optional[float],
    ramp_up_duration: Optional[int],
    ramp_down_duration: Optional[int],
    dry_run: bool,
    author: Optional[str]
):
    """Update a schedule."""
    # Get existing schedule
    schedules = await db.get_schedules()
    existing = next((s for s in schedules if s['id'] == schedule_id), None)
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
        final_start = start_time or str(existing['start_time'])
        final_end = end_time or str(existing['end_time'])
        final_mode = mode or existing.get('mode')
        final_day = day_of_week if day_of_week is not None else existing.get('day_of_week')
        
        has_conflict, conflict_msg = await check_schedule_conflicts(
            db, existing['location'], existing['cluster'],
            final_start, final_end, final_day, final_mode,
            exclude_id=schedule_id
        )
        if has_conflict:
            print(f"Schedule conflict: {conflict_msg}")
            sys.exit(1)
    
    # Show changes
    changes = {}
    print("Changes to apply:")
    if name and name != existing['name']:
        print(f"  name: {existing['name']} → {name}")
        changes['name'] = name
    if start_time and str(start_time) != str(existing['start_time']):
        print(f"  start_time: {existing['start_time']} → {start_time}")
        changes['start_time'] = start_time
    if end_time and str(end_time) != str(existing['end_time']):
        print(f"  end_time: {existing['end_time']} → {end_time}")
        changes['end_time'] = end_time
    if mode and mode != existing.get('mode'):
        print(f"  mode: {existing.get('mode', 'N/A')} → {mode}")
        changes['mode'] = mode
    if day_of_week is not None and day_of_week != existing.get('day_of_week'):
        print(f"  day_of_week: {existing.get('day_of_week', 'N/A')} → {day_of_week}")
        changes['day_of_week'] = day_of_week
    if enabled is not None and enabled != existing.get('enabled', True):
        print(f"  enabled: {existing.get('enabled', True)} → {enabled}")
        changes['enabled'] = enabled
    if target_intensity is not None and target_intensity != existing.get('target_intensity'):
        print(f"  target_intensity: {existing.get('target_intensity', 'N/A')} → {target_intensity}%")
        changes['target_intensity'] = target_intensity
    if ramp_up_duration is not None and ramp_up_duration != existing.get('ramp_up_duration'):
        print(f"  ramp_up_duration: {existing.get('ramp_up_duration', 'N/A')} → {ramp_up_duration} minutes")
        changes['ramp_up_duration'] = ramp_up_duration
    if ramp_down_duration is not None and ramp_down_duration != existing.get('ramp_down_duration'):
        print(f"  ramp_down_duration: {existing.get('ramp_down_duration', 'N/A')} → {ramp_down_duration} minutes")
        changes['ramp_down_duration'] = ramp_down_duration
    
    # Validate ramp parameters if provided
    if target_intensity is not None:
        if target_intensity < 0 or target_intensity > 100:
            print(f"Error: target_intensity must be between 0 and 100")
            sys.exit(1)
    if ramp_up_duration is not None and ramp_up_duration < 0:
        print(f"Error: ramp_up_duration must be >= 0")
        sys.exit(1)
    if ramp_down_duration is not None and ramp_down_duration < 0:
        print(f"Error: ramp_down_duration must be >= 0")
        sys.exit(1)
    
    if not changes:
        print("No changes specified")
        return
    
    if dry_run:
        print("\n[DRY RUN] Changes not applied")
        return
    
    # Apply
    success = await db.update_schedule(
        schedule_id, name, start_time, end_time, day_of_week, enabled, mode,
        target_intensity, ramp_up_duration, ramp_down_duration
    )
    
    if not success:
        print("Error: Failed to update schedule")
        sys.exit(1)
    
    # Log to config_versions
    await db.log_config_version(
        config_type='schedule',
        author=author or os.getenv('USER', 'unknown'),
        comment=f"Updated schedule ID {schedule_id}",
        location=existing['location'],
        cluster=existing['cluster'],
        changes=changes
    )
    
    print("Schedule updated successfully")


async def cmd_schedule_delete(db: DatabaseManager, schedule_id: int, dry_run: bool, author: Optional[str]):
    """Delete a schedule."""
    # Get existing schedule
    schedules = await db.get_schedules()
    existing = next((s for s in schedules if s['id'] == schedule_id), None)
    if not existing:
        print(f"Schedule {schedule_id} not found")
        sys.exit(1)
    
    print(f"Deleting schedule:")
    print(f"  ID: {schedule_id}")
    print(f"  Name: {existing['name']}")
    print(f"  Location/Cluster: {existing['location']}/{existing['cluster']}")
    
    if dry_run:
        print("\n[DRY RUN] Schedule not deleted")
        return
    
    # Delete
    success = await db.delete_schedule(schedule_id)
    
    if not success:
        print("Error: Failed to delete schedule")
        sys.exit(1)
    
    # Log to config_versions
    await db.log_config_version(
        config_type='schedule',
        author=author or os.getenv('USER', 'unknown'),
        comment=f"Deleted schedule: {existing['name']}",
        location=existing['location'],
        cluster=existing['cluster'],
        changes={'schedule_id': schedule_id, 'action': 'deleted'}
    )
    
    print("Schedule deleted successfully")


async def cmd_config_show(db: DatabaseManager, location: str, cluster: str):
    """Show effective configuration for a zone."""
    print(f"Effective configuration for {location}/{cluster}:")
    print()
    
    # Get setpoints (show all modes)
    all_setpoints = await db.get_all_setpoints_for_location_cluster(location, cluster)
    print("Setpoints:")
    if all_setpoints:
        for sp in all_setpoints:
            mode_str = sp.get('mode') or 'default'
            print(f"  Mode: {mode_str}")
            if sp.get('temperature') is not None:
                print(f"    Temperature: {sp['temperature']}°C")
            if sp.get('humidity') is not None:
                print(f"    Humidity: {sp['humidity']}%")
            if sp.get('co2') is not None:
                print(f"    CO2: {sp['co2']} ppm")
            if sp.get('vpd') is not None:
                print(f"    VPD: {sp['vpd']} kPa")
    else:
        print("  (not set)")
    print()
    
    # Get schedules
    schedules = await db.get_schedules(location, cluster)
    print(f"Schedules ({len(schedules)} total):")
    for sched in schedules:
        if sched.get('enabled', True):
            mode_str = f" [{sched.get('mode', 'N/A')}]" if sched.get('mode') else ""
            intensity_str = ""
            if sched.get('target_intensity') is not None:
                intensity_str = f" @ {sched['target_intensity']}%"
                if sched.get('ramp_up_duration') or sched.get('ramp_down_duration'):
                    ramp_parts = []
                    if sched.get('ramp_up_duration'):
                        ramp_parts.append(f"↑{sched['ramp_up_duration']}m")
                    if sched.get('ramp_down_duration'):
                        ramp_parts.append(f"↓{sched['ramp_down_duration']}m")
                    intensity_str += f" ({', '.join(ramp_parts)})"
            print(f"  - {sched['name']}{mode_str}: {sched['start_time']} - {sched['end_time']}{intensity_str}")
    print()
    
    # Get PID parameters (would need device types from config, simplified here)
    print("PID Parameters:")
    all_pid = await db.get_all_pid_parameters()
    for device_type, params in all_pid.items():
        print(f"  {device_type}: Kp={params['kp']}, Ki={params['ki']}, Kd={params['kd']}")


async def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='CLI tool for managing automation service configuration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument('--author', help='Author name for config version logging')
    parser.add_argument('--dry-run', action='store_true', help='Validate but do not apply changes')
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Setpoint commands
    sp_parser = subparsers.add_parser('setpoint', help='Manage setpoints')
    sp_subparsers = sp_parser.add_subparsers(dest='setpoint_cmd')
    
    sp_get = sp_subparsers.add_parser('get', help='Get setpoints')
    sp_get.add_argument('location', help='Location name')
    sp_get.add_argument('cluster', help='Cluster name')
    sp_get.add_argument('--mode', help='Mode (DAY/NIGHT/TRANSITION). If not specified, shows default/legacy setpoint or all modes.')
    
    sp_set = sp_subparsers.add_parser('set', help='Set setpoints')
    sp_set.add_argument('location', help='Location name')
    sp_set.add_argument('cluster', help='Cluster name')
    sp_set.add_argument('--temp', type=float, help='Temperature setpoint (°C)')
    sp_set.add_argument('--humidity', type=float, help='Humidity setpoint (percent)')
    sp_set.add_argument('--co2', type=float, help='CO2 setpoint (ppm)')
    sp_set.add_argument('--vpd', type=float, help='VPD setpoint (kPa)')
    sp_set.add_argument('--mode', help='Mode (DAY/NIGHT/TRANSITION). If not specified, uses default/legacy setpoint (mode=NULL).')
    
    # PID commands
    pid_parser = subparsers.add_parser('pid', help='Manage PID parameters')
    pid_subparsers = pid_parser.add_subparsers(dest='pid_cmd')
    
    pid_get = pid_subparsers.add_parser('get', help='Get PID parameters')
    pid_get.add_argument('device_type', help='Device type (heater, co2, etc.)')
    
    pid_set = pid_subparsers.add_parser('set', help='Set PID parameters')
    pid_set.add_argument('device_type', help='Device type (heater, co2, etc.)')
    pid_set.add_argument('--kp', type=float, required=True, help='Proportional gain')
    pid_set.add_argument('--ki', type=float, required=True, help='Integral gain')
    pid_set.add_argument('--kd', type=float, required=True, help='Derivative gain')
    
    # Schedule commands
    sched_parser = subparsers.add_parser('schedule', help='Manage schedules')
    sched_subparsers = sched_parser.add_subparsers(dest='schedule_cmd')
    
    sched_list = sched_subparsers.add_parser('list', help='List schedules')
    sched_list.add_argument('--location', help='Filter by location')
    sched_list.add_argument('--cluster', help='Filter by cluster')
    
    sched_create = sched_subparsers.add_parser('create', help='Create schedule')
    sched_create.add_argument('name', help='Schedule name')
    sched_create.add_argument('location', help='Location name')
    sched_create.add_argument('cluster', help='Cluster name')
    sched_create.add_argument('device', help='Device name')
    sched_create.add_argument('start', help='Start time (HH:MM)')
    sched_create.add_argument('end', help='End time (HH:MM)')
    sched_create.add_argument('--mode', help='Mode (DAY, NIGHT, TRANSITION)')
    sched_create.add_argument('--day-of-week', type=int, help='Day of week (0-6, None for daily)')
    sched_create.add_argument('--enabled', action='store_true', default=True, help='Enable schedule')
    sched_create.add_argument('--disabled', action='store_true', help='Disable schedule')
    sched_create.add_argument('--target-intensity', type=float, help='Target light intensity (0-100%) for ramp schedules')
    sched_create.add_argument('--ramp-up-duration', type=int, help='Ramp up duration in minutes (0 = instant)')
    sched_create.add_argument('--ramp-down-duration', type=int, help='Ramp down duration in minutes (0 = instant)')
    
    sched_update = sched_subparsers.add_parser('update', help='Update schedule')
    sched_update.add_argument('id', type=int, help='Schedule ID')
    sched_update.add_argument('--name', help='New name')
    sched_update.add_argument('--start', help='New start time (HH:MM)')
    sched_update.add_argument('--end', help='New end time (HH:MM)')
    sched_update.add_argument('--mode', help='New mode (DAY, NIGHT, TRANSITION)')
    sched_update.add_argument('--day-of-week', type=int, help='New day of week (0-6)')
    sched_update.add_argument('--enabled', action='store_true', help='Enable schedule')
    sched_update.add_argument('--disabled', action='store_true', help='Disable schedule')
    sched_update.add_argument('--target-intensity', type=float, help='New target light intensity (0-100%)')
    sched_update.add_argument('--ramp-up-duration', type=int, help='New ramp up duration in minutes (0 = instant)')
    sched_update.add_argument('--ramp-down-duration', type=int, help='New ramp down duration in minutes (0 = instant)')
    
    sched_delete = sched_subparsers.add_parser('delete', help='Delete schedule')
    sched_delete.add_argument('id', type=int, help='Schedule ID')
    
    # Config show command
    config_parser = subparsers.add_parser('config', help='Show configuration')
    config_subparsers = config_parser.add_subparsers(dest='config_cmd')
    config_show = config_subparsers.add_parser('show', help='Show effective config')
    config_show.add_argument('location', help='Location name')
    config_show.add_argument('cluster', help='Cluster name')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Initialize database
    db = DatabaseManager()
    try:
        success = await db.initialize()
        if not success:
            print("Error: Failed to initialize database connection")
            sys.exit(1)
    except Exception as e:
        print(f"Error connecting to database: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    try:
        # Route to appropriate command
        if args.command == 'setpoint':
            if args.setpoint_cmd == 'get':
                await cmd_setpoint_get(db, args.location, args.cluster, args.mode)
            elif args.setpoint_cmd == 'set':
                await cmd_setpoint_set(
                    db, args.location, args.cluster,
                    args.temp, args.humidity, args.co2, args.vpd, args.mode,
                    args.dry_run, args.author
                )
        
        elif args.command == 'pid':
            if args.pid_cmd == 'get':
                await cmd_pid_get(db, args.device_type)
            elif args.pid_cmd == 'set':
                await cmd_pid_set(
                    db, args.device_type, args.kp, args.ki, args.kd,
                    args.dry_run, args.author
                )
        
        elif args.command == 'schedule':
            if args.schedule_cmd == 'list':
                await cmd_schedule_list(db, args.location, args.cluster)
            elif args.schedule_cmd == 'create':
                enabled = not args.disabled if hasattr(args, 'disabled') else args.enabled
                await cmd_schedule_create(
                    db, args.name, args.location, args.cluster, args.device,
                    args.start, args.end, args.mode, args.day_of_week,
                    enabled, getattr(args, 'target_intensity', None),
                    getattr(args, 'ramp_up_duration', None),
                    getattr(args, 'ramp_down_duration', None),
                    args.dry_run, args.author
                )
            elif args.schedule_cmd == 'update':
                enabled = None
                if hasattr(args, 'enabled') and args.enabled:
                    enabled = True
                elif hasattr(args, 'disabled') and args.disabled:
                    enabled = False
                await cmd_schedule_update(
                    db, args.id, args.name, args.start, args.end,
                    args.mode, args.day_of_week, enabled,
                    getattr(args, 'target_intensity', None),
                    getattr(args, 'ramp_up_duration', None),
                    getattr(args, 'ramp_down_duration', None),
                    args.dry_run, args.author
                )
            elif args.schedule_cmd == 'delete':
                await cmd_schedule_delete(db, args.id, args.dry_run, args.author)
        
        elif args.command == 'config':
            if args.config_cmd == 'show':
                await cmd_config_show(db, args.location, args.cluster)
    
    finally:
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())

