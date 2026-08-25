# Control Layer

Deterministic 1-second control loop for climate, lights, and hardware outputs.

## Package layout

```
control/
├── control_engine.py              # Main loop orchestration
├── scheduler/                     # Time-based projections
│   ├── __init__.py                # Scheduler and install_snapshot()
│   ├── photoperiod.py             # Sun/moon resolution
│   ├── light_intensity.py         # (device_id, mode_id) anchors
│   ├── light_programs.py          # Supplemental / override programs
│   └── schedules.py               # Non-light DAY/NIGHT rows
├── device_processor.py            # Per-device control dispatch
├── device_controller/             # Device-type controllers
│   ├── binary_device.py
│   ├── dimmable_light.py
│   ├── rules.py
│   └── vpd.py
├── relay_manager.py               # MCP23017 command projection
├── relay_board_state_manager.py   # Observed board state sampling
├── pid_controller.py              # PID implementation
├── pid_controller_manager.py      # PID lifecycle
├── vpd_controller.py              # VPD calculation
├── vpd_cascade_controller.py      # VPD-driven actuator selection
├── sensor_reader.py               # Redis state reads
├── setpoint_manager.py            # Effective setpoint authority
└── runtime_device_registry.py     # Immutable snapshot publisher
```

## One snapshot per tick

Every tick captures the current `RuntimeDeviceSnapshot` once. `Scheduler.bind_snapshot()` and `release_snapshot()` scope that snapshot to the tick via a context var, so no cross-version scheduling state leaks between ticks.

## Scheduler.install_snapshot()

`Scheduler.install_snapshot(snapshot)` atomically installs all projections from one immutable snapshot: mode parameters, light intensities, light programs, and device lookup. The `_ready` flag blocks ticks until the complete snapshot is installed. Partial caches are never visible to the control loop.

## VPD, PID, and device authority

- VPD is the master climate controller. Humidity and dehumidification derive from VPD error.
- PID runs only for heating, cooling, and CO2.
- Effective setpoints come from the active climate period (`climate_periods`) and the setpoint manager, not hardcoded values.
- Light intensity resolves from `light_programs` first, then photoperiod plus `light_target_intensity`, then 0% outside sun. A missing intensity anchor falls back to `MINIMUM_LIGHT_INTENSITY = 10.0`.

## Blocking I/O and hardware bypass

All hardware writes go through the relay manager and dimming drivers. Never use `sleep()`, blocking network calls, or direct I2C access inside the control loop. The loop delegates blocking work to `asyncio.to_thread` and relies on observed relay state for safety decisions.
