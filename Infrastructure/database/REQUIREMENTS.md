# CEA Database Schema Requirements

## Overview

This document describes the normalized database schema for the CEA (Controlled Environment Agriculture) project. The schema implements a hierarchical structure for rooms, racks, devices, and sensors, with a unified time-series measurement table.

## Database Engine

- **PostgreSQL** (latest stable)
- **TimescaleDB** extension for time-series optimization

## Schema Structure

### Metadata Tables (Normalized Hierarchy)

1. **room**
   - `room_id` (PK, SERIAL)
   - `name` (TEXT, UNIQUE)
   - `target_vpd` (REAL, optional)
   - `target_temp` (REAL, optional)
   - `created_at` (TIMESTAMPTZ)

2. **rack**
   - `rack_id` (PK, SERIAL)
   - `room_id` (FK → room)
   - `name` (TEXT)
   - `created_at` (TIMESTAMPTZ)
   - UNIQUE(room_id, name)

3. **device**
   - `device_id` (PK, SERIAL)
   - `rack_id` (FK → rack, nullable)
   - `name` (TEXT)
   - `type` (TEXT)
   - `ip_address` (TEXT, optional)
   - `serial_number` (TEXT, optional)
   - `created_at` (TIMESTAMPTZ)

4. **sensor**
   - `sensor_id` (PK, SERIAL)
   - `device_id` (FK → device)
   - `name` (TEXT)
   - `unit` (TEXT)
   - `data_type` (TEXT)
   - `channel` (INTEGER, optional)
   - `calibration_offset` (REAL, optional, default 0.0)
   - `created_at` (TIMESTAMPTZ)
   - UNIQUE(device_id, name)

### Time-Series Table

**measurement** (TimescaleDB hypertable)
- `time` (TIMESTAMPTZ, PK)
- `sensor_id` (INTEGER, FK → sensor, PK)
- `value` (REAL)
- `status` (TEXT, optional)

**Indexes:**
- `(sensor_id, time DESC)` - Primary index for fast queries
- `(time DESC)` - Time index for chunking
- `(sensor_id)` - Sensor lookup index

**Compression:**
- Enabled on chunks older than 90 days
- Segment by `sensor_id` for optimal compression

**Continuous Aggregates:**
- `measurement_hourly`: Hourly min/max/avg per sensor
- `measurement_daily`: Daily min/max/avg per sensor

### Optional Tables

1. **crop_batch**
   - `batch_id` (PK, SERIAL)
   - `crop_name` (TEXT)
   - `start_date` (DATE)
   - `end_date` (DATE, optional)
   - `room_id` (FK → room)

2. **setpoints** (Created and managed by automation-service)
   - `id` (PK, BIGSERIAL)
   - `location` (TEXT) - e.g., "Flower Room"
   - `cluster` (TEXT) - e.g., "back", "front", "main"
   - `temperature` (REAL) - Temperature setpoint in Celsius
   - `humidity` (REAL) - Humidity setpoint in percent
   - `co2` (REAL) - CO2 setpoint in ppm
   - `vpd` (REAL) - VPD setpoint in kPa
   - `mode` (TEXT) - "DAY", "NIGHT", "TRANSITION", or NULL (legacy/default)
   - `updated_at` (TIMESTAMPTZ) - Last update timestamp
   - UNIQUE(location, cluster, mode)
   
   **Note**: This table is created automatically by the automation-service on startup. 
   See `Infrastructure/database/SETPOINTS_TABLE_EXPLANATION.md` for details.

3. **actuator_events**
   - `event_id` (PK, SERIAL)
   - `device_id` (FK → device)
   - `action` (TEXT)
   - `value` (REAL, optional)
   - `time` (TIMESTAMPTZ)
   - Hypertable for time-series optimization

## Performance Requirements

- **Ingestion Rate**: 50 sensors × 1 sample/second = 4.3M datapoints/day
- **Retention**: 90 days raw data, then compress
- **Compression**: Automatic compression on chunks older than 90 days
- **Query Performance**: 
  - Live/short (≤1 hour): Query raw `measurement` / `measurement_with_metadata`
  - Medium (≥12 hours): Use hourly continuous aggregates
  - Multi-day ranges: Use daily continuous aggregates
  - Historical data (> 90 days): Prefer aggregates; compressed chunks stay readable

## Migration

### From can_messages to Normalized Schema

The migration script (`migrate_to_normalized_schema.py`) performs:

1. **Metadata Creation**:
   - Creates rooms from node_id mapping:
     - node_id 1 → "Flower Room" (back)
     - node_id 2 → "Flower Room" (front)
     - node_id 3 → "Veg Room" (main)
     - node_id 4 → "Lab" (main)
     - node_id 5 → "Outside" (main)
   - Creates devices: One per unique node_id
   - Creates sensors: One per unique sensor name pattern

2. **Data Migration**:
   - Extracts sensor values from `can_messages.decoded_data` JSONB
   - Inserts into `measurement` table with proper sensor_id references
   - Preserves all timestamps and values

3. **Verification**:
   - Row counts
   - Value ranges
   - Timestamp ranges

## API Endpoints

### POST /api/v1/measurement

Ingest a new measurement.

**Request:**
```json
{
  "time": "2024-01-15T10:30:00Z",
  "sensor_id": 123,
  "value": 23.5,
  "status": "ok"
}
```

**Response:** 201 Created
```json
{
  "time": "2024-01-15T10:30:00Z",
  "sensor_id": 123,
  "value": 23.5,
  "status": "ok",
  "message": "Measurement recorded for sensor dry_bulb_b (ID: 123)"
}
```

### GET /api/v1/measurement/sensor/{sensor_id}

Get measurements for a specific sensor.

**Query Parameters:**
- `start_time` (optional): ISO 8601 timestamp
- `end_time` (optional): ISO 8601 timestamp
- `limit` (optional, default: 100, max: 1000)

## Grafana Integration

### Query Examples

**Recent Data (< 90 days):**
```sql
SELECT 
    m.time,
    s.name as sensor_name,
    r.name as room_name,
    m.value
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id
WHERE r.name = 'Flower Room'
AND m.time > NOW() - INTERVAL '24 hours'
ORDER BY m.time DESC;
```

**Historical Data (> 90 days) - Use Aggregates:**
```sql
SELECT 
    md.time,
    s.name as sensor_name,
    r.name as room_name,
    md.avg_value as value
FROM measurement_daily md
JOIN sensor s ON md.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id
WHERE r.name = 'Flower Room'
AND md.time > NOW() - INTERVAL '30 days'
ORDER BY md.time DESC;
```

## Monitoring

### Monitoring Scripts

- `monitor_can_processor.sh` - Monitor CAN processor service, CAN bus interface, Redis stream, and recent CAN messages
- `monitor_redis_stream.sh` - Monitor Redis stream (`sensor:raw`) and display live sensor values

### Backpressure Monitoring

- Stream length > 5000: Warning logged
- Monitor pending message count
- Monitor ingestion rate (measurements/second)

## Files

- `Infrastructure/database/cea_schema.sql` - Schema creation script

## Weather Sensors

The weather service collects data from YUL Airport (CYUL) via Aviation Weather Center METAR API and stores it in the "Outside" room.

### Weather Sensors

The following sensors are created under the "Weather Station YUL" device:

- `outside_temp` - Outside temperature (°C)
- `outside_rh` - Outside relative humidity (%)
- `outside_pressure` - Atmospheric pressure (hPa)
- `outside_wind_speed` - Wind speed (m/s)
- `outside_wind_direction` - Wind direction (degrees, 0-360)
- `outside_precipitation` - Precipitation (mm, may be null if no precipitation)

### Data Source

- **Service**: `weather-service` (port 8003)
- **API**: Aviation Weather Center METAR API
- **Station**: CYUL (Montréal-Pierre Elliott Trudeau International Airport)
- **Poll Interval**: 15 minutes
- **Room**: "Outside"
- **Device**: "Weather Station YUL" (rack_id is NULL)

## Autostart Configuration

Run `enable_autostart.sh` to enable all services for boot autostart:

```bash
./enable_autostart.sh
```

Services enabled:
- redis-server
- postgresql
- can-setup
- can-processor
- cea-backend
- automation-service
- weather-service





