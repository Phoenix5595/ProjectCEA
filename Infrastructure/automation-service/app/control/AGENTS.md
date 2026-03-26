# CONTROL LAYER

## OVERVIEW

Deterministic 2-second control loop: Read sensors → Evaluate rules → Run PID → Command actuators. Safety supervisor overrides all.

## STRUCTURE

```
control/
├── control_engine.py      # Main loop orchestration (665 lines)
├── scheduler.py           # Mode transitions, setpoint calculation (756 lines)
├── device_processor.py    # Device state management
├── relay_manager.py       # MCP23017 relay control
├── pid_controller.py      # PID implementation
└── tests/
```

## DEEP DIVE DOCS

| Topic | Document |
|-------|----------|
| Refactoring notes | `../../docs/control_engine_refactoring.md` |

## CONTROL LOOP (Every 2 seconds)

```
1. Read sensor values from Redis state keys
2. Resolve active climate period from climate_periods (by time) and compute effective setpoints (ramps between periods)
3. Load photoperiod bounds from room light schedule for is_sun / light intensity
4. Run PID controllers (heating, cooling, CO2 only); VPD-only for humidifier/dehumidifier
5. Apply safety constraints (failsafe supervisor)
6. Write actuator commands
```

## KEY CLASSES

| Class | File | Purpose |
|-------|------|---------|
| `ControlEngine` | `control_engine.py` | Main loop coordinator |
| `Scheduler` | `scheduler.py` | Mode + setpoint calculation |
| `PIDController` | `pid_controller.py` | Standard PID with anti-windup |
| `PIDControllerManager` | `pid_controller_manager.py` | PID lifecycle, control-mode routing (heating, cooling, CO2) |
| `VPDController` | `vpd_controller.py` | VPD calculation, target humidity from VPD |
| `VPDCascadeController` | `vpd_cascade_controller.py` | VPD-driven actuator selection (vent/dehum/humidifier) |
| `DeviceProcessor` | `device_processor.py` | Device loop, PID + VPD context |
| `DeviceController` | `device_controller.py` | Device output (PID, VPD-only, rule-based) |
| `RelayManager` | `relay_manager.py` | Hardware abstraction |

Light intensity comes from the scheduler (sun/moon schedules). Environmental setpoints come from the active **climate period** (`climate_periods`), not from a fixed four-mode `get_climate_mode` API (removed).

## SAFETY LAYERS

1. **Safety Supervisor**: Hard limits, sensor failure detection
2. **Interlocks**: e.g., heater OFF when exhaust ON
3. **Failsafe**: Last-known-good values, timeout detection

## ANTI-PATTERNS (CRITICAL)

| Never | Reason |
|-------|--------|
| Use `sleep()` or blocking calls | Kills deterministic timing |
| Hardcode setpoints in code | Use database |
| Direct hardware access | Bypasses interlocks + safety |
| Module-level state | Use instance vars or Redis |
| Read Redis Streams in loop | Streams = history, state keys = control |

## TODO (from code comments)

- `control_engine.py:481`: Integrate with scheduler for DAY/NIGHT mode
- `device_processor.py:67`: Implement failsafe logic
