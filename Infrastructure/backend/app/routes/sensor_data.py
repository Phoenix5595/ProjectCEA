"""Bulk sensor/setpoint/light data for dashboard (Redis)."""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from fastapi import APIRouter

from app.redis_client import get_redis_client
from shared.infra_logging import get_logger
from shared.redis_keys import sensor_full

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["sensor-data"])


def _parse_location_cluster(key: str) -> tuple[str | None, str | None, str | None]:
    """Parse 'Location_cluster_rest' e.g. 'Veg Room_main_dry_bulb_setpoint_f' -> ('Veg Room', 'main', 'dry_bulb_setpoint_f')."""
    m = re.match(r"^(.+?)_main_(.+)$", key)
    if m:
        return m.group(1).strip(), "main", m.group(2)
    return None, None, None


@router.post("/sensor-data")
async def post_sensor_data(body: dict[str, Any]) -> dict[str, float]:
    """Return current values for requested keys from Redis.

    Request body: { "keys": ["Veg Room_main_heating_setpoint", "Veg Room_main_light_1_intensity", ...] }
    Returns: { "Veg Room_main_heating_setpoint": 22.5, ... } (only keys that were found). Temperatures in Celsius.
    """
    keys: list[str] = body.get("keys") or []
    if not keys:
        return {}

    client = await get_redis_client()
    if not client:
        return {}

    result: dict[str, float] = {}

    # Map dashboard keys to Redis sensor names (1-Wire: Lab_main_* -> lab_temp, water_temperature)
    LAB_SENSOR_ALIASES: dict[str, str] = {
        "Lab_main_lab_temp": "lab_temp",
        "Lab_main_water_temperature": "water_temperature",
    }

    try:
        # 1) Read canonical sensor state for each dashboard key (and Lab aliases).
        sensor_pairs: list[tuple[str, str]] = []
        for k in keys:
            if k in LAB_SENSOR_ALIASES:
                sensor_pairs.append((k, sensor_full("Lab", "main", LAB_SENSOR_ALIASES[k])))
                continue
            location, cluster, sensor_name = _parse_location_cluster(k)
            if location is not None and cluster is not None and sensor_name is not None:
                sensor_pairs.append((k, sensor_full(location, cluster, sensor_name)))

        values = await client.mget([redis_key for _, redis_key in sensor_pairs])
        for (key, _), val in zip(sensor_pairs, values, strict=True):
            if val is not None:
                with contextlib.suppress(ValueError, TypeError):
                    result[key] = float(val)

        remaining = [k for k in keys if k not in result]

        # 2) Setpoints: effective_setpoint:{location}:{cluster}:heating_setpoint etc.
        for key in remaining:
            if "setpoint" not in key:
                continue
            location, cluster, suffix = _parse_location_cluster(key)
            if not location or not cluster:
                continue
            prefix = f"cea:effective_setpoint:{location}:{cluster}"
            if "heating_setpoint" in key or "dry_bulb_setpoint" in key:
                raw = await client.get(f"{prefix}:heating_setpoint")
                if raw is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        result[key] = float(raw)
            elif "cooling_setpoint" in key:
                raw = await client.get(f"{prefix}:cooling_setpoint")
                if raw is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        result[key] = float(raw)
            elif "relative_humidity_setpoint" in key:
                raw = await client.get(f"{prefix}:humidity")
                if raw is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        result[key] = float(raw)
            elif "co2_setpoint" in key:
                raw = await client.get(f"{prefix}:co2")
                if raw is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        result[key] = float(raw)
            elif "vpd_setpoint" in key:
                raw = await client.get(f"{prefix}:vpd")
                if raw is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        result[key] = float(raw)

        remaining = [k for k in keys if k not in result]

        # 3) Light intensity: light:{location}:{cluster}:{device} JSON with "intensity"
        for key in remaining:
            if not key.endswith("_intensity"):
                continue
            location, cluster, rest = _parse_location_cluster(key)
            if not location or not cluster or rest is None or not rest.startswith("light_"):
                continue
            device = rest.replace("_intensity", "")
            redis_key = f"cea:light:{location}:{cluster}:{device}"
            raw = await client.get(redis_key)
            if raw is not None:
                try:
                    data = json.loads(raw)
                    if isinstance(data.get("intensity"), (int, float)):
                        result[key] = float(data["intensity"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
    except Exception as e:
        logger.warning(f"Error reading sensor-data from Redis: {e}")

    return result
