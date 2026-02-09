#!/usr/bin/env python3
"""Sync latest sensor values from replica TimescaleDB to Redis.

Reads from the latest_sensor_values view and writes sensor:* and sensor:*:ts
keys so Grafana (and other consumers) can read current values from Redis
instead of querying the DB constantly. Runs in a loop with configurable interval.
"""

import logging
import os
import sys
import time
from typing import NoReturn

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


def get_env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        logger.error("Missing required env %s", name)
        sys.exit(1)
    return val


def run_forever() -> NoReturn:
    pg_host = get_env("PGHOST", "projectcea_database")
    pg_port = get_env("PGPORT", "5432")
    pg_db = get_env("PGDATABASE", "cea_sensors")
    pg_user = get_env("PGUSER", "cea_user")
    pg_password = get_env("PGPASSWORD")
    redis_host = get_env("REDIS_HOST", "projectcea_redis")
    redis_port = int(get_env("REDIS_PORT", "6379"))
    interval_sec = int(get_env("SYNC_INTERVAL_SEC", "10"))

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
            conn.close()

            if not rows:
                logger.debug("No rows from latest_sensor_values")
            else:
                pipe = redis_client.pipeline()
                for row in rows:
                    name = row["sensor_name"]
                    value = row["value"]
                    ts = row["time"]
                    ts_ms = int(ts.timestamp() * 1000) if ts else 0
                    key = f"sensor:{name}"
                    ts_key = f"sensor:{name}:ts"
                    pipe.setex(key, REDIS_TTL_SEC, str(value))
                    pipe.setex(ts_key, REDIS_TTL_SEC, str(ts_ms))
                pipe.execute()
                logger.info("Synced %d sensors to Redis", len(rows))

        except psycopg2.OperationalError as e:
            logger.warning("DB error: %s; retry in %ss", e, DB_CONNECT_RETRY_SEC)
            time.sleep(DB_CONNECT_RETRY_SEC)
            continue
        except redis.RedisError as e:
            logger.warning("Redis error: %s; retry in %ss", e, REDIS_CONNECT_RETRY_SEC)
            time.sleep(REDIS_CONNECT_RETRY_SEC)
            continue
        except Exception as e:
            logger.exception("Sync error: %s", e)
            time.sleep(interval_sec)
            continue

        time.sleep(interval_sec)


if __name__ == "__main__":
    run_forever()
