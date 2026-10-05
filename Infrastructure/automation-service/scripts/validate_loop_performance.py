import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

# Add the application and shared directory to the path
sys.path.append(os.path.join(os.getcwd(), "Infrastructure/automation-service"))
sys.path.append(os.path.join(os.getcwd(), "Infrastructure"))

from app.alarm_manager import AlarmManager
from app.automation.interlock_manager import InterlockManager
from app.automation.rules_engine import RulesEngine
from app.config import ConfigLoader
from app.control.control_engine import ControlEngine
from app.control.decision_event_policy import DecisionEventPolicy
from app.control.performance_monitor import get_performance_monitor
from app.control.relay_manager import RelayManager
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.control.scheduler import Scheduler
from app.database import DatabaseManager
from app.hardware.mcp23017 import MCP23017Driver


class _FakeSMBus:
    """In-memory stand-in for smbus2.SMBus used during the load test.

    The control loop hits MCP23017 once per tick (read current GPIOA to
    compare against the desired state). This fake records writes and
    returns what was last written so the driver's read-modify-write
    cycle works without real hardware.
    """

    _instances: list["_FakeSMBus"] = []

    def __init__(self, bus: int) -> None:
        self.bus = bus
        self.regs: dict[tuple[int, int], int] = {}
        _FakeSMBus._instances.append(self)

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        self.regs[(addr, reg)] = value & 0xFF

    def read_byte_data(self, addr: int, reg: int) -> int:
        return self.regs.get((addr, reg), 0)

    def write_byte(self, addr: int, value: int) -> None:
        return

    def write_word_data(self, addr: int, reg: int, value: int) -> None:
        self.regs[(addr, reg)] = value & 0xFF
        self.regs[(addr, (reg + 1) & 0xFF)] = (value >> 8) & 0xFF

    def close(self) -> None:
        return


class _FakeRuntimeDeviceRegistry:
    def __init__(self, snapshot: RuntimeDeviceSnapshot) -> None:
        self.snapshot = snapshot

    def subscribe(self, consumer) -> None:
        consumer(self.snapshot)


async def run_load_test():
    print("Starting 1Hz Control Loop Load Test (Simulated 10 minutes)...")

    # Initialize components with the real MCP23017 driver; hardware I2C
    # is replaced with _FakeSMBus via unittest.mock.patch below.
    config = ConfigLoader("Infrastructure/automation-service/automation_config.yaml")
    config._config["control"]["update_interval"] = 1
    device_hierarchy = {
        "Flower Room": {
            "main": {
                "heater_f_1": {
                    "device_id": 101,
                    "device_type": "heating",
                    "control_mode": "auto",
                    "channel": 1,
                }
            }
        }
    }
    snapshot = RuntimeDeviceSnapshot.create(
        version=1,
        hierarchy=device_hierarchy,
        mode_parameters={
            ("Flower Room", "main"): {
                "mode_id": 1,
                "day_start": "06:00",
                "night_start": "18:00",
                "ramp_up": 0,
                "ramp_down": 0,
            }
        },
        active_modes={
            ("Flower Room", "main"): {
                "mode_id": 1,
                "submode_id": None,
                "mode_name": "veg",
                "submode_name": None,
            }
        },
        light_intensities={},
        light_programs=[],
    )
    runtime_device_registry = _FakeRuntimeDeviceRegistry(snapshot)
    config._runtime_device_registry = runtime_device_registry

    db = DatabaseManager(
        db_config={
            "host": "127.0.0.1",
            "database": "monitoring_test_unused",
            "user": "test",
            "password": "unused",
        }
    )
    db._db_connected = True

    # Mock repositories to avoid needing real DB.
    db._sensor_repo = MagicMock()
    sensor_values = {
        sensor_name: 25.0
        for clusters in config.get_sensor_mapping().values()
        for cluster_sensors in clusters.values()
        for sensor_name in cluster_sensors.values()
        if sensor_name
    }
    db._sensor_repo.get_sensor_values_batch = AsyncMock(return_value=sensor_values)

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

    db._room_mode_repo = MagicMock()
    db._room_mode_repo.get_active_mode = AsyncMock(
        return_value={"mode_name": "flower", "mode_id": 1}
    )
    db._device_repo = MagicMock()
    db._device_repo.set_device_state = AsyncMock()

    db._control_action_repo = MagicMock()
    db._control_action_repo.log_control_action = AsyncMock()
    db._control_action_repo.log_automation_state = AsyncMock()
    db._control_action_repo.log_automation_state_batch = AsyncMock()

    db._pid_repo = MagicMock()
    db._pid_repo.get_pid_parameters = AsyncMock(return_value={"kp": 10, "ki": 0.1, "kd": 0})
    db._pid_repo.get_pid_control_mode = AsyncMock(
        return_value={"control_mode": "on_off", "hysteresis_high": 1.0, "hysteresis_low": 0.5}
    )

    # Mock Redis client
    redis_client = MagicMock()
    redis_client.redis_enabled = False
    redis_client.read_last_good_value = MagicMock(return_value={"value": 25.0})
    redis_client.check_last_good_age = MagicMock(return_value=(True, 1.0))
    redis_client.redis_client = None
    db._automation_redis = redis_client

    with patch("smbus2.SMBus", new=_FakeSMBus):
        mcp_driver = MCP23017Driver(
            i2c_bus=config.get("hardware.mcp_i2c_bus", 0),
            i2c_address=config.get("hardware.i2c_address", 0x27),
            active_low=config.get("hardware.active_low", True),
        )
    interlock_manager = InterlockManager(runtime_device_registry, config.get_interlocks())
    relay_manager = RelayManager(mcp_driver, runtime_device_registry, interlock_manager)

    # Add missing method that DeviceController expects
    relay_manager.set_channel_state = AsyncMock(return_value=True)

    scheduler = Scheduler([], climate_periods_repo=db._climate_periods_repo)
    event_policy = DecisionEventPolicy()
    rules_engine = RulesEngine([], scheduler, event_policy=event_policy)
    alarm_manager = AlarmManager(redis_client, db)

    engine = ControlEngine(
        relay_manager=relay_manager,
        database=db,
        config=config,
        scheduler=scheduler,
        rules_engine=rules_engine,
        runtime_device_registry=runtime_device_registry,
        event_policy=event_policy,
        alarm_manager=alarm_manager,
    )
    engine._ramps_restored = True

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
        except Exception as exc:
            raise RuntimeError(f"fake control tick {i} failed") from exc

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

    sensor_batch_reads = db._sensor_repo.get_sensor_values_batch.call_count
    print(f"Sensor batch reads: {sensor_batch_reads} ({iterations} ticks; one configured room)")
    print(f"Mean wall-clock tick time: {total_execution_time / iterations * 1000:.2f}ms")
    if sensor_batch_reads != iterations:
        raise RuntimeError(f"expected one sensor batch per tick, observed {sensor_batch_reads}")


if __name__ == "__main__":
    asyncio.run(run_load_test())
