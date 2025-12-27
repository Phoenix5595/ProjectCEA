# Displaying Setpoints in Grafana Dashboards

This guide documents how the `temperature_rh_vpd_with_setpoints.json` panel shows setpoints with day/night schedule awareness and a DAY overlay.

## What the panel does
- Query A: sensor measurements (temperature, RH, VPD) from `measurement_with_metadata`.
- Query B: effective heating setpoint for `location = 'Flower Room'`, `cluster = 'main'`, switching between DAY and NIGHT based on schedules. Uses `effective_setpoints` table which contains the actual setpoint values being used (accounting for ramp transitions).
- Query C: VPD setpoint for the same room/cluster and schedule logic. Uses `setpoint_history` table (VPD setpoints are not tracked in `effective_setpoints`).
- Query D: DAY overlay (yellow fill) so the active daylight period is visually highlighted.
- Modes supported: `DAY`, `NIGHT`, `PRE_DAY`, `PRE_NIGHT`; `NULL`/`TRANSITION` are ignored.

## How the setpoint queries work

### Effective Setpoints (Heating/Cooling)
The effective setpoint queries use the `effective_setpoints` table which logs the actual setpoint values being used at every control step, accounting for ramp transitions:

1. Query `effective_setpoints` table filtered by location, cluster, and time range.
2. Filter by mode (DAY/NIGHT) based on schedule matching:
   - Check if timestamp falls within an active DAY schedule period.
   - If yes, use rows where `mode = 'DAY'`.
   - If no, use rows where `mode = 'NIGHT'`.
3. The `effective_heating_setpoint` and `effective_cooling_setpoint` columns contain the actual values being sent to controllers (may differ from nominal during ramps).
4. The `nominal_heating_setpoint` and `nominal_cooling_setpoint` columns contain the target values from the `setpoints` table (for reference).

### VPD Setpoints
VPD setpoints use the `setpoint_history` table (legacy) since VPD is not tracked in `effective_setpoints`:

1. Query `setpoint_history` table filtered by location, cluster, and time range.
2. Filter by mode based on schedule matching (same logic as above).
3. Results are dashed lines (red for temperature, blue for VPD). The overlay returns `100` during DAY and `NULL` otherwise and is styled as a translucent yellow fill.

### Key Differences
- **Nominal setpoints** (`setpoints` table): Target values configured by user, unchanged during ramps.
- **Effective setpoints** (`effective_setpoints` table): Actual values being used, account for ramp transitions, logged at every control step.
- **Ramp progress**: Tracked in `ramp_progress_heating` and `ramp_progress_cooling` columns (0.0-1.0, NULL when not ramping).

## Table expectations
- `setpoints` (automation-service schema): `location`, `cluster` (here: `main`), `mode` (`DAY`/`NIGHT`/`PRE_DAY`/`PRE_NIGHT`), `heating_setpoint`, `cooling_setpoint`, `vpd`, `updated_at`. Contains nominal (target) setpoints.
- `effective_setpoints` (automation-service schema): `location`, `cluster`, `mode`, `effective_heating_setpoint`, `effective_cooling_setpoint`, `nominal_heating_setpoint`, `nominal_cooling_setpoint`, `ramp_progress_heating`, `ramp_progress_cooling`, `timestamp`. Contains actual setpoints being used (accounting for ramps), logged at every control step.
- `setpoint_history` (legacy): Historical setpoint changes, used for VPD setpoints and backward compatibility.
- `schedules`: `location`, `cluster`, `mode` (`DAY`/`NIGHT`), `enabled`, `start_time`, `end_time`, optional `day_of_week`.

## Using or adapting the panel
- If your room/cluster differ, replace `'Flower Room'` / `'main'` in queries B/C/D.
- Increase/decrease the interval in `generate_series` for coarser or finer stepping.
- Keep the `tp.time::time` casts to avoid SQL syntax errors and to correctly support overnight day windows.
- Ensure schedules cover the day you are viewing; when no DAY schedule matches, the queries fall back to NIGHT setpoints.

## Troubleshooting
- No setpoint lines: confirm DAY/NIGHT rows exist in `setpoints` for `location='Flower Room'` and `cluster='main'`; verify schedules are enabled and times cover the range.
- Wrong value: check `updated_at` ordering and `mode` on the latest setpoints; only DAY/NIGHT are considered.
- Syntax error near `tp`: verify comparisons use `tp.time::time` (already applied in the panel JSON).

