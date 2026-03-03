import asyncio
from datetime import datetime
import os
import time

import asyncpg

# Database connection parameters from environment or defaults
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "cea_sensors")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "cea_user")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "cea_password_change_me")


async def run_load_test(duration_minutes=10):
    print(f"Starting 10-minute load test monitor at {datetime.now()}")

    try:
        conn = await asyncpg.connect(
            host=POSTGRES_HOST, database=POSTGRES_DB, user=POSTGRES_USER, password=POSTGRES_PASSWORD
        )
        print("Connected to database")
    except Exception as e:
        print(f"Failed to connect to database: {e}")
        return

    start_time = time.time()
    end_time = start_time + (duration_minutes * 60)

    ticks = []
    slow_ticks = 0
    total_ticks = 0

    print(f"Monitoring automation_state for {duration_minutes} minutes...")

    last_processed_timestamp = None

    try:
        while time.time() < end_time:
            # Query the latest tick timestamp from automation_state
            # We group by timestamp to identify a single "tick" across multiple devices
            row = await conn.fetchrow("""
                SELECT timestamp
                FROM automation_state
                ORDER BY timestamp DESC
                LIMIT 1
            """)

            if row:
                current_timestamp = row["timestamp"]

                if current_timestamp != last_processed_timestamp:
                    if last_processed_timestamp:
                        interval = (current_timestamp - last_processed_timestamp).total_seconds()
                        ticks.append(interval)
                        total_ticks += 1

                        if interval > 2.0:
                            slow_ticks += 1
                            print(f"[{datetime.now()}] SLOW TICK: {interval:.2f}s (Target: <2s)")
                        else:
                            # print(f"[{datetime.now()}] Tick: {interval:.2f}s")
                            pass

                    last_processed_timestamp = current_timestamp

            # Poll every 0.1s for high resolution
            await asyncio.sleep(0.1)

    except Exception as e:
        print(f"Error during load test: {e}")
    finally:
        await conn.close()

    # Analysis
    if ticks:
        avg_interval = sum(ticks) / len(ticks)
        max_interval = max(ticks)
        min_interval = min(ticks)

        print("\n--- LOAD TEST RESULTS ---")
        print(f"Duration: {duration_minutes} minutes")
        print(f"Total Ticks: {total_ticks}")
        print(f"Average Interval: {avg_interval:.2f}s")
        print(f"Max Interval: {max_interval:.2f}s")
        print(f"Min Interval: {min_interval:.2f}s")
        print(f"Slow Ticks (>2s): {slow_ticks} ({(slow_ticks / total_ticks) * 100:.1f}%)")
        print("-------------------------\n")

        # Document findings in a format that can be appended to learnings.md
        results_str = f"""
## Load Test Results - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- **Goal**: Validate 1Hz update rate under stress (10 min test)
- **Status**: Completed
- **Total Ticks**: {total_ticks}
- **Average Interval**: {avg_interval:.2f}s
- **Max Interval**: {max_interval:.2f}s
- **Slow Ticks (>2s)**: {slow_ticks}
- **Observations**:
    - The control loop interval should be 1s for 1Hz.
    - Currently configured at {os.environ.get("CURRENT_INTERVAL", "unknown")}s.
    - If Max Interval > 2s, the 1Hz requirement is NOT met.
"""
        with open("load_test_results.txt", "w") as f:
            f.write(results_str)


if __name__ == "__main__":
    import sys

    duration = 10
    if len(sys.argv) > 1:
        duration = int(sys.argv[1])
    asyncio.run(run_load_test(duration))
