# CONTROL LAYER

**Generated:** 2026-01-07

## OVERVIEW
Core automation logic: deterministic control loop (2s tick), PID orchestration, mode scheduling, hardware abstraction.

## STRUCTURE
```
control/
├── control_engine.py       # Main loop: Sensors -> Rules -> PID -> Actuators
├── scheduler.py           # Time-based mode (DAY/NIGHT) & ramp logic
├── pid_controller.py      # PID algo (Kp/Ki/Kd) with anti-windup
├── relay_manager.py       # Device state/interlock management
├── device_controller.py   # Hardware orchestration layer
├── setpoint_manager.py    # Effective setpoint calculation (ramps)
├── sensor_data_manager.py # Sensor data aggregation
└── performance_monitor.py # Loop timing metrics
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Main Loop | `control_engine.py` | `run_control_loop()` (2s tick) |
| PID Logic | `pid_controller.py` | Standard PID + reloadable params |
| Modes | `scheduler.py` | `get_climate_mode()` (DAY/NIGHT/PRE_*) |
| Actuation | `relay_manager.py` | `set_device_state()` with interlocks |
| Ramps | `setpoint_manager.py` | `compute_effective_setpoints()` |
| Tests | `tests/` | 20+ unit tests for control logic |

## CONVENTIONS
- **Loop**: Deterministic 2s tick. Never block.
- **State**: Redis = Hot (10s TTL), DB = Cold (Persistent).
- **Modes**: PRE_DAY > DAY > PRE_NIGHT > NIGHT.
- **Setpoints**: Calculated dynamically (Base + Ramp).
- **Hardware**: Always go through `RelayManager` (safety).

## ANTI-PATTERNS
- **Blocking**: No `sleep()` or long sync calls in loop.
- **Hardcoding**: No setpoints in code (use DB).
- **Bypass**: Direct hardware access skips interlocks.
- **Global**: No module-level state (use instance/Redis).
