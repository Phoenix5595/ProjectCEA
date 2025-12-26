-- Migration: Remove facility table, make room name globally unique
-- This script migrates existing data from facility-based to room-based structure
-- Run this AFTER updating the schema

-- Step 1: Drop views that depend on facility
DROP VIEW IF EXISTS measurement_with_metadata CASCADE;
DROP VIEW IF EXISTS latest_sensor_values CASCADE;
DROP VIEW IF EXISTS measurement_timeseries CASCADE;

-- Step 2: Drop foreign key constraint on facility_id
ALTER TABLE room DROP CONSTRAINT IF EXISTS room_facility_id_fkey;

-- Step 3: Drop facility_id column from room
ALTER TABLE room DROP COLUMN IF EXISTS facility_id;

-- Step 4: Update unique constraint - make room name globally unique
ALTER TABLE room DROP CONSTRAINT IF EXISTS room_facility_id_name_key;
ALTER TABLE room ADD CONSTRAINT room_name_key UNIQUE (name);

-- Step 5: Recreate views (updated without facility)
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

CREATE OR REPLACE VIEW latest_sensor_values AS
SELECT DISTINCT ON (sensor_id)
    m.sensor_id,
    m.time,
    m.value,
    m.status,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    d.name AS device_name,
    r.name AS room_name
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id
ORDER BY m.sensor_id, m.time DESC;

CREATE OR REPLACE VIEW measurement_timeseries AS
SELECT 
    m.time,
    m.sensor_id,
    m.value,
    m.status,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    d.device_id,
    d.name AS device_name,
    r.room_id,
    r.name AS room_name
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id;

-- Step 6: Drop facility table (after verifying data migration)
DROP TABLE IF EXISTS facility CASCADE;

-- Verification queries
SELECT 'Migration complete. Verify data:' AS status;
SELECT COUNT(*) AS total_rooms FROM room;
SELECT name AS room_name FROM room ORDER BY name;

