-- CEA Normalized Database Schema
-- PostgreSQL + TimescaleDB schema with metadata tables and unified measurement hypertable
-- 
-- This schema implements a normalized structure:
-- room -> rack -> device -> sensor -> measurement
--
-- Created: 2024
-- Database: cea_sensors

-- ============================================
-- Enable TimescaleDB Extension
-- ============================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================
-- Metadata Tables (Normalized Hierarchy)
-- ============================================

-- Room: Top-level rooms
CREATE TABLE IF NOT EXISTS room (
    room_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    target_vpd REAL,
    target_temp REAL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Rack: Racks within rooms (optional)
CREATE TABLE IF NOT EXISTS rack (
    rack_id SERIAL PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES room(room_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(room_id, name)
);

-- Device: Devices (CAN nodes, sensors, actuators)
CREATE TABLE IF NOT EXISTS device (
    device_id SERIAL PRIMARY KEY,
    rack_id INTEGER REFERENCES rack(rack_id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    ip_address TEXT,
    serial_number TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Sensor: Individual sensors
CREATE TABLE IF NOT EXISTS sensor (
    sensor_id SERIAL PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    data_type TEXT NOT NULL,
    channel INTEGER,
    calibration_offset REAL DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(device_id, name)
);

-- ============================================
-- Time-Series Table (Hypertable)
-- ============================================

-- Measurement: Unified time-series table for all sensor readings
CREATE TABLE IF NOT EXISTS measurement (
    time TIMESTAMPTZ NOT NULL,
    sensor_id INTEGER NOT NULL REFERENCES sensor(sensor_id) ON DELETE CASCADE,
    value REAL NOT NULL,
    status TEXT,
    PRIMARY KEY (time, sensor_id)
);

-- Create hypertable for time-series optimization
-- Chunk interval: 1 day (optimal for 4.3M datapoints/day)
SELECT create_hypertable('measurement', 'time', 
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

-- Indexes for fast queries
-- Primary index: (sensor_id, time DESC) - most common query pattern
CREATE INDEX IF NOT EXISTS idx_measurement_sensor_time 
    ON measurement (sensor_id, time DESC);

-- Time index for chunking and time-range queries
CREATE INDEX IF NOT EXISTS idx_measurement_time 
    ON measurement (time DESC);

-- Sensor index for sensor lookups
CREATE INDEX IF NOT EXISTS idx_measurement_sensor_id 
    ON measurement (sensor_id);

-- ============================================
-- Performance Optimization Indexes
-- ============================================

-- Index on sensor.name for fast filtering in measurement_with_metadata queries
-- This significantly speeds up WHERE sensor_name = '...' clauses
CREATE INDEX IF NOT EXISTS idx_sensor_name 
    ON sensor (name);

-- Indexes on foreign key columns for optimized JOINs in measurement_with_metadata view
-- These improve join performance when querying the view

CREATE INDEX IF NOT EXISTS idx_rack_room_id 
    ON rack (room_id);

CREATE INDEX IF NOT EXISTS idx_device_rack_id 
    ON device (rack_id);

-- ============================================
-- Compression Policy
-- ============================================

-- Enable compression on chunks older than 90 days
ALTER TABLE measurement SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sensor_id'
);

-- Add compression policy: compress chunks older than 90 days
SELECT add_compression_policy('measurement', 
    INTERVAL '90 days',
    if_not_exists => TRUE);

-- ============================================
-- Optional Tables
-- ============================================

-- Crop Batch: Track crop batches per room
CREATE TABLE IF NOT EXISTS crop_batch (
    batch_id SERIAL PRIMARY KEY,
    crop_name TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    room_id INTEGER NOT NULL REFERENCES room(room_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Setpoints: Room-level setpoints with timestamps
CREATE TABLE IF NOT EXISTS setpoints (
    setpoint_id SERIAL PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES room(room_id) ON DELETE CASCADE,
    variable TEXT NOT NULL,
    target REAL NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(room_id, variable)
);

-- Actuator Events: Device control events
CREATE TABLE IF NOT EXISTS actuator_events (
    event_id SERIAL PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    value REAL,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create hypertable for actuator_events (time-series)
SELECT create_hypertable('actuator_events', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

-- Index for actuator_events
CREATE INDEX IF NOT EXISTS idx_actuator_events_device_time 
    ON actuator_events (device_id, time DESC);

-- ============================================
-- Continuous Aggregates
-- ============================================

-- Hourly aggregates: min, max, avg per sensor per hour
CREATE MATERIALIZED VIEW IF NOT EXISTS measurement_hourly
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', time) AS time,
    sensor_id,
    AVG(value) AS avg_value,
    MIN(value) AS min_value,
    MAX(value) AS max_value,
    COUNT(*) AS reading_count
FROM measurement
GROUP BY time, sensor_id;

-- Add refresh policy for hourly aggregates
-- Refresh every hour, keep last 7 days of raw data
SELECT add_continuous_aggregate_policy('measurement_hourly',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- Daily aggregates: min, max, avg per sensor per day
CREATE MATERIALIZED VIEW IF NOT EXISTS measurement_daily
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 day', time) AS time,
    sensor_id,
    AVG(value) AS avg_value,
    MIN(value) AS min_value,
    MAX(value) AS max_value,
    COUNT(*) AS reading_count
FROM measurement
GROUP BY time, sensor_id;

-- Add refresh policy for daily aggregates
-- Refresh every day, keep last 30 days of raw data
SELECT add_continuous_aggregate_policy('measurement_daily',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

-- ============================================
-- Helper Views for Common Queries
-- ============================================

-- View: Measurement with full metadata (for Grafana queries)
-- Join path: measurement -> sensor -> device -> rack -> room
CREATE OR REPLACE VIEW measurement_with_metadata AS
SELECT 
    m.time,
    m.sensor_id,
    m.value,
    m.status,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    s.data_type AS sensor_data_type,
    d.device_id,
    d.name AS device_name,
    d.type AS device_type,
    r.room_id,
    r.name AS room_name,
    r.target_vpd,
    r.target_temp
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id;

-- ============================================
-- Comments for Documentation
-- ============================================

COMMENT ON TABLE room IS 'Rooms (e.g., "Flower Room", "Veg Room") with optional target_vpd/target_temp';
COMMENT ON TABLE rack IS 'Racks within rooms (optional)';
COMMENT ON TABLE device IS 'Devices (CAN nodes) with type, IP, serial number';
COMMENT ON TABLE sensor IS 'Individual sensors with name, unit, data_type, channel, calibration_offset';
COMMENT ON TABLE measurement IS 'Unified time-series table for all sensor readings (hypertable)';
COMMENT ON TABLE crop_batch IS 'Track crop batches per room';
COMMENT ON TABLE setpoints IS 'Room-level setpoints with timestamps';
COMMENT ON TABLE actuator_events IS 'Device control events (hypertable)';

COMMENT ON VIEW measurement_hourly IS 'Hourly aggregates: min, max, avg per sensor per hour';
COMMENT ON VIEW measurement_daily IS 'Daily aggregates: min, max, avg per sensor per day';
COMMENT ON VIEW measurement_with_metadata IS 'Measurement data with full metadata joins for Grafana queries';

-- ============================================
-- Initial Data (Optional)
-- ============================================


-- ============================================
-- Verification Queries
-- ============================================

-- Verify hypertable was created
-- SELECT * FROM timescaledb_information.hypertables WHERE hypertable_name = 'measurement';

-- Verify compression policy
-- SELECT * FROM timescaledb_information.jobs WHERE proc_name = 'policy_compression';

-- Verify continuous aggregates
-- SELECT * FROM timescaledb_information.continuous_aggregates;

-- Verify indexes
-- SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'measurement';




