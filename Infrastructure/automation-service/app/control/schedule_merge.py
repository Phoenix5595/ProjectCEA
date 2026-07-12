"""Schedule validation and merging with config.

Validates that dimmable lights have required DB configuration
(light_target_intensity rows, mode_parameters) and raises/clears
alarms via AlarmManager. Non-light DAY/NIGHT schedule rows are
still validated for completeness.
"""

from __future__ import annotations

from datetime import time
from typing import Any

from app.alarm_manager import AlarmManager
from app.database import DatabaseManager
from shared.infra_logging import get_logger

logger = get_logger(__name__)


async def merge_schedules_with_config(
    schedules: list[dict[str, Any]],
    config: Any,
    alarm_manager: AlarmManager | None = None,
    database: DatabaseManager | None = None,
) -> list[dict[str, Any]]:
    """Validate schedules against config and raise/clear alarms for missing light configuration.

    Call this whenever loading schedules into ``Scheduler`` (startup, refresh events, API updates).
    """
    if config is None:
        await validate_dimmable_light_schedule_coverage(schedules, None, alarm_manager, database)
        return list(schedules)
    devices = await config.get_devices()
    await validate_dimmable_light_schedule_coverage(schedules, devices, alarm_manager, database)
    await validate_light_config_against_schedules(schedules, devices, alarm_manager, database)
    return list(schedules)


def _parse_schedule_time_value(val: Any) -> time | None:
    if val is None:
        return None
    try:
        if isinstance(val, time):
            return val
        parts = str(val).split(":")
        if len(parts) >= 2:
            hour = int(parts[0])
            minute = int(parts[1])
            second = int(parts[2]) if len(parts) > 2 else 0
            return time(hour, minute, second)
    except (ValueError, IndexError) as e:
        logger.error("Error parsing schedule time %r: %s", val, e)
    return None


def _time_to_minutes(t: time) -> int:
    return (t.hour * 60 + t.minute) % 1440


def _mark_minutes(covered: bytearray, start_m: int, end_m: int, overnight: bool) -> None:
    if not overnight:
        for m in range(start_m, min(end_m, 1440)):
            covered[m] = 1
        return
    for m in range(start_m, 1440):
        covered[m] = 1
    for m in range(0, end_m):
        covered[m] = 1


def _daily_schedule_has_gaps(
    schedules: list[dict[str, Any]], loc: str, clus: str, device_name: str
) -> bool:
    """True if enabled SUN/DAY/MOON/NIGHT rows (daily only) do not cover all 1440 minutes."""
    all_rows: list[dict[str, Any]] = []
    for s in schedules:
        if s.get("location") != loc or s.get("cluster") != clus:
            continue
        if s.get("device_name") != device_name:
            continue
        if not s.get("enabled", True):
            continue
        all_rows.append(s)
    if not all_rows:
        return False
    if any(r.get("day_of_week") is not None for r in all_rows):
        return False
    covered = bytearray(1440)
    for s in all_rows:
        mode = str(s.get("mode", "")).upper()
        if mode not in ("SUN", "DAY", "MOON", "NIGHT"):
            continue
        st = _parse_schedule_time_value(s.get("start_time"))
        et = _parse_schedule_time_value(s.get("end_time"))
        if not st or not et:
            continue
        sm = _time_to_minutes(st)
        em = _time_to_minutes(et)
        overnight = st > et
        _mark_minutes(covered, sm, em, overnight)
    return covered.count(1) < 1440


async def _check_light_target_intensity(
    location: str,
    cluster: str,
    device_name: str,
    alarm_manager: AlarmManager | None,
    database: DatabaseManager | None,
) -> None:
    """Raise WARNING alarm if light has no light_target_intensity row; clear when present."""
    if not database or not alarm_manager:
        return
    try:
        device_id = await database.device_repo.get_device_id(location, cluster, device_name)
        if device_id is None:
            return
        active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
        if not active_mode:
            return
        mode_id = active_mode.get("mode_id")
        if mode_id is None:
            return
        intensity = await database.light_target_intensity_repo.get_intensity(device_id, mode_id)
        alarm_name = f"missing_light_target_intensity:{device_name}"
        if intensity is None:
            alarm_manager.raise_alarm(
                location,
                cluster,
                alarm_name,
                "warning",
                f"Light {device_name} has no light_target_intensity row for active mode "
                f"{active_mode.get('mode_name', 'unknown')}",
            )
        else:
            alarm_manager.clear_alarm(location, cluster, alarm_name)
    except Exception:
        logger.exception(
            "Error checking light_target_intensity for %s/%s/%s", location, cluster, device_name
        )


async def _check_mode_parameters(
    location: str,
    cluster: str,
    alarm_manager: AlarmManager | None,
    database: DatabaseManager | None,
) -> None:
    """Raise CRITICAL alarm if room has no mode_parameters for active mode; clear when present."""
    if not database or not alarm_manager:
        return
    try:
        active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
        if not active_mode:
            return
        mode_name = active_mode.get("mode_name")
        submode_name = active_mode.get("submode_name")
        if not mode_name:
            return
        params = await database.room_mode_repo.get_mode_parameters(
            location, cluster, mode_name, submode_name
        )
        alarm_name = "missing_mode_parameters"
        if params is None:
            alarm_manager.raise_alarm(
                location,
                cluster,
                alarm_name,
                "critical",
                f"Room {location}/{cluster} has no mode_parameters for active mode "
                f"{mode_name}/{submode_name or 'None'}",
            )
        else:
            alarm_manager.clear_alarm(location, cluster, alarm_name)
    except Exception:
        logger.exception("Error checking mode_parameters for %s/%s", location, cluster)


async def validate_dimmable_light_schedule_coverage(
    schedules: list[dict[str, Any]],
    devices: dict[str, dict[str, dict[str, dict[str, Any]]]] | None,
    alarm_manager: AlarmManager | None = None,
    database: DatabaseManager | None = None,
) -> None:
    """For DFR0971 lights: check light_target_intensity rows and raise/clear WARNING alarms.

    For non-light devices: logs errors when DAY/NIGHT schedule rows are missing.
    """
    if not devices:
        return

    def parseable_times(s: dict[str, Any]) -> bool:
        st, et = s.get("start_time"), s.get("end_time")
        return st is not None and et is not None and str(st).strip() != "" and str(et).strip() != ""

    checked_rooms: set[tuple[str, str]] = set()

    for loc, clusters in devices.items():
        for clus, devs in clusters.items():
            for device_name, info in devs.items():
                if str(device_name).startswith("light"):
                    if info.get("dimming_enabled") and info.get("dimming_type") == "dfr0971":
                        await _check_light_target_intensity(
                            loc, clus, device_name, alarm_manager, database
                        )
                        room_key = (loc, clus)
                        if room_key not in checked_rooms:
                            await _check_mode_parameters(loc, clus, alarm_manager, database)
                            checked_rooms.add(room_key)
                else:
                    sun_ok = moon_ok = False
                    for s in schedules:
                        if s.get("location") != loc or s.get("cluster") != clus:
                            continue
                        if s.get("device_name") != device_name:
                            continue
                        if not s.get("enabled", True):
                            continue
                        if not parseable_times(s):
                            continue
                        mode = str(s.get("mode", "")).upper()
                        if mode in ("SUN", "DAY"):
                            sun_ok = True
                        if mode in ("MOON", "NIGHT"):
                            moon_ok = True
                    if not sun_ok or not moon_ok:
                        logger.error(
                            "Device schedule coverage incomplete: location=%s cluster=%s "
                            "device=%s sun_or_day_row=%s moon_or_night_row=%s "
                            "(need enabled SUN/DAY and MOON/NIGHT rows with start_time and end_time)",
                            loc,
                            clus,
                            device_name,
                            sun_ok,
                            moon_ok,
                        )


async def validate_light_config_against_schedules(
    schedules: list[dict[str, Any]],
    devices: dict[str, dict[str, dict[str, dict[str, Any]]]] | None,
    alarm_manager: AlarmManager | None = None,
    database: DatabaseManager | None = None,
) -> None:
    """For DFR0971 lights: check mode_parameters and log schedule row presence.

    For non-light devices: logs when schedule rows are missing or daily windows leave gaps.
    """
    if not devices or not schedules:
        return

    checked_rooms: set[tuple[str, str]] = set()

    for loc, clusters in devices.items():
        for clus, devs in clusters.items():
            for device_name, info in devs.items():
                if str(device_name).startswith("light"):
                    if info.get("dimming_enabled") and info.get("dimming_type") == "dfr0971":
                        room_key = (loc, clus)
                        if room_key not in checked_rooms:
                            await _check_mode_parameters(loc, clus, alarm_manager, database)
                            checked_rooms.add(room_key)

                        row_count = sum(
                            1
                            for s in schedules
                            if s.get("location") == loc
                            and s.get("cluster") == clus
                            and s.get("device_name") == device_name
                        )
                        if row_count == 0:
                            logger.warning(
                                "Light %s in config has no schedule rows in DB: location=%s "
                                "cluster=%s — this is OK if light uses mode_parameters + "
                                "light_target_intensity exclusively.",
                                device_name,
                                loc,
                                clus,
                            )
                        if _daily_schedule_has_gaps(schedules, loc, clus, device_name):
                            logger.warning(
                                "Light daily schedule may not cover 24h: location=%s cluster=%s "
                                "device=%s — review SUN/MOON windows (day_of_week=null rows only).",
                                loc,
                                clus,
                                device_name,
                            )
                else:
                    row_count = sum(
                        1
                        for s in schedules
                        if s.get("location") == loc
                        and s.get("cluster") == clus
                        and s.get("device_name") == device_name
                    )
                    if row_count == 0:
                        logger.error(
                            "Device in config has no schedule rows in DB: location=%s "
                            "cluster=%s device=%s — check ZoneConfig device_name matches "
                            "schedules.device_name.",
                            loc,
                            clus,
                            device_name,
                        )
                    if _daily_schedule_has_gaps(schedules, loc, clus, device_name):
                        logger.warning(
                            "Device daily schedule may not cover 24h: location=%s cluster=%s "
                            "device=%s — review SUN/MOON windows (day_of_week=null rows only).",
                            loc,
                            clus,
                            device_name,
                        )
