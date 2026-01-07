#!/usr/bin/env python3

import asyncio
import sys
import os
sys.path.append('.')

# Get password from environment variable (do not hardcode)
if not os.getenv('POSTGRES_PASSWORD'):
    raise ValueError("POSTGRES_PASSWORD environment variable is required")

if not os.getenv('REDIS_URL'):
    os.environ['REDIS_URL'] = 'redis://localhost:6379'

from app.database import DatabaseManager
from app.control.scheduler import Scheduler

async def check_effective_setpoints():
    db = DatabaseManager()
    await db.initialize()

    print("="*70)
    print("EFFECTIVE_SETPOINTS DATA ANALYSIS")
    print("="*70)

    # Check Flower Room data
    print("\n1. Flower Room effective_setpoints:")
    flower_count = await db._pool.fetchval(
        'SELECT COUNT(*) FROM effective_setpoints WHERE location = $1 AND cluster = $2',
        'Flower Room', 'main'
    )
    print(f"   Total records: {flower_count}")

    flower_modes = await db._pool.fetch(
        'SELECT mode, COUNT(*) as count FROM effective_setpoints WHERE location = $1 AND cluster = $2 GROUP BY mode',
        'Flower Room', 'main'
    )
    print(f"   Modes: {[(row['mode'], row['count']) for row in flower_modes]}")

    # Check Veg Room data
    print("\n2. Veg Room effective_setpoints:")
    veg_count = await db._pool.fetchval(
        'SELECT COUNT(*) FROM effective_setpoints WHERE location = $1 AND cluster = $2',
        'Veg Room', 'main'
    )
    print(f"   Total records: {veg_count}")

    veg_modes = await db._pool.fetch(
        'SELECT mode, COUNT(*) as count FROM effective_setpoints WHERE location = $1 AND cluster = $2 GROUP BY mode',
        'Veg Room', 'main'
    )
    print(f"   Modes: {[(row['mode'], row['count']) for row in veg_modes]}")

    # Get sample data for Flower Room
    print("\n3. Flower Room recent data (last 5 records):")
    flower_recent = await db._pool.fetch(
        "SELECT timestamp, mode, effective_heating_setpoint, effective_cooling_setpoint, effective_vpd_setpoint "
        "FROM effective_setpoints WHERE location = $1 AND cluster = $2 "
        "ORDER BY timestamp DESC LIMIT 5",
        'Flower Room', 'main'
    )
    for row in flower_recent:
        print(f"   {row['timestamp']} | mode={row['mode']} | "
              f"heat={row['effective_heating_setpoint']} | "
              f"cool={row['effective_cooling_setpoint']} | "
              f"vpd={row['effective_vpd_setpoint']}")

    # Get sample data for Veg Room
    print("\n4. Veg Room recent data (last 5 records):")
    veg_recent = await db._pool.fetch(
        "SELECT timestamp, mode, effective_heating_setpoint, effective_cooling_setpoint, effective_vpd_setpoint "
        "FROM effective_setpoints WHERE location = $1 AND cluster = $2 "
        "ORDER BY timestamp DESC LIMIT 5",
        'Veg Room', 'main'
    )
    for row in veg_recent:
        print(f"   {row['timestamp']} | mode={row['mode']} | "
              f"heat={row['effective_heating_setpoint']} | "
              f"cool={row['effective_cooling_setpoint']} | "
              f"vpd={row['effective_vpd_setpoint']}")

    # Check what the flower dashboard query would return
    print("\n5. Simulating Flower Room DAY query (last 1 hour):")
    from datetime import datetime, timedelta
    one_hour_ago = datetime.now() - timedelta(hours=1)

    # This is what the flower dashboard queries use
    flower_day_query = await db._pool.fetch(
        """SELECT
            time_bucket('5 minutes', timestamp) as bucket,
            AVG(effective_heating_setpoint) as avg_heat,
            AVG(effective_cooling_setpoint) as avg_cool,
            AVG(effective_vpd_setpoint) as avg_vpd,
            AVG(effective_light_intensity) as avg_light
         FROM effective_setpoints
         WHERE location = $1 AND cluster = $2 AND mode = $3
           AND timestamp >= $4
         GROUP BY bucket
         ORDER BY bucket""",
        'Flower Room', 'main', 'DAY', one_hour_ago
    )
    print(f"   DAY records found: {len(flower_day_query)}")

    flower_night_query = await db._pool.fetch(
        """SELECT
            time_bucket('5 minutes', timestamp) as bucket,
            AVG(effective_heating_setpoint) as avg_heat,
            AVG(effective_cooling_setpoint) as avg_cool,
            AVG(effective_vpd_setpoint) as avg_vpd,
            AVG(effective_light_intensity) as avg_light
         FROM effective_setpoints
         WHERE location = $1 AND cluster = $2 AND mode = $3
           AND timestamp >= $4
         GROUP BY bucket
         ORDER BY bucket""",
        'Flower Room', 'main', 'NIGHT', one_hour_ago
    )
    print(f"   NIGHT records found: {len(flower_night_query)}")

    flower_pre_day_query = await db._pool.fetch(
        """SELECT
            time_bucket('5 minutes', timestamp) as bucket,
            AVG(effective_heating_setpoint) as avg_heat,
            AVG(effective_cooling_setpoint) as avg_cool,
            AVG(effective_vpd_setpoint) as avg_vpd,
            AVG(effective_light_intensity) as avg_light
         FROM effective_setpoints
         WHERE location = $1 AND cluster = $2 AND mode = $3
           AND timestamp >= $4
         GROUP BY bucket
         ORDER BY bucket""",
        'Flower Room', 'main', 'PRE_DAY', one_hour_ago
    )
    print(f"   PRE_DAY records found: {len(flower_pre_day_query)}")

    flower_pre_night_query = await db._pool.fetch(
        """SELECT
            time_bucket('5 minutes', timestamp) as bucket,
            AVG(effective_heating_setpoint) as avg_heat,
            AVG(effective_cooling_setpoint) as avg_cool,
            AVG(effective_vpd_setpoint) as avg_vpd,
            AVG(effective_light_intensity) as avg_light
         FROM effective_setpoints
         WHERE location = $1 AND cluster = $2 AND mode = $3
           AND timestamp >= $4
         GROUP BY bucket
         ORDER BY bucket""",
        'Flower Room', 'main', 'PRE_NIGHT', one_hour_ago
    )
    print(f"   PRE_NIGHT records found: {len(flower_pre_night_query)}")

    print("\n6. Simulating Veg Room query (last 1 hour):")
    veg_day_query = await db._pool.fetch(
        """SELECT
            time_bucket('5 minutes', timestamp) as bucket,
            AVG(effective_heating_setpoint) as avg_heat
         FROM effective_setpoints
         WHERE location = $1 AND cluster = $2 AND mode = $3
           AND timestamp >= $4
         GROUP BY bucket
         ORDER BY bucket""",
        'Veg Room', 'main', 'DAY', one_hour_ago
    )
    print(f"   DAY records found: {len(veg_day_query)}")

    print("\n" + "="*70)
    await db.close()

if __name__ == '__main__':
    asyncio.run(check_effective_setpoints())
