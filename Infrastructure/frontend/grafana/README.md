# Grafana Setup for CEA Sensors

Complete guide for setting up and using Grafana to visualize sensor data from the CEA project.

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Database Schema](#database-schema)
4. [Query Examples](#query-examples)
5. [Importing Dashboards](#importing-dashboards)
6. [Troubleshooting](#troubleshooting)

---

## Installation

### On Raspberry Pi

```bash
# Add Grafana repository
sudo apt-get install -y software-properties-common
sudo add-apt-repository "deb https://packages.grafana.com/oss/deb stable main"
wget -q -O - https://packages.grafana.com/gpg.key | sudo apt-key add -

# Install Grafana
sudo apt-get update
sudo apt-get install grafana

# Enable and start Grafana
sudo systemctl enable grafana-server
sudo systemctl start grafana-server
```

### Verify Installation

```bash
# Check status
sudo systemctl status grafana-server

# Access Grafana
# Default URL: http://localhost:3000
# Default credentials: admin / admin (change on first login)
```

---

## Configuration

### Step 1: Add PostgreSQL Data Source

1. Open Grafana (usually at `http://localhost:3000`)
2. Login with admin/admin (change password on first login)
3. Go to **Configuration** → **Data Sources** → **Add data source**
4. Select **PostgreSQL**
5. Configure:
   - **Name**: `CEA Sensors`
   - **Host**: `localhost:5432` (or your PostgreSQL host)
   - **Database**: `cea_sensors`
   - **User**: `cea_user`
   - **Password**: (your database password)
   - **SSL Mode**: `disable` (or `require` for production)
   - **TimescaleDB**: Enable (checkbox)
6. Click **Save & Test** - should show "Data source is working"

### Performance Quick Rules
- Use raw data for live/short panels (≤1h); do not aggregate or cache.
- For ≥12h ranges, prefer hourly continuous aggregates; for multi-day ranges, prefer daily aggregates.
- Always include `$__timeFrom()` / `$__timeTo()` and filter by `sensor_name` to hit indexes.
- For “latest” values, use `latest_sensor_values` view or `get_latest_by_pattern()` instead of `MAX(time)` subqueries.
- Keep datasource pooling at: max open 100, max idle 100, max lifetime 4h, connection timeout 10s (documented here; change in Grafana if needed).

### Step 2: Create Your First Panel

1. Create a new dashboard or open an existing one
2. Click **Add panel** → **Add visualization**
3. Select data source: `CEA Sensors`
4. **Switch to Raw SQL mode** (click "Code" or "Raw SQL" button)
5. Use one of the example queries below

---

## Database Schema

The database uses a **fully normalized schema** with metadata tables and a unified `measurement` hypertable for all sensor readings.

### Schema Overview

#### Metadata Tables (Hierarchy)
- `room` → `rack` → `device` → `sensor` → `measurement`

#### Time-Series Table
- **`measurement`**: Unified hypertable for all sensor readings
  - Columns: `time`, `sensor_id`, `value`, `status`
  - Indexed on `(sensor_id, time DESC)` for fast queries
  - Compression enabled for data older than 90 days

#### Helper View
- **`measurement_with_metadata`**: Pre-joined view for easy querying
  - Includes: `time`, `sensor_name`, `sensor_unit`, `device_name`, `room_name`, `value`, `status`
  - **Use this view for most Grafana queries!**

#### Continuous Aggregates
- **`measurement_hourly`**: Hourly min/max/avg per sensor (if created)
- **`measurement_daily`**: Daily min/max/avg per sensor (if created)

### Using the Normalized Schema

**Recommended: Use `measurement_with_metadata` View**

This view automatically joins all metadata, making queries simple:

```sql
SELECT 
    time,
    sensor_name,
    value
FROM measurement_with_metadata
WHERE sensor_name = 'dry_bulb_b'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time;
```

---

## Query Examples

### Temperature (Dry Bulb + Wet Bulb)

```sql
SELECT 
    time,
    sensor_name,
    value
FROM measurement_with_metadata
WHERE sensor_name IN ('dry_bulb_b', 'wet_bulb_b')
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time, sensor_name;
```

**Settings:**
- Format: **Time series**
- Unit: **Celsius (°C)**

**Grafana will automatically create separate series for each sensor_name!**

### RH & VPD (Calculated)

```sql
SELECT 
    time,
    sensor_name,
    value
FROM measurement_with_metadata
WHERE sensor_name IN ('rh_b', 'vpd_b')
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time, sensor_name;
```

**Settings:**
- Format: **Time series**
- Unit: **Percent (%)** for RH, **Kilopascal (kPa)** for VPD

### CO2 Levels (All Locations)

```sql
SELECT 
    time,
    sensor_name,
    value
FROM measurement_with_metadata
WHERE sensor_name LIKE 'co2_%'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time, sensor_name;
```

**Settings:**
- Format: **Time series**
- Unit: **Parts per million (ppm)**

### Pressure

```sql
SELECT 
    time,
    sensor_name,
    value
FROM measurement_with_metadata
WHERE sensor_name = 'pressure_b'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time;
```

**Settings:**
- Format: **Time series**
- Unit: **Hectopascal (hPa)**

### Secondary RH (from SCD30)

```sql
SELECT 
    time,
    value
FROM measurement_with_metadata
WHERE sensor_name = 'secondary_rh_b'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time;
```

**Settings:**
- Format: **Time series**
- Unit: **Percent (%)**

### Water Level

```sql
SELECT 
    time,
    sensor_name,
    value
FROM measurement_with_metadata
WHERE sensor_name LIKE 'water_level_%'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time, sensor_name;
```

**Settings:**
- Format: **Time series**
- Unit: **Millimeters (mm)**

### Outside Weather (YUL Airport)

#### Temperature and Humidity

```sql
SELECT 
    time,
    sensor_name,
    value
FROM measurement_with_metadata
WHERE sensor_name IN ('outside_temp', 'outside_rh')
  AND room_name = 'Outside'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time, sensor_name;
```

**Settings:**
- Format: **Time series**
- Unit: **Celsius (°C)** for temperature, **Percent (%)** for humidity

#### Atmospheric Pressure

```sql
SELECT 
    time,
    value
FROM measurement_with_metadata
WHERE sensor_name = 'outside_pressure'
  AND room_name = 'Outside'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time;
```

**Settings:**
- Format: **Time series**
- Unit: **Hectopascal (hPa)**

#### Wind Speed and Direction

```sql
SELECT 
    time,
    sensor_name,
    value
FROM measurement_with_metadata
WHERE sensor_name IN ('outside_wind_speed', 'outside_wind_direction')
  AND room_name = 'Outside'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time, sensor_name;
```

**Settings:**
- Format: **Time series**
- Unit: **Meters per second (m/s)** for wind speed, **Degrees** for wind direction

#### Precipitation

```sql
SELECT 
    time,
    value
FROM measurement_with_metadata
WHERE sensor_name = 'outside_precipitation'
  AND room_name = 'Outside'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time;
```

**Settings:**
- Format: **Time series**
- Unit: **Millimeters (mm)**
- Note: Precipitation may be NULL if no precipitation occurred

### Filter by Room

```sql
SELECT 
    time,
    sensor_name,
    value
FROM measurement_with_metadata
WHERE room_name = 'Flower Room'
  AND sensor_name LIKE 'dry_bulb_%'
  AND time > $__timeFrom()
  AND time < $__timeTo()
ORDER BY time, sensor_name;
```

### Hourly Aggregations (For Longer Time Ranges)

For queries showing >24 hours, use `time_bucket` for better performance:

```sql
SELECT 
    time_bucket('1 hour', time) as time,
    sensor_name,
    AVG(value) as avg_value,
    MIN(value) as min_value,
    MAX(value) as max_value
FROM measurement_with_metadata
WHERE sensor_name = 'dry_bulb_b'
  AND time > $__timeFrom()
  AND time < $__timeTo()
GROUP BY time_bucket('1 hour', time), sensor_name
ORDER BY time;
```

**When to use aggregations:**
- **< 24 hours**: Query raw data from `measurement_with_metadata`
- **1-7 days**: Use hourly aggregation with `time_bucket('1 hour', ...)`
- **> 7 days**: Use daily aggregation with `time_bucket('1 day', ...)**

### Using Continuous Aggregates (If Available)

If continuous aggregates are created, use them for even better performance:

```sql
SELECT 
    m.time_bucket as time,
    s.name as sensor_name,
    m.avg_value,
    m.min_value,
    m.max_value
FROM measurement_hourly m
JOIN sensor s ON m.sensor_id = s.sensor_id
WHERE s.name = 'dry_bulb_b'
  AND m.time_bucket > $__timeFrom()
  AND m.time_bucket < $__timeTo()
ORDER BY m.time_bucket;
```

---

## Performance Optimization

### Optimized Data Source Settings

For best performance with 1-second refresh rate, configure your Grafana PostgreSQL data source with these settings:

**Connection Settings:**
- **Host**: `localhost:5432`
- **Database**: `cea_sensors`
- **User**: `cea_user`
- **Password**: (your password)
- **SSL Mode**: `disable` (or `require` for production)
- **TimescaleDB**: Enable (checkbox)

**Connection Pooling (Critical for Performance):**
- **Max open connections**: `100`
- **Max idle connections**: `100`
- **Max connection lifetime**: `14400` (4 hours)
- **Connection timeout**: `10` seconds

These settings allow Grafana to maintain a connection pool, reducing connection overhead and improving query performance.

### Query Optimization Best Practices

**1. Use DISTINCT ON instead of MAX(time) subqueries**

❌ **Slow** (uses MAX with GROUP BY):
```sql
SELECT sensor_id, MAX(time) as max_time 
FROM measurement 
WHERE time >= NOW() - INTERVAL '10 minutes' 
GROUP BY sensor_id
```

✅ **Fast** (uses DISTINCT ON):
```sql
SELECT DISTINCT ON (sensor_id) sensor_id, time, value
FROM measurement
WHERE time >= NOW() - INTERVAL '10 minutes'
ORDER BY sensor_id, time DESC
```

**2. Filter by sensor_name using the index**

The `sensor.name` column is indexed, so filtering by `sensor_name` in `measurement_with_metadata` is fast:
```sql
SELECT time, sensor_name, value
FROM measurement_with_metadata
WHERE sensor_name = 'dry_bulb_b'
  AND time >= $__timeFrom()
  AND time <= $__timeTo()
ORDER BY time;
```

**3. Use time range filters early**

Always include time range filters (`$__timeFrom()` and `$__timeTo()`) in WHERE clauses to leverage TimescaleDB chunk exclusion:
```sql
-- Good: Time filter in WHERE clause
WHERE time >= $__timeFrom() AND time <= $__timeTo()

-- Bad: Time filter in subquery only
WHERE sensor_name = 'dry_bulb_b'  -- Missing time filter!
```

**4. For latest values, limit time window**

When getting latest values, use a recent time window (e.g., last 10 minutes) instead of full time range:
```sql
WHERE time >= NOW() - INTERVAL '10 minutes'
```

**5. Use continuous aggregates for longer time ranges**

- **< 24 hours**: Query `measurement_with_metadata` (raw data)
- **1-7 days**: Consider using `measurement_hourly` continuous aggregate
- **> 7 days**: Use `measurement_daily` continuous aggregate

### Performance Verification

To verify query performance, use `EXPLAIN ANALYZE`:

```sql
EXPLAIN ANALYZE
SELECT time, sensor_name, value
FROM measurement_with_metadata
WHERE sensor_name = 'dry_bulb_b'
  AND time >= NOW() - INTERVAL '1 hour'
  AND time <= NOW()
ORDER BY time;
```

Look for:
- **Index usage**: Should see "Index Scan" on `idx_sensor_name` and `idx_measurement_sensor_time`
- **Execution time**: Should be < 100ms for recent data queries
- **Rows examined**: Should be minimal (TimescaleDB chunk exclusion)

### Database Indexes

The following indexes are created for optimal performance:
- `idx_sensor_name` on `sensor(name)` - Fast sensor_name filtering
- `idx_measurement_sensor_time` on `measurement(sensor_id, time DESC)` - Fast time-series queries
- Foreign key indexes on `rack(room_id)`, `device(rack_id)` - Fast JOINs

### PostgreSQL Configuration

Ensure PostgreSQL is configured for time-series workloads. See `Infrastructure/database/timescaledb_config.sql` for recommended settings:
- `work_mem = 64MB` - For better aggregation performance
- `shared_buffers = 256MB` - Cache frequently accessed data
- `effective_cache_size = 1GB` - Help query planner make better decisions

---

## Importing Dashboards

### Method 1: Via Grafana UI (Recommended)

1. Open Grafana in your browser (usually `http://localhost:3000`)
2. Click **Dashboards** → **Import** (or click the **+** icon → **Import**)
3. Click **Upload JSON file**
4. Navigate to: `Infrastructure/frontend/grafana/dashboards/cea_sensors_example.json`
5. Select the file and click **Load**
6. Configure:
   - **Name**: "CEA Sensors Dashboard" (or your choice)
   - **Folder**: (optional)
   - **Data source**: Select **"CEA Sensors"** (your PostgreSQL data source)
7. Click **Import**

### Method 2: Via Command Line Script

Use the provided import script:

```bash
cd "/home/antoine/Project CEA"
./Infrastructure/frontend/grafana/import_dashboard.sh
```

Or with custom Grafana URL and password:

```bash
./Infrastructure/frontend/grafana/import_dashboard.sh http://your-grafana-url:3000 your_password
```

The script will:
- Test Grafana connection
- Import the dashboard
- Display the dashboard URL

The dashboard includes:
- Temperature panels (dry bulb, wet bulb)
- CO2 levels
- RH & VPD
- Pressure
- Secondary RH
- Water level
- Hourly aggregations

---

## Troubleshooting

### Dashboard Import Issues

#### Issue: Dashboard imports but panels don't show / "Add visualization" does nothing

**Solution 1: Try the Simple Test Dashboard First**

Import the simple test dashboard to verify your setup works:

1. Go to Grafana → Dashboards → Import
2. Upload: `Infrastructure/frontend/grafana/dashboards/cea_sensors_simple.json`
3. Select your data source
4. Click Import

If this works, then the main dashboard should work too.

**Solution 2: Check Browser Console for Errors**

1. Open browser Developer Tools (F12)
2. Go to Console tab
3. Try importing the dashboard
4. Look for red error messages
5. Common errors:
   - "Datasource not found" → Check data source name
   - "Invalid query" → Check SQL syntax
   - "Permission denied" → Check database user permissions

**Solution 3: Verify Data Source Connection**

1. Go to Configuration → Data Sources
2. Click on your PostgreSQL data source
3. Click "Test" button
4. Should show "Data source is working"
5. If not, check:
   - Host: `localhost:5432`
   - Database: `cea_sensors`
   - User: `cea_user`
   - Password: (your password)
   - SSL Mode: `disable` (for local)

**Solution 4: Manual Panel Creation (If Import Fails)**

If import doesn't work, create panels manually:

1. Create new dashboard: Dashboards → New → New Dashboard
2. Click "Add visualization"
3. Select your PostgreSQL data source
4. Click "Code" or "Raw SQL" button
5. Paste this query:

```sql
SELECT time, sensor_name, value 
FROM measurement_with_metadata 
WHERE sensor_name = 'dry_bulb_b' 
AND time >= $__timeFrom() 
AND time <= $__timeTo() 
ORDER BY time
```

6. Set Format: **Time series**
7. Click "Run query"
8. Should see data!

**Solution 5: Check Time Range**

After importing:
1. Check time range (top right corner)
2. Set to "Last 6 hours" or "Last 1 hour"
3. Your data goes back to Dec 2, so "Last 7 days" will show more

### Connection Issues

#### Cannot connect to database

- Check PostgreSQL is running: `sudo systemctl status postgresql`
- Verify credentials in Grafana data source settings
- Test connection: `psql -h localhost -U cea_user -d cea_sensors`
- Check pg_hba.conf if connection denied: `sudo cat /etc/postgresql/*/main/pg_hba.conf`

### Data Issues

#### No data showing

- Check CAN processor is running: `sudo systemctl status can-processor.service`
- Verify data exists: `SELECT COUNT(*) FROM measurement;`
- Check time range in dashboard (top right corner)
- Make sure you're using `$__timeFrom()` and `$__timeTo()` in your SQL queries
- Verify sensor names: `SELECT DISTINCT name FROM sensor ORDER BY name;`
- Test if data is accessible:
  ```bash
  psql -h localhost -U cea_user -d cea_sensors -c "SELECT COUNT(*) FROM measurement WHERE time > NOW() - INTERVAL '1 hour';"
  ```
  Should return a number > 0.

#### "No data" in panels

- Time range too narrow (try "Last 6 hours")
- Data source not selected correctly
- Query syntax error (check browser console)

#### "Can't find sensor"

- List all sensors: `SELECT name, unit FROM sensor ORDER BY name;`
- Check sensor exists: `SELECT * FROM sensor WHERE name = 'dry_bulb_b';`
- Verify sensor names match exactly (case-sensitive)

#### "relation does not exist" errors

- Make sure you're using `measurement_with_metadata` view (not old table names)
- Verify schema exists: `SELECT * FROM measurement_with_metadata LIMIT 1;`

### Performance Issues

#### Slow queries

- **For < 24 hours**: Use raw `measurement_with_metadata` view
- **For 1-7 days**: Use hourly aggregation with `time_bucket('1 hour', ...)`
- **For > 7 days**: Use daily aggregation with `time_bucket('1 day', ...)`
- Make sure you're filtering by `time` range first (uses indexes)
- Use continuous aggregates for even better performance (if available)

### Common Issues Summary

**Dashboard imports but is blank:**
- Check browser console for errors
- Verify data source exists
- Try the simple test dashboard first

**"Add visualization" does nothing:**
- This usually means the dashboard imported but panels aren't rendering
- Check browser console for JavaScript errors
- Try refreshing the page
- Try creating a new panel manually to test

---

## Sensor Name Reference

Common sensor names in the system:

- **Temperature**: `dry_bulb_b`, `dry_bulb_f`, `wet_bulb_b`, `wet_bulb_f`, `secondary_temp_b`, `secondary_temp_f`
- **Humidity**: `rh_b`, `rh_f`, `secondary_rh_b`, `secondary_rh_f`
- **VPD**: `vpd_b`, `vpd_f`
- **CO2**: `co2_b`, `co2_f`
- **Pressure**: `pressure_b`, `pressure_f`
- **Water Level**: `water_level_b`, `water_level_f`
- **Outside Weather**: `outside_temp`, `outside_rh`, `outside_pressure`, `outside_wind_speed`, `outside_wind_direction`, `outside_precipitation`

Suffixes:
- `_b` = Back location
- `_f` = Front location
- `_v` = Veg room
- No suffix = Default location
- `outside_` = Outside weather station (YUL Airport)

---

## Performance Optimization

The new schema is optimized with:
- ✅ **Hypertable** for efficient time-series storage
- ✅ **Composite indexes** on `(sensor_id, time DESC)` for fast queries
- ✅ **Compression** for data older than 90 days (70% storage savings)
- ✅ **Continuous aggregates** for faster aggregations (if enabled)
- ✅ **Pre-joined view** (`measurement_with_metadata`) for convenience

---

## Migration Notes

The old normalized tables (`temperature_readings`, `climate_readings`, etc.) are **deprecated**. All new data is written to the unified `measurement` table.

The `can_messages` table is still available for raw message logging if needed, but `measurement` is the primary data source for Grafana.

**Important**: Always use `measurement_with_metadata` view for Grafana queries, not the old `can_messages` table with JSONB columns.

---

## Dashboard Panels

Recommended panels:

1. **Temperature Panel**: Dry bulb and wet bulb temperatures
2. **CO2 Panel**: CO2 levels over time
3. **Humidity Panel**: RH and VPD
4. **Pressure Panel**: Atmospheric pressure
5. **Water Level Panel**: Water level measurements
6. **Outside Weather Panel**: Outside temperature, humidity, pressure, wind
7. **Multi-Node Comparison**: Compare all nodes side-by-side

---

## Alerting

Grafana provides built-in alerting capabilities for email and push notifications.

### Quick Start

1. **Configure Notification Channels**:
   - Go to **Alerting** → **Notification channels** → **Add channel**
   - Set up email notifications (SMTP) or push notifications (webhooks)

2. **Import Alert Rules**:
   - Alert rule templates are available in `Infrastructure/frontend/grafana/alerting/alert-rules/`
   - Import via Grafana UI or create manually

3. **Complete Setup Guide**:
   - See `Infrastructure/frontend/grafana/alerting/README.md` for detailed instructions

### Available Alert Rules

- **Temperature Alerts**: Dry bulb, wet bulb, secondary temperature (high/low thresholds)
- **Humidity Alerts**: Secondary RH, Lab RH (high/low thresholds)
- **Water Level Alerts**: All water level sensors (high threshold - tank empty)

### Thresholds

Based on previous alarm system configuration:
- **Temperature**: Min 10°C, Max 35°C (dry bulb/secondary), Max 25°C (wet bulb)
- **Humidity**: Min 10%, Max 90%
- **Water Level**: Max 200mm (distance from sensor to water surface)

---

## Next Steps

- Customize panels to your needs
- Set up alerts for threshold violations (see `alerting/README.md`)
- Create additional dashboards for specific rooms or sensors
- Set up automated reports

---

## Service Configuration

See `Infrastructure/frontend/grafana.service` for systemd service file.
