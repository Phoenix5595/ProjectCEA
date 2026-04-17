# Grafana Requirements for CEA

## Data Source
- PostgreSQL with TimescaleDB enabled (checkbox on in Grafana).
- Host/DB/user/password must match the backend (`cea_sensors` / `cea_user`).
- Connection pooling (documented only): max open 100, max idle 100, max lifetime 4h, connection timeout 10s.

## Query Guidance
- Always include time filters (`$__timeFrom()`, `$__timeTo()`) to leverage chunk exclusion.
- Filter by `sensor_name` (uses `idx_sensor_name`) and avoid `SELECT *`.
- Use optimized views/functions:
  - `measurement_with_metadata` / `measurement_timeseries` for series data.
  - `latest_sensor_values` or `get_latest_by_pattern('%_suffix')` for “latest” panels instead of `MAX(time)` subqueries.

## Time-Range Rules
- Live/short (≤1h): query raw data only (no aggregation or caching).
- Medium (≥12h): prefer hourly continuous aggregates.
- Multi-day: prefer daily aggregates for trend panels.

## Dashboards
- Panels should pick the view/aggregate that matches their time range (raw for live, hourly for 12h+, daily for multi-day).
- Alert rules should use raw data for live sensitivity; aggregates are acceptable for historical summaries.
- **Room light intensity** (`effective_setpoints.effective_light_intensity`): always filter by **`device_name`** (`light_1`, `light_2`, …). Flower/Veg sector dashboards include a **“Light effective_setpoints — sample freshness”** table: age in seconds since the last row per light (alert if a fixture stops logging). Curves are fed by the **TimescaleDB** logger (throttled in the automation loop), not Redis `light:*` keys used for live UI.
- **Optional operator panel**: add a small table query with `SELECT DISTINCT device_name FROM effective_setpoints WHERE location = '$room' AND cluster = 'main' AND effective_light_intensity IS NOT NULL AND $__timeFilter(timestamp)` so actual DB names are visible if YAML/DB labels drift from `light_1`…`light_3`.

## Naming
- Use sensor display mappings from frontend when showing names; backend keys remain unchanged.

## Setpoint visualization (temperature/VPD panel)
- Use `effective_setpoints` table to display actual setpoint values being used (accounts for ramp transitions).
- Use day/night switching from the `schedules` table (`location='Flower Room'`, `cluster='main'`, `mode='DAY'` / `mode='NIGHT'`, `enabled=true`; honor `day_of_week` when present).
- Compare schedule windows with `tp.time::time` so overnight ranges are handled and syntax stays valid.
- Query `effective_heating_setpoint` and `effective_vpd_setpoint` columns from `effective_setpoints` table filtered by location, cluster, and time range.
- Filter by mode (DAY/NIGHT) based on schedule matching at each timestamp.
- Only `DAY` and `NIGHT` modes are considered; legacy `NULL`/`TRANSITION` values are ignored.
- Default to NIGHT when no DAY schedule matches; ensure a DAY overlay series returns `100` during day and `NULL` otherwise and is styled as a translucent yellow fill.

