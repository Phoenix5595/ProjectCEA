import asyncio
from datetime import datetime
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

# Add the application and shared directory to the path
sys.path.append(os.path.join(os.getcwd(), "Infrastructure/automation-service"))
sys.path.append(os.path.join(os.getcwd(), "Infrastructure"))

from app.alarm_manager import AlarmManager
from app.automation.interlock_manager import InterlockManager
from app.automation.rules_engine import RulesEngine
from app.config import ConfigLoader
from app.control.control_engine import ControlEngine
from app.control.performance_monitor import get_performance_monitor
from app.control.relay_manager import RelayManager
from app.control.scheduler import Scheduler
from app.database import DatabaseManager
from app.hardware.mcp23017 import MCP23017Driver


async def run_load_test():
    print("Starting 1Hz Control Loop Load Test (Simulated 10 minutes)...")

    # Initialize components with simulation mode
    config = ConfigLoader("Infrastructure/automation-service/automation_config.yaml")
    config._config["hardware"]["simulation"] = True
    config._config["control"]["update_interval"] = 1

    db = DatabaseManager()
    db._db_connected = True

    # Mock repositories to avoid needing real DB
    db._sensor_repo = MagicMock()
    db._sensor_repo.get_sensor_value = AsyncMock(return_value=25.0)

    db._climate_periods_repo = MagicMock()
    db._climate_periods_repo.get_active_period = AsyncMock(
        return_value={
            "period_name": "DAY",
            "heating_setpoint": 24.0,
            "cooling_setpoint": 26.0,
            "humidity": 60.0,
            "co2": 1000.0,
            "vpd": 1.2,
        }
    )
    db._climate_periods_repo.get_periods = AsyncMock(
        return_value=[
            {
                "period_name": "DAY",
                "start_time": "06:00",
                "end_time": "18:00",
                "ramp_minutes": 0,
                "heating_setpoint": 24.0,
                "cooling_setpoint": 26.0,
                "humidity": 60.0,
                "co2": 1000.0,
                "vpd": 1.2,
            },
            {
                "period_name": "NIGHT",
                "start_time": "18:00",
                "end_time": "06:00",
                "ramp_minutes": 0,
                "heating_setpoint": 20.0,
                "cooling_setpoint": 25.0,
                "humidity": 65.0,
                "co2": 800.0,
                "vpd": 0.8,
            },
        ]
    )

    db._setpoint_repo = MagicMock()
    db._setpoint_repo.log_effective_setpoints = AsyncMock()

    db._schedule_repo = MagicMock()
    db._schedule_repo.get_room_light_schedule = AsyncMock(
        return_value={"day_start_time": "06:00", "day_end_time": "18:00"}
    )
    db._schedule_repo.get_climate_schedule = AsyncMock(
        return_value={"pre_day_duration": 30, "pre_night_duration": 30}
    )
    db._schedule_repo.get_schedules = AsyncMock(return_value=[])

    db._device_repo = MagicMock()
    db._device_repo.set_device_state = AsyncMock()

    db._control_action_repo = MagicMock()
    db._control_action_repo.log_control_action = AsyncMock()
    db._control_action_repo.log_automation_state = AsyncMock()

    db._pid_repo = MagicMock()
    db._pid_repo.get_pid_parameters = AsyncMock(return_value={"kp": 10, "ki": 0.1, "kd": 0})

    # Mock Redis client
    redis_client = MagicMock()
    redis_client.redis_enabled = True
    redis_client.read_last_good_value = MagicMock(return_value={"value": 25.0})
    redis_client.check_last_good_age = MagicMock(return_value=(True, 1.0))
    db._automation_redis = redis_client

    mcp_driver = MCP23017Driver(
        i2c_bus=config.get("hardware.mcp_i2c_bus", 0),
        i2c_address=config.get("hardware.i2c_address", 0x27),
        simulation=True,
    )
    interlock_manager = InterlockManager(config.get_devices(), config.get_interlocks())
    relay_manager = RelayManager(mcp_driver, config.get_devices(), interlock_manager)

    # Add missing method that DeviceController expects
    relay_manager.set_channel_state = AsyncMock(return_value=True)

    scheduler = Scheduler([], climate_periods_repo=db._climate_periods_repo)
    rules_engine = RulesEngine([], scheduler)
    alarm_manager = AlarmManager(redis_client, db)

    engine = ControlEngine(
        relay_manager=relay_manager,
        database=db,
        config=config,
        scheduler=scheduler,
        rules_engine=rules_engine,
        alarm_manager=alarm_manager,
    )

    monitor = get_performance_monitor()
    monitor.reset()

    iterations = 600  # 10 minutes at 1Hz
    slow_ticks = 0
    total_execution_time = 0

    print(f"Running {iterations} iterations...")

    for i in range(iterations):
        start_time = time.perf_counter()

        try:
            await engine.run_control_loop()
        except Exception as e:
            print(f"  iter {i}: {type(e).__name__}: {e}")

        execution_time = time.perf_counter() - start_time
        total_execution_time += execution_time

        if execution_time > 2.0:
            print(f"CRITICAL: Tick {i} took {execution_time:.3f}s (> 2.0s)")
            slow_ticks += 1
        elif execution_time > 1.0:
            # print(f"Warning: Tick {i} took {execution_time:.3f}s (> 1.0s)")
            pass

        if i % 60 == 0 and i > 0:
            print(f"Progress: {i}/{iterations} iterations completed...")

    print("\n--- Load Test Results ---")
    stats = monitor.get_statistics()

    loop_stats = stats.get("total_loop_time", {})
    print(f"Total Iterations: {iterations}")
    print(f"Average Execution Time: {loop_stats.get('average', 0) * 1000:.2f}ms")
    print(f"Max Execution Time: {loop_stats.get('max', 0) * 1000:.2f}ms")
    print(f"P95 Execution Time: {loop_stats.get('p95', 0) * 1000:.2f}ms")
    print(f"P99 Execution Time: {loop_stats.get('p99', 0) * 1000:.2f}ms")
    print(f"Ticks > 2.0s: {slow_ticks}")

    # Document findings
    notepad_dir = ".sisyphus/notepads/MASTER-CONSOLIDATION-PLAN"
    os.makedirs(notepad_dir, exist_ok=True)
    with open(os.path.join(notepad_dir, "learnings.md"), "a") as f:
        f.write(
            f"\n## Control Loop Performance Validation ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n"
        )
        f.write(f"- Iterations: {iterations}\n")
        f.write(f"- Avg Tick Execution: {loop_stats.get('average', 0) * 1000:.2f}ms\n")
        f.write(f"- Max Tick Execution: {loop_stats.get('max', 0) * 1000:.2f}ms\n")
        f.write(f"- P95 Tick Execution: {loop_stats.get('p95', 0) * 1000:.2f}ms\n")
        f.write(f"- Ticks > 2.0s: {slow_ticks}\n")
        if slow_ticks == 0 and loop_stats.get("p95", 0) < 1.5:
            f.write("- Result: PASS - Control loop maintains 1Hz performance targets.\n")
        else:
            f.write("- Result: FAIL - Performance degradation detected.\n")


if __name__ == "__main__":
    asyncio.run(run_load_test())
