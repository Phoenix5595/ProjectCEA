#!/usr/bin/env python3
"""Sync latest sensor values from replica TimescaleDB to Redis.

Reads from the latest_sensor_values view and writes cea:sensor:* state keys
keys so Grafana (and other consumers) can read current values from Redis
instead of querying the DB constantly. Also builds pre-formatted hashes for
Flower operator tables (Averages, Front cluster, Back cluster) consumed via
HGETALL — see cea:grafana:flower_* keys in provisioning docs.

Runs in a loop with configurable interval (``SYNC_INTERVAL_SEC``; Iskra default 1 s
in docker-compose so Flower cluster tables track the 1 s dashboard refresh).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, NoReturn

import psycopg2
from psycopg2.extras import RealDictCursor
import redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

REDIS_TTL_SEC = 30
DB_CONNECT_RETRY_SEC = 5
REDIS_CONNECT_RETRY_SEC = 5

# Keys for Flower dashboard table panels (HGETALL; field order = sort order).
REDIS_PANEL_FLOWER_FRONT = "cea:grafana:flower_front"
REDIS_PANEL_FLOWER_BACK = "cea:grafana:flower_back"
REDIS_PANEL_FLOWER_AVERAGES = "cea:grafana:flower_averages"

_ORDER_CASE_FRONT = """
    CASE
        WHEN s.name LIKE 'dry_bulb%%' THEN 1
        WHEN s.name LIKE 'wet_bulb%%' THEN 2
        WHEN s.name LIKE 'rh%%' AND s.name NOT LIKE 'secondary_rh%%' THEN 3
        WHEN s.name LIKE 'vpd%%' THEN 4
        WHEN s.name LIKE 'co2%%' THEN 5
        WHEN s.name LIKE 'pressure%%' THEN 6
        WHEN s.name LIKE 'secondary_temp%%' THEN 7
        WHEN s.name LIKE 'secondary_rh%%' THEN 8
        WHEN s.name LIKE 'water_level%%' THEN 9
        ELSE 10
    END, s.name
"""

_ORDER_CASE_BACK = """
    CASE
        WHEN sensor_name LIKE 'dry_bulb%%' THEN 1
        WHEN sensor_name LIKE 'wet_bulb%%' THEN 2
        WHEN sensor_name LIKE 'rh%%' AND sensor_name NOT LIKE 'secondary_rh%%' THEN 3
        WHEN sensor_name LIKE 'vpd%%' THEN 4
        WHEN sensor_name LIKE 'co2%%' THEN 5
        WHEN sensor_name LIKE 'pressure%%' THEN 6
        WHEN sensor_name LIKE 'secondary_temp%%' THEN 7
        WHEN sensor_name LIKE 'secondary_rh%%' THEN 8
        WHEN sensor_name LIKE 'water_level%%' THEN 9
        ELSE 10
    END, sensor_name
"""

# (base_without_suffix, display label, unit, sort order) — matches Flower SQL averages.
_AVG_SPECS: list[tuple[str, str, str, int]] = [
    ("dry_bulb", "Dry Bulb Avg", "°C", 1),
    ("wet_bulb", "Wet Bulb Avg", "°C", 2),
    ("rh", "RH Avg", "%", 3),
    ("vpd", "VPD Avg", " kPa", 4),
    ("co2", "CO2 Avg", " ppm", 5),
    ("pressure", "Pressure Avg", " hPa", 6),
    ("water_level", "Water Level Avg", " mm", 9),
]


def get_env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        logger.error("Missing required env %s", name)
        sys.exit(1)
    return val


def _dash() -> str:
    return "\u2014"


def _cell_text(value: Any) -> str:
    """Format a measurement cell like Grafana ::text on REAL (no forced decimals)."""
    if value is None:
        return _dash()
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return str(value)


def _front_display(sensor_name: str) -> str:
    m = {
        "dry_bulb_f": "Dry Bulb",
        "wet_bulb_f": "Wet Bulb",
        "rh_f": "RH",
        "vpd_f": "VPD",
        "co2_f": "CO2",
        "pressure_f": "Pressure",
        "secondary_temp_f": "Secondary Temp",
        "secondary_rh_f": "Secondary RH",
        "water_level_f": "Water Level",
    }
    return m.get(sensor_name, sensor_name)


def _front_unit(sensor_name: str) -> str:
    if sensor_name.startswith("dry_bulb") or sensor_name.startswith("secondary_temp"):
        return "°C"
    if sensor_name.startswith("wet_bulb"):
        return "°C"
    if sensor_name.startswith("rh") and not sensor_name.startswith("secondary_rh"):
        return "%"
    if sensor_name.startswith("secondary_rh"):
        return "%"
    if sensor_name.startswith("vpd"):
        return " kPa"
    if sensor_name.startswith("co2"):
        return " ppm"
    if sensor_name.startswith("pressure"):
        return " hPa"
    if sensor_name.startswith("water_level"):
        return " mm"
    return ""


def _back_display(sensor_name: str) -> str:
    m = {
        "dry_bulb_b": "Dry Bulb",
        "wet_bulb_b": "Wet Bulb",
        "rh_b": "RH",
        "vpd_b": "VPD",
        "co2_b": "CO2",
        "pressure_b": "Pressure",
        "secondary_temp_b": "Secondary Temp",
        "secondary_rh_b": "Secondary RH",
        "water_level_b": "Water Level",
    }
    return m.get(sensor_name, sensor_name)


def _back_unit(sensor_name: str) -> str:
    return _front_unit(sensor_name.replace("_b", "_f", 1)) if sensor_name.endswith("_b") else ""


def _format_ts_max(times: list[Any]) -> str:
    if not times:
        return "No samples"
    valid = [t for t in times if t is not None]
    if not valid:
        return "No samples"
    mx = max(valid)
    return mx.strftime("%Y/%m/%d %H:%M:%S")


def _build_flower_front_panel(
    rows_by_name: dict[str, dict[str, Any]], front_names: list[str]
) -> dict[str, str]:
    """Hash fields are display labels (insertion order preserved for HGETALL on Redis 7+)."""
    mapping: dict[str, str] = {}
    times: list[Any] = []
    for name in front_names:
        row = rows_by_name.get(name)
        label = _front_display(name)
        unit = _front_unit(name)
        if row is None or row.get("value") is None:
            mapping[label] = _dash() + unit
        else:
            mapping[label] = _cell_text(row["value"]) + unit
        if row and row.get("time") is not None:
            times.append(row["time"])
    mapping["Last Update"] = _format_ts_max(times)
    return mapping


def _build_flower_back_panel(
    rows_by_name: dict[str, dict[str, Any]], back_names: list[str]
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    times: list[Any] = []
    for name in back_names:
        row = rows_by_name.get(name)
        label = _back_display(name)
        unit = _back_unit(name)
        if row is None or row.get("value") is None:
            mapping[label] = _dash() + unit
        else:
            mapping[label] = _cell_text(row["value"]) + unit
        if row and row.get("time") is not None:
            times.append(row["time"])
    mapping["Last Update"] = _format_ts_max(times)
    return mapping


def _build_flower_averages_panel(rows_by_name: dict[str, dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    all_times: list[Any] = []
    for base, label, unit, _sort in _AVG_SPECS:
        fk = f"{base}_f"
        bk = f"{base}_b"
        ff = rows_by_name.get(fk)
        fb = rows_by_name.get(bk)
        for r in (ff, fb):
            if r and r.get("time") is not None:
                all_times.append(r["time"])

        vnum: float | None = None
        if (
            ff is not None
            and fb is not None
            and ff.get("value") is not None
            and fb.get("value") is not None
        ):
            vnum = (float(ff["value"]) + float(fb["value"])) / 2.0
        elif fb is not None and fb.get("value") is not None:
            vnum = float(fb["value"])
        elif ff is not None and ff.get("value") is not None:
            vnum = float(ff["value"])

        if vnum is None:
            continue
        mapping[label] = f"{round(vnum, 2)}{unit}"

    mapping["Last Update"] = _format_ts_max(all_times)
    return mapping


def _push_hash(pipe: Any, key: str, mapping: dict[str, str]) -> None:
    pipe.delete(key)
    if mapping:
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, REDIS_TTL_SEC)


def run_forever() -> NoReturn:
    pg_host = get_env("PGHOST", "projectcea_database")
    pg_port = get_env("PGPORT", "5432")
    pg_db = get_env("PGDATABASE", "cea_sensors")
    pg_user = get_env("PGUSER", "cea_user")
    pg_password = get_env("PGPASSWORD")
    redis_host = get_env("REDIS_HOST", "projectcea_redis")
    redis_port = int(get_env("REDIS_PORT", "6379"))
    interval_sec = int(get_env("SYNC_INTERVAL_SEC", "1"))

    redis_client: redis.Redis | None = None
    while True:
        try:
            redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                decode_responses=True,
            )
            redis_client.ping()
            break
        except redis.RedisError as e:
            logger.warning("Redis not ready: %s; retry in %ss", e, REDIS_CONNECT_RETRY_SEC)
            time.sleep(REDIS_CONNECT_RETRY_SEC)

    logger.info("Redis connected")

    front_sql = f"SELECT s.name FROM sensor s WHERE s.name LIKE '%%_f' ORDER BY {_ORDER_CASE_FRONT}"
    back_sql = f"""
        SELECT sensor_name FROM latest_sensor_values
        WHERE sensor_name LIKE '%%_b'
        ORDER BY {_ORDER_CASE_BACK}
    """

    while True:
        try:
            conn = psycopg2.connect(
                host=pg_host,
                port=pg_port,
                dbname=pg_db,
                user=pg_user,
                password=pg_password,
                connect_timeout=5,
            )
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT sensor_name, value, time FROM latest_sensor_values")
                rows = cur.fetchall()
                cur.execute(front_sql)
                front_names = [r["name"] for r in cur.fetchall()]
                cur.execute(back_sql)
                back_names = [r["sensor_name"] for r in cur.fetchall()]
            conn.close()

            rows_by_name = {r["sensor_name"]: r for r in rows}

            pipe = redis_client.pipeline()
            if not rows:
                logger.debug("No rows from latest_sensor_values")
            else:
                for row in rows:
                    name = row["sensor_name"]
                    value = row["value"]
                    ts = row["time"]
                    ts_ms = int(ts.timestamp() * 1000) if ts else 0
                    key = f"cea:sensor:global:main:{name}"
                    ts_key = f"cea:sensor:global:main:{name}_ts"
                    pipe.setex(key, REDIS_TTL_SEC, str(value))
                    pipe.setex(ts_key, REDIS_TTL_SEC, str(ts_ms))
                logger.info("Synced %d sensors to Redis", len(rows))

            front_map = _build_flower_front_panel(rows_by_name, front_names)
            back_map = _build_flower_back_panel(rows_by_name, back_names)
            avg_map = _build_flower_averages_panel(rows_by_name)
            _push_hash(pipe, REDIS_PANEL_FLOWER_FRONT, front_map)
            _push_hash(pipe, REDIS_PANEL_FLOWER_BACK, back_map)
            _push_hash(pipe, REDIS_PANEL_FLOWER_AVERAGES, avg_map)
            pipe.execute()

        except psycopg2.OperationalError as e:
            logger.warning("DB error: %s; retry in %ss", e, DB_CONNECT_RETRY_SEC)
            time.sleep(DB_CONNECT_RETRY_SEC)
            continue
        except redis.RedisError as e:
            logger.warning("Redis error: %s; retry in %ss", e, REDIS_CONNECT_RETRY_SEC)
            time.sleep(REDIS_CONNECT_RETRY_SEC)
            continue
        except Exception:
            logger.exception("Sync error")
            time.sleep(interval_sec)
            continue

        time.sleep(interval_sec)


if __name__ == "__main__":
    run_forever()
