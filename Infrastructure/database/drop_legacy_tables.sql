-- Drop Legacy Tables
-- This script removes old column-based sensor reading tables that are no longer used
-- All data is now stored in the normalized 'measurement' table
--
-- WARNING: This will permanently delete historical data from these tables
-- Make sure you have backups if you need to preserve this data
--
-- Created: 2025-12-17
-- Database: cea_sensors

-- ============================================
-- Drop Continuous Aggregates (if they exist)
-- ============================================

-- Drop continuous aggregates that depend on legacy tables
DROP MATERIALIZED VIEW IF EXISTS temperature_readings_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS temperature_readings_daily CASCADE;
DROP MATERIALIZED VIEW IF EXISTS climate_readings_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS climate_readings_daily CASCADE;
DROP MATERIALIZED VIEW IF EXISTS co2_readings_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS co2_readings_daily CASCADE;
DROP MATERIALIZED VIEW IF EXISTS pressure_readings_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS secondary_rh_readings_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS water_level_readings_hourly CASCADE;

-- ============================================
-- Drop Legacy Sensor Reading Tables
-- ============================================

-- Drop legacy column-based sensor reading tables
-- These tables stopped receiving data on 2025-12-12
-- All new data goes to the 'measurement' table

DROP TABLE IF EXISTS temperature_readings CASCADE;
DROP TABLE IF EXISTS climate_readings CASCADE;
DROP TABLE IF EXISTS co2_readings CASCADE;
DROP TABLE IF EXISTS pressure_readings CASCADE;
DROP TABLE IF EXISTS secondary_rh_readings CASCADE;
DROP TABLE IF EXISTS water_level_readings CASCADE;
DROP TABLE IF EXISTS heartbeat_readings CASCADE;

-- ============================================
-- Drop can_messages (legacy raw CAN logging)
-- ============================================

-- Drop can_messages table (no longer actively written to)
-- Note: Automation service fallback code may need updating to use 'measurement' table instead
DROP TABLE IF EXISTS can_messages CASCADE;

-- ============================================
-- Verification
-- ============================================

-- Verify legacy tables are gone
DO $$
DECLARE
    legacy_tables TEXT[] := ARRAY[
        'temperature_readings',
        'climate_readings',
        'co2_readings',
        'pressure_readings',
        'secondary_rh_readings',
        'water_level_readings',
        'heartbeat_readings'
    ];
    table_name TEXT;
    table_exists BOOLEAN;
BEGIN
    FOREACH table_name IN ARRAY legacy_tables
    LOOP
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND information_schema.tables.table_name = drop_legacy_tables.table_name
        ) INTO table_exists;
        
        IF table_exists THEN
            RAISE NOTICE 'WARNING: Table % still exists!', table_name;
        ELSE
            RAISE NOTICE 'OK: Table % successfully dropped', table_name;
        END IF;
    END LOOP;
END $$;

-- Show remaining tables
SELECT 
    'Remaining sensor-related tables:' as info,
    table_name
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_type = 'BASE TABLE'
AND (
    table_name LIKE '%reading%' 
    OR table_name LIKE '%sensor%'
    OR table_name LIKE '%measurement%'
    OR table_name = 'can_messages'
)
ORDER BY table_name;

