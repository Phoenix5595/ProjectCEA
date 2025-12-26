# Displaying Setpoints in Grafana Dashboards

This guide documents how the `temperature_rh_vpd_with_setpoints.json` panel shows setpoints with day/night schedule awareness and a DAY overlay.

## What the panel does
- Query A: sensor measurements (temperature, RH, VPD) from `measurement_with_metadata`.
- Query B: temperature setpoint for `location = 'Flower Room'`, `cluster = 'main'`, switching between DAY and NIGHT based on schedules.
- Query C: VPD setpoint for the same room/cluster and schedule logic.
- Query D: DAY overlay (yellow fill) so the active daylight period is visually highlighted.
- Modes supported: `DAY` and `NIGHT` only; `NULL`/`TRANSITION` are ignored.

## How the setpoint queries work
1. Build `time_points` with `generate_series($__timeFrom()::timestamp, $__timeTo()::timestamp, INTERVAL '5 minute')`.
2. Compute `day_flag` from the `schedules` table:
   - `location = 'Flower Room'`, `cluster = 'main'`, `mode = 'DAY'`, `enabled = true`.
   - Optional `day_of_week` respected: `day_of_week IS NULL OR day_of_week = EXTRACT(DOW FROM tp.time)`.
   - Time window handles overnight spans via `tp.time::time` comparisons:
     - `start_time <= end_time`: simple between.
     - `start_time > end_time`: overnight, so `>= start_time OR < end_time`.
3. For each timestamp, pick the latest setpoint where `updated_at <= time`:
   - If `day_flag` is true, use the most recent `mode = 'DAY'` value.
   - Otherwise, use the most recent `mode = 'NIGHT'` value (default to night when no day schedule matches).
4. Results are dashed lines (red for temperature, blue for VPD). The overlay returns `100` during DAY and `NULL` otherwise and is styled as a translucent yellow fill.

## Table expectations
- `setpoints` (automation-service schema): `location`, `cluster` (here: `main`), `mode` (`DAY`/`NIGHT`), `temperature`, `vpd`, `updated_at`.
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

