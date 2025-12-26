# Displaying Setpoints in Grafana Dashboards

## Overview

This guide explains how to display temperature and VPD setpoints on Grafana graphs. Setpoints are stored in the `setpoints` table in the `cea_sensors` database (same database used by Grafana).

## Basic Approach

The panel JSON file `temperature_rh_vpd_with_setpoints.json` includes:
- **Query A**: Original sensor data (temperature, RH, VPD)
- **Query B**: Temperature setpoints for Back and Front clusters
- **Query C**: VPD setpoints for Back and Front clusters

## How It Works

The setpoint queries:
1. Generate a time series for the selected time range (every 5 minutes)
2. Query the most recent setpoint values from the `setpoints` table
3. Display them as constant horizontal reference lines (dashed)

## Current Limitations

The current implementation shows the **most recent setpoint** for the DAY mode (or NULL mode if DAY doesn't exist). This means:
- ✅ Works well if setpoints don't change during the time period
- ⚠️ May not reflect NIGHT mode setpoints if viewing night-time data
- ⚠️ Doesn't show historical setpoint changes

## Database Schema

The `setpoints` table structure:
- `location` (TEXT): e.g., "Flower Room"
- `cluster` (TEXT): e.g., "back", "front"
- `mode` (TEXT): "DAY", "NIGHT", "TRANSITION", or NULL (legacy)
- `temperature` (REAL): Temperature setpoint in Celsius
- `vpd` (REAL): VPD setpoint in kPa
- `updated_at` (TIMESTAMPTZ): When the setpoint was last updated

## Using the Panel

1. **Import the panel JSON** into your Grafana dashboard
2. **Adjust location/cluster** if needed:
   - The queries assume "Flower Room" with clusters "back" and "front"
   - Modify the `location` and `cluster` values in queries B and C if different

3. **Customize appearance**:
   - Setpoints are displayed as dashed lines
   - Colors: Red/Purple for temperature, Blue/Cyan for VPD
   - You can modify colors in the fieldConfig overrides

## Advanced: Historical Setpoint Changes

To show setpoints that change over time (e.g., DAY vs NIGHT), you would need to:

1. **Query setpoints for both DAY and NIGHT modes**
2. **Use schedule times** to determine which setpoint was active at each time
3. **Create a time-series** that switches between setpoints based on schedule

Example enhanced query (for temperature setpoint with DAY/NIGHT support):

```sql
WITH time_series AS (
  SELECT generate_series($__timeFrom()::timestamp, $__timeTo()::timestamp, INTERVAL '5 minute') AS time
),
day_schedule AS (
  SELECT start_time, end_time 
  FROM schedules 
  WHERE location = 'Flower Room' 
    AND cluster = 'back' 
    AND mode = 'DAY' 
    AND enabled = true
  LIMIT 1
),
night_schedule AS (
  SELECT start_time, end_time 
  FROM schedules 
  WHERE location = 'Flower Room' 
    AND cluster = 'back' 
    AND mode = 'NIGHT' 
    AND enabled = true
  LIMIT 1
),
day_setpoint AS (
  SELECT temperature AS value 
  FROM setpoints 
  WHERE location = 'Flower Room' 
    AND cluster = 'back' 
    AND mode = 'DAY'
  ORDER BY updated_at DESC 
  LIMIT 1
),
night_setpoint AS (
  SELECT temperature AS value 
  FROM setpoints 
  WHERE location = 'Flower Room' 
    AND cluster = 'back' 
    AND mode = 'NIGHT'
  ORDER BY updated_at DESC 
  LIMIT 1
)
SELECT 
  ts.time AS "time",
  'Temp Setpoint - Back' AS metric,
  CASE 
    WHEN EXTRACT(HOUR FROM ts.time) >= EXTRACT(HOUR FROM (SELECT start_time FROM day_schedule))
     AND EXTRACT(HOUR FROM ts.time) < EXTRACT(HOUR FROM (SELECT end_time FROM day_schedule))
    THEN (SELECT value FROM day_setpoint)
    ELSE (SELECT value FROM night_setpoint)
  END AS value
FROM time_series ts
WHERE (SELECT value FROM day_setpoint) IS NOT NULL 
   OR (SELECT value FROM night_setpoint) IS NOT NULL
```

**Note**: This is more complex and may need adjustment based on your specific schedule setup.

## Alternative: Using control_history

The `control_history` table records setpoint values with each control action. You could query this to see what setpoints were actually used:

```sql
SELECT 
  timestamp AS "time",
  'Temp Setpoint (from control)' AS metric,
  setpoint AS value
FROM control_history
WHERE location = 'Flower Room' 
  AND cluster = 'back'
  AND setpoint IS NOT NULL
  AND timestamp >= $__timeFrom()
  AND timestamp <= $__timeTo()
ORDER BY timestamp
```

This shows the setpoint values that were actually used during control actions, but may have gaps if there were no control actions during certain periods.

## Troubleshooting

1. **No setpoints showing**: 
   - Check that setpoints exist in the database: `SELECT * FROM setpoints WHERE location = 'Flower Room';`
   - Verify the location and cluster names match exactly

2. **Wrong setpoints showing**:
   - Check which mode is active: `SELECT * FROM setpoints WHERE location = 'Flower Room' AND cluster = 'back';`
   - The query prioritizes DAY mode, then NULL mode

3. **Performance issues**:
   - The `generate_series` creates many data points for long time ranges
   - Consider increasing the interval (e.g., `INTERVAL '10 minute'` or `INTERVAL '1 hour'`)

## Customization

To customize for different rooms/clusters, modify:
- `location = 'Flower Room'` → your room name
- `cluster = 'back'` or `cluster = 'front'` → your cluster name
- Colors in the fieldConfig overrides section
- Line styles (dash patterns, width) in the overrides

