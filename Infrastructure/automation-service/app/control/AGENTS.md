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
└── vpd_controller.py      # VPD calculation
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

## SCHEDULER CACHES

The `Scheduler` loads these caches atomically on startup. The `_ready` flag blocks the control loop until all caches are populated. All updates do atomic reference swaps (no partial state visible to the control loop).

| Cache | Method | Source |
|-------|--------|--------|
| Mode parameters | `update_mode_parameters()` | `mode_parameters` table |
| Light intensities | `update_light_intensities([{device_id, mode_id}: target_intensity])` | `light_target_intensity` table |
| Light programs | `update_light_programs()` | `light_programs` table |
| Device lookup | `update_device_lookup()` | `device_registry` table |

## LIGHT INTENSITY RESOLUTION

`get_schedule_intensity()` evaluates in order:

1. **Light programs** — highest priority active program wins (priority DESC, created_at ASC tie-break).
2. **Photoperiod + light_target_intensity cache** — if in sun period, look up `(device_id, mode_id)` in the intensity cache.
3. **0.0** — if not in photoperiod (moon / dark period).

Fallback: `MINIMUM_LIGHT_INTENSITY = 10.0` when no `light_target_intensity` row exists for the device/mode. Hardcoded safety default, not darkness.

## LIGHT RAMP STATE

Keyed by `(location, cluster, device_name)`. Intensity is computed from elapsed time since sun start or end (`time_since_start / ramp_up_duration`). On restart, the ramp resumes by recalculating from elapsed time. No stored intensity value is needed.

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

*Last updated: 2026-07-12*
