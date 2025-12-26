-- Migration: Remove zones, connect rooms directly to facilities
-- This script migrates existing data from zone-based to facility-based room structure
-- Run this AFTER updating the schema

-- Step 1: Add new facility_id column to room (if not exists)
ALTER TABLE room ADD COLUMN IF NOT EXISTS facility_id_new INTEGER;

-- Step 2: Populate facility_id from zone
UPDATE room r
SET facility_id_new = z.facility_id
FROM zone z
WHERE r.zone_id = z.zone_id;

-- Step 3: Verify data migration
DO $$
DECLARE
    unmapped_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO unmapped_count
    FROM room
    WHERE facility_id_new IS NULL AND zone_id IS NOT NULL;
    
    IF unmapped_count > 0 THEN
        RAISE WARNING 'Found % rooms with zone_id but no facility_id_new', unmapped_count;
    ELSE
        RAISE NOTICE 'All rooms successfully mapped to facilities';
    END IF;
END $$;

-- Step 4: Drop views that depend on zone_id
DROP VIEW IF EXISTS measurement_with_metadata CASCADE;
DROP VIEW IF EXISTS latest_sensor_values CASCADE;
DROP VIEW IF EXISTS measurement_timeseries CASCADE;

-- Step 5: Drop foreign key constraint on zone_id
ALTER TABLE room DROP CONSTRAINT IF EXISTS room_zone_id_fkey;

-- Step 6: Drop zone_id column
ALTER TABLE room DROP COLUMN IF EXISTS zone_id;

-- Step 7: Rename facility_id_new to facility_id
ALTER TABLE room RENAME COLUMN facility_id_new TO facility_id;

-- Step 8: Add foreign key constraint to facility
ALTER TABLE room ADD CONSTRAINT room_facility_id_fkey 
    FOREIGN KEY (facility_id) REFERENCES facility(facility_id) ON DELETE CASCADE;

-- Step 9: Add NOT NULL constraint
ALTER TABLE room ALTER COLUMN facility_id SET NOT NULL;

-- Step 10: Update unique constraint
ALTER TABLE room DROP CONSTRAINT IF EXISTS room_zone_id_name_key;
ALTER TABLE room ADD CONSTRAINT room_facility_id_name_key UNIQUE (facility_id, name);

-- Step 11: Recreate views (updated without zones)
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
    r.target_temp,
    f.facility_id,
    f.name AS facility_name
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id
LEFT JOIN facility f ON r.facility_id = f.facility_id;

CREATE OR REPLACE VIEW latest_sensor_values AS
SELECT DISTINCT ON (sensor_id)
    m.sensor_id,
    m.time,
    m.value,
    m.status,
    s.name AS sensor_name,
    s.unit AS sensor_unit,
    d.name AS device_name,
    r.name AS room_name,
    f.name AS facility_name
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id
LEFT JOIN facility f ON r.facility_id = f.facility_id
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
    r.name AS room_name,
    f.facility_id,
    f.name AS facility_name
FROM measurement m
JOIN sensor s ON m.sensor_id = s.sensor_id
JOIN device d ON s.device_id = d.device_id
LEFT JOIN rack rk ON d.rack_id = rk.rack_id
LEFT JOIN room r ON rk.room_id = r.room_id
LEFT JOIN facility f ON r.facility_id = f.facility_id;

-- Step 12: Drop zone table (after verifying data migration)
-- Uncomment the following line after verifying all data is migrated correctly
-- DROP TABLE IF EXISTS zone CASCADE;

-- Verification queries
SELECT 'Migration complete. Verify data:' AS status;
SELECT COUNT(*) AS total_rooms FROM room;
SELECT COUNT(*) AS rooms_with_facility FROM room WHERE facility_id IS NOT NULL;
SELECT f.name AS facility_name, COUNT(r.room_id) AS room_count
FROM facility f
LEFT JOIN room r ON f.facility_id = r.facility_id
GROUP BY f.facility_id, f.name
ORDER BY f.name;

