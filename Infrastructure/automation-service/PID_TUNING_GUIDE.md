# PID Tuning Guide

This guide covers tuning the PID controllers used by the automation service. PID parameters live in PostgreSQL (written through the `pid_parameters` table and `pid_control_modes` table) and are cached in Redis for the 1-second control loop.

## What supports PID today

The controller creates PID controllers only for these `device_type` values (`app/control/pid_controller_manager.py`):

- `heating` (e.g., room heaters)
- `cooling` (e.g., exhaust fans used for cooling)
- `co2` (e.g., CO2 enrichment)

Other devices such as humidifiers, dehumidifiers, and fans default to ON/OFF or hysteresis control via `pid_control_modes`.

## Default parameters from config

`automation_config.yaml` provides the starting PID values:

| Device type | Kp | Ki | Kd |
|-------------|----|----|----|
| Heater | 25.0 | 0.02 | 0.0 |
| Cooling (fan) | 25.0 | 0.02 | 0.0 |
| CO2 | 10.0 | 0.01 | 0.0 |

These defaults are loaded when no row exists in the database for a given location/cluster/device type. They are starting points, not guaranteed values for every room.

## Validation ranges

`automation_config.yaml` enforces these limits at startup:

| Device type | Kp | Ki | Kd |
|-------------|----|----|----|
| Heater / Fan | 0.0–100.0 | 0.0–1.0 | 0.0–10.0 |
| CO2 | 0.0–50.0 | 0.0–0.5 | 0.0–5.0 |

Values outside these ranges are rejected by `app/models/config_schema.py`.

## How to read or change parameters

Use the automation service CLI (`config_cli.py`) or the `/api/pid/...` endpoints. The CLI writes to PostgreSQL and logs a change reason to the `config_versions` table.

```bash
# Read current parameters
./config_cli.py pid get heater --location "Flower Room" --cluster main

# Set new parameters (dry-run first)
./config_cli.py pid set heater --kp 22.0 --ki 0.02 --kd 0.0 --dry-run
```

Restart the service after changing YAML-owned limits or defaults; runtime PID gains take effect as soon as the database row is updated.

## Basic tuning approach

1. Start with the defaults from `automation_config.yaml`.
2. Set Ki and Kd to 0. Raise Kp until the process responds quickly without sustained oscillation, then reduce Kp by about 20–30%.
3. Add Ki in small steps (for heaters, try 0.01–0.05) to remove steady-state offset. Stop if oscillation appears.
4. Add Kd only if overshoot remains. Most thermal processes do not need Kd because thermal mass already damps the response.
5. Wait several minutes between changes; thermal loops are slow.

## Common symptoms

| Symptom | Try |
|---------|-----|
| Oscillation | Reduce Kp 20–30%, reduce Ki 50% |
| Slow response | Increase Kp 20–30% |
| Steady-state offset | Increase Ki slightly |
| Overshoot | Reduce Kp 10–20%, add small Kd |

## Safety context

- The control loop runs every `control.update_interval` second (`automation_config.yaml` sets 1 s; valid range is 1–5 s).
- Safety limits (`safety_limits` in `automation_config.yaml`) are enforced separately from PID tuning.
- The global heating↔exhaust interlock is currently removed; any safety rules must be configured explicitly.

See `REQUIREMENTS.md` for the climate control contract and `app/control/AGENTS.md` for the control-loop rules.
