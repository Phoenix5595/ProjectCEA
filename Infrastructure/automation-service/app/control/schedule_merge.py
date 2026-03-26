"""Merge DB schedules with config so the control scheduler always has per-light SUN rows.

After climate-period / ZoneConfig refactors, photoperiod may live only on ``room_schedule`` while
``light_*`` SUN rows are missing. That makes ``is_sun`` false (no day bounds) and/or
``get_schedule_intensity`` return 0 for every light — behavior that worked when redundant
per-light rows still existed. We synthesize SUN rows from ``room_schedule`` for each DFR0971
light in config when needed.
"""

from __future__ import annotations

from typing import Any


def expand_light_schedules_for_control(
    schedules: list[dict[str, Any]],
    devices: dict[str, dict[str, dict[str, dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """Return a new list: DB schedules plus synthetic SUN rows derived from ``room_schedule``.

    Args:
        schedules: Rows from ``schedule_repo.get_schedules()`` (any scope).
        devices: ``config.get_devices()`` hierarchy.

    Returns:
        Original schedules plus any synthetic light SUN rows not already present.
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

    synthetic: list[dict[str, Any]] = []
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
            synthetic.append(
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

    if not synthetic:
        return list(schedules)
    return list(schedules) + synthetic
