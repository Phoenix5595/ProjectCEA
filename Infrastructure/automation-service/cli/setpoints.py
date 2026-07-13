"""Setpoint validation and config show commands for config_cli."""

from __future__ import annotations

from app.database import DatabaseManager

SETPOINT_RANGES = {
    "temperature": (10.0, 35.0),  # °C
    "humidity": (30.0, 90.0),  # %
    "co2": (400.0, 2000.0),  # ppm
    "vpd": (0.0, 5.0),  # kPa
}


def validate_setpoint(name: str, value: float) -> tuple[bool, str | None]:
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


async def cmd_config_show(db: DatabaseManager, location: str, cluster: str):
    """Show effective configuration for a zone."""
    print(f"Effective configuration for {location}/{cluster}:")
    print()

    periods = await db.climate_periods_repo.get_periods(location, cluster)
    print("Climate Periods:")
    if periods:
        for p in periods:
            print(f"  {p.get('period_name', 'unnamed')}: {p.get('start_time')}-{p.get('end_time')}")
            print(f"    Heat: {p.get('heating_setpoint')}°C | Cool: {p.get('cooling_setpoint')}°C")
            print(f"    VPD: {p.get('vpd_setpoint')} kPa | CO2: {p.get('co2_setpoint')} ppm")
            print(f"    Ramp: {p.get('ramp_minutes', 0)} min")
    else:
        print("  (no periods defined)")
    print()

    # Get schedules
    schedules = await db.schedule_repo.get_schedules(location, cluster)
    print(f"Schedules ({len(schedules)} total):")
    for sched in schedules:
        if sched.get("enabled", True):
            mode_str = f" [{sched.get('mode', 'N/A')}]" if sched.get("mode") else ""
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
            print(
                f"  - {sched['name']}{mode_str}: {sched['start_time']} - {sched['end_time']}{intensity_str}"
            )
    print()

    # Get PID parameters (would need device types from config, simplified here)
    print("PID Parameters:")
    all_pid = await db.pid_repo.get_all_pid_parameters()
    for params in all_pid:
        device_type = params["device_type"]
        print(f"  {device_type}: Kp={params['kp']}, Ki={params['ki']}, Kd={params['kd']}")
