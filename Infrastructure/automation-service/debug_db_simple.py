#!/usr/bin/env python3
import asyncio
import asyncpg

async def check_database():
    conn = await asyncpg.connect(
        host='localhost',
        database='cea_sensors',
        user='cea_user',
        password='Lenin1917'
    )

    print("="*70)
    print("FLOWER ROOM EFFECTIVE SETPOINTS ANALYSIS")
    print("="*70)

    # 1. Check if effective_setpoints table exists and has Flower Room data
    print("\n1. Table exists?")
    try:
        count = await conn.fetchval("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'effective_setpoints'")
        print(f"   effective_setpoints table exists: {count > 0}")
    except Exception as e:
        print(f"   Error checking table: {e}")
        return

    # 2. Check Flower Room data count
    print("\n2. Flower Room data in effective_setpoints:")
    flower_count = await conn.fetchval(
        "SELECT COUNT(*) FROM effective_setpoints WHERE location = $1 AND cluster = $2",
        'Flower Room', 'main'
    )
    print(f"   Total records: {flower_count}")

    if flower_count == 0:
        print("   ⚠️  NO DATA FOUND for Flower Room!")
        print("\n   Checking what locations exist in effective_setpoints:")
        locations = await conn.fetch(
            "SELECT DISTINCT location, cluster, COUNT(*) as count FROM effective_setpoints GROUP BY location, cluster ORDER BY location"
        )
        for row in locations:
            print(f"      {row['location']} | {row['cluster']} | {row['count']} records")
        await conn.close()
        return

    # 3. Check modes present for Flower Room
    print("\n3. Flower Room modes:")
    modes = await conn.fetch(
        "SELECT mode, COUNT(*) as count FROM effective_setpoints WHERE location = $1 AND cluster = $2 GROUP BY mode",
        'Flower Room', 'main'
    )
    for row in modes:
        print(f"   {row['mode']}: {row['count']} records")

    # 4. Check NULL values
    print("\n4. NULL value analysis:")
    heat_nulls = await conn.fetchval(
        "SELECT COUNT(*) FROM effective_setpoints WHERE location = $1 AND cluster = $2 AND effective_heating_setpoint IS NULL",
        'Flower Room', 'main'
    )
    cool_nulls = await conn.fetchval(
        "SELECT COUNT(*) FROM effective_setpoints WHERE location = $1 AND cluster = $2 AND effective_cooling_setpoint IS NULL",
        'Flower Room', 'main'
    )
    vpd_nulls = await conn.fetchval(
        "SELECT COUNT(*) FROM effective_setpoints WHERE location = $1 AND cluster = $2 AND effective_vpd_setpoint IS NULL",
        'Flower Room', 'main'
    )
    light_nulls = await conn.fetchval(
        "SELECT COUNT(*) FROM effective_setpoints WHERE location = $1 AND cluster = $2 AND effective_light_intensity IS NULL",
        'Flower Room', 'main'
    )
    print(f"   effective_heating_setpoint NULL: {heat_nulls}")
    print(f"   effective_cooling_setpoint NULL: {cool_nulls}")
    print(f"   effective_vpd_setpoint NULL: {vpd_nulls}")
    print(f"   effective_light_intensity NULL: {light_nulls}")

    # 5. Sample recent data
    print("\n5. Recent Flower Room data (last 5 records):")
    recent = await conn.fetch(
        """SELECT timestamp, mode, effective_heating_setpoint, effective_cooling_setpoint,
                  effective_vpd_setpoint, effective_light_intensity
           FROM effective_setpoints
           WHERE location = $1 AND cluster = $2
           ORDER BY timestamp DESC LIMIT 5""",
        'Flower Room', 'main'
    )
    for row in recent:
        print(f"   {row['timestamp']}")
        print(f"      mode={row['mode']}, heat={row['effective_heating_setpoint']}, "
              f"cool={row['effective_cooling_setpoint']}, vpd={row['effective_vpd_setpoint']}, "
              f"light={row['effective_light_intensity']}")

    # 6. Test the actual dashboard query
    print("\n6. Testing dashboard query (last 1 hour):")
    from datetime import datetime, timedelta
    one_hour_ago = datetime.now() - timedelta(hours=1)

    test_query = """
        SELECT
            time_bucket('5 minutes', timestamp) as bucket,
            AVG(effective_heating_setpoint) as avg_heat
        FROM effective_setpoints
        WHERE location = $1 AND cluster = $2
          AND effective_heating_setpoint IS NOT NULL
          AND timestamp >= $3
        GROUP BY bucket
        ORDER BY bucket
    """
    result = await conn.fetch(test_query, 'Flower Room', 'main', one_hour_ago)
    print(f"   Dashboard query results: {len(result)} data points")
    for row in result:
        print(f"      {row['bucket']}: avg_heat={row['avg_heat']}")

    # 7. Check last timestamp
    print("\n7. Last data timestamp:")
    last_time = await conn.fetchval(
        "SELECT MAX(timestamp) FROM effective_setpoints WHERE location = $1 AND cluster = $2",
        'Flower Room', 'main'
    )
    print(f"   Last entry: {last_time}")

    # 8. Check time range of data
    print("\n8. Data time range:")
    time_range = await conn.fetchrow(
        "SELECT MIN(timestamp) as min_time, MAX(timestamp) as max_time FROM effective_setpoints WHERE location = $1 AND cluster = $2",
        'Flower Room', 'main'
    )
    print(f"   From: {time_range['min_time']}")
    print(f"   To:   {time_range['max_time']}")

    print("\n" + "="*70)
    await conn.close()

if __name__ == '__main__':
    asyncio.run(check_database())
