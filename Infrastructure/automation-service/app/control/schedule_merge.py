"""Merge DB schedules with config so the control scheduler always has per-light SUN and MOON rows.

After climate-period / ZoneConfig refactors, photoperiod may live only on ``room_schedule`` while
``light_*`` SUN rows are missing. That makes ``is_sun`` false (no day bounds) and/or
``get_schedule_intensity`` return 0 for every light — behavior that worked when redundant
per-light rows still existed. We synthesize SUN rows from ``room_schedule`` for each DFR0971
light in config when needed.

We also synthesize **MOON** rows as the **complement** of the room photoperiod when a light has
no MOON/NIGHT row, so ``get_light_intensity_details`` resolves outside the sun window and Grafana
``effective_setpoints`` logging is not starved.
"""

from __future__ import annotations

from datetime import time
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


async def merge_schedules_with_config(
    schedules: list[dict[str, Any]], config: Any
) -> list[dict[str, Any]]:
    """Expand DB schedules with synthetic SUN/MOON rows from ``room_schedule`` for DFR0971 lights.

    Call this whenever loading schedules into ``Scheduler`` (startup, refresh events, API updates).
    """
    if config is None:
        merged = list(schedules)
        validate_dimmable_light_schedule_coverage(merged, None)
        return merged
    devices = await config.get_devices()
    merged = expand_light_schedules_for_control(schedules, devices)
    validate_dimmable_light_schedule_coverage(merged, devices)
    validate_light_config_against_schedules(merged, devices)
    return merged


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


def _format_schedule_time(t: time) -> str:
    if t.second:
        return f"{t.hour:02d}:{t.minute:02d}:{t.second:02d}"
    return f"{t.hour:02d}:{t.minute:02d}"


def _moon_window_from_sun_times(sun_start: time, sun_end: time) -> tuple[time, time] | None:
    """Complement of photoperiod [sun_start, sun_end) on the daily circle → MOON [m_start, m_end)."""
    if sun_start == sun_end:
        return None
    # Same rule for normal and overnight sun: moon runs from sun end to sun start.
    return sun_end, sun_start


def has_moon_or_night_row(
    schedules: list[dict[str, Any]], loc: str, clus: str, device_name: str
) -> bool:
    """True if the device has an enabled MOON or NIGHT row in ``schedules``."""
    for s in schedules:
        if s.get("location") != loc or s.get("cluster") != clus:
            continue
        if s.get("device_name") != device_name:
            continue
        if not s.get("enabled", True):
            continue
        mode = str(s.get("mode", "")).upper()
        if mode in ("MOON", "NIGHT"):
            return True
    return False


def validate_dimmable_light_schedule_coverage(
    schedules: list[dict[str, Any]],
    devices: dict[str, dict[str, dict[str, dict[str, Any]]]] | None,
) -> None:
    """Log errors when a DFR0971 dimmable light lacks enabled SUN/DAY or MOON/NIGHT rows with times.

    ``get_light_intensity_details`` needs a row matching the current window; missing MOON rows
    yield no match outside the sun window and logging/control gaps.
    """
    if not schedules or not devices:
        return

    def parseable_times(s: dict[str, Any]) -> bool:
        st, et = s.get("start_time"), s.get("end_time")
        return st is not None and et is not None and str(st).strip() != "" and str(et).strip() != ""

    for loc, clusters in devices.items():
        for clus, devs in clusters.items():
            for device_name, info in devs.items():
                if not str(device_name).startswith("light"):
                    continue
                if not info.get("dimming_enabled") or info.get("dimming_type") != "dfr0971":
                    continue
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
                        "Dimmable light schedule coverage incomplete: location=%s cluster=%s "
                        "device=%s sun_or_day_row=%s moon_or_night_row=%s "
                        "(need enabled SUN/DAY and MOON/NIGHT rows with start_time and end_time)",
                        loc,
                        clus,
                        device_name,
                        sun_ok,
                        moon_ok,
                    )


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


def validate_light_config_against_schedules(
    schedules: list[dict[str, Any]],
    devices: dict[str, dict[str, dict[str, dict[str, Any]]]] | None,
) -> None:
    """Log when config dimmable lights have no DB rows or daily windows leave gaps in 24h."""
    if not devices or not schedules:
        return
    for loc, clusters in devices.items():
        for clus, devs in clusters.items():
            for device_name, info in devs.items():
                if not str(device_name).startswith("light"):
                    continue
                if not info.get("dimming_enabled") or info.get("dimming_type") != "dfr0971":
                    continue
                row_count = sum(
                    1
                    for s in schedules
                    if s.get("location") == loc
                    and s.get("cluster") == clus
                    and s.get("device_name") == device_name
                )
                if row_count == 0:
                    logger.error(
                        "Dimmable light in config has no schedule rows in DB: location=%s "
                        "cluster=%s device=%s — check ZoneConfig device_name matches "
                        "schedules.device_name.",
                        loc,
                        clus,
                        device_name,
                    )
                if _daily_schedule_has_gaps(schedules, loc, clus, device_name):
                    logger.warning(
                        "Dimmable light daily schedule may not cover 24h: location=%s cluster=%s "
                        "device=%s — review SUN/MOON windows (day_of_week=null rows only).",
                        loc,
                        clus,
                        device_name,
                    )


def expand_light_schedules_for_control(
    schedules: list[dict[str, Any]],
    devices: dict[str, dict[str, dict[str, dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """Return DB schedules plus synthetic SUN and MOON rows derived from ``room_schedule``.

    Args:
        schedules: Rows from ``schedule_repo.get_schedules()`` (any scope).
        devices: ``config.get_devices()`` hierarchy.

    Returns:
        Original schedules plus synthetic light SUN/MOON rows as needed.
    """
    if not schedules or not devices:
        return list(schedules)

    # Latest room_schedule row per location/cluster (highest id wins).
    room_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for s in schedules:
        if s.get("device_name") != "room_schedule":
            continue
        if not s.get("enabled", True):
            continue
        key = (str(s["location"]), str(s["cluster"]))
        prev = room_by_key.get(key)
        sid = s.get("id") or 0
        if prev is None or sid > (prev.get("id") or 0):
            room_by_key[key] = s

    if not room_by_key:
        return list(schedules)

    def has_sun_or_day_row(loc: str, clus: str, device_name: str) -> bool:
        for s in schedules:
            if s.get("location") != loc or s.get("cluster") != clus:
                continue
            if s.get("device_name") != device_name:
                continue
            if not s.get("enabled", True):
                continue
            if str(s.get("mode", "")).upper() in ("SUN", "DAY"):
                return True
        return False

    def fallback_target_for_room(loc: str, clus: str) -> float:
        for s in schedules:
            if s.get("location") != loc or s.get("cluster") != clus:
                continue
            dn = str(s.get("device_name", ""))
            if not dn.startswith("light"):
                continue
            ti = s.get("target_intensity")
            if ti is not None:
                return float(ti)
        return 80.0

    synthetic_sun: list[dict[str, Any]] = []
    for (loc, clus), room in room_by_key.items():
        if loc not in devices or clus not in devices[loc]:
            continue
        target = fallback_target_for_room(loc, clus)
        for device_name, info in devices[loc][clus].items():
            if not str(device_name).startswith("light"):
                continue
            if not info.get("dimming_enabled") or info.get("dimming_type") != "dfr0971":
                continue
            if has_sun_or_day_row(loc, clus, device_name):
                continue
            synthetic_sun.append(
                {
                    "id": None,
                    "name": "synthetic from room_schedule",
                    "location": loc,
                    "cluster": clus,
                    "device_name": device_name,
                    "start_time": room["start_time"],
                    "end_time": room["end_time"],
                    "day_of_week": None,
                    "enabled": True,
                    "mode": "SUN",
                    "target_intensity": target,
                    "ramp_up_duration": room.get("ramp_up_duration") or 30,
                    "ramp_down_duration": room.get("ramp_down_duration") or 15,
                    "updated_at": None,
                }
            )

    base_with_sun = list(schedules) + synthetic_sun

    synthetic_moon: list[dict[str, Any]] = []
    for (loc, clus), room in room_by_key.items():
        if loc not in devices or clus not in devices[loc]:
            continue
        st = _parse_schedule_time_value(room.get("start_time"))
        et = _parse_schedule_time_value(room.get("end_time"))
        moon_pair = _moon_window_from_sun_times(st, et) if st and et else None
        if not moon_pair:
            continue
        m_start_t, m_end_t = moon_pair
        m_start_s = _format_schedule_time(m_start_t)
        m_end_s = _format_schedule_time(m_end_t)
        for device_name, info in devices[loc][clus].items():
            if not str(device_name).startswith("light"):
                continue
            if not info.get("dimming_enabled") or info.get("dimming_type") != "dfr0971":
                continue
            if has_moon_or_night_row(base_with_sun, loc, clus, device_name):
                continue
            synthetic_moon.append(
                {
                    "id": None,
                    "name": "synthetic MOON from room_schedule",
                    "location": loc,
                    "cluster": clus,
                    "device_name": device_name,
                    "start_time": m_start_s,
                    "end_time": m_end_s,
                    "day_of_week": None,
                    "enabled": True,
                    "mode": "MOON",
                    "target_intensity": 0.0,
                    "ramp_up_duration": room.get("ramp_down_duration") or 15,
                    "ramp_down_duration": room.get("ramp_up_duration") or 30,
                    "updated_at": None,
                }
            )

    if not synthetic_sun and not synthetic_moon:
        return list(schedules)
    return list(schedules) + synthetic_sun + synthetic_moon
