-- TimescaleDB Continuous Aggregates for CEA Sensor Data
-- These materialized views automatically aggregate data for faster queries
-- Especially useful for Grafana dashboards showing longer time ranges (>24 hours)

-- Temperature readings - hourly aggregates
CREATE MATERIALIZED VIEW IF NOT EXISTS temperature_readings_hourly
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', timestamp) AS time,
    node_id,
    AVG(dry_bulb_c) AS avg_dry_bulb_c,
    MIN(dry_bulb_c) AS min_dry_bulb_c,
    MAX(dry_bulb_c) AS max_dry_bulb_c,
    AVG(wet_bulb_c) AS avg_wet_bulb_c,
    MIN(wet_bulb_c) AS min_wet_bulb_c,
    MAX(wet_bulb_c) AS max_wet_bulb_c,
    COUNT(*) AS reading_count
FROM temperature_readings
GROUP BY time, node_id;

-- Add refresh policy: refresh every hour, keep last 7 days of raw data
SELECT add_continuous_aggregate_policy('temperature_readings_hourly',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- Climate readings - hourly aggregates
CREATE MATERIALIZED VIEW IF NOT EXISTS climate_readings_hourly
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', timestamp) AS time,
    node_id,
    AVG(rh_percent) AS avg_rh_percent,
    MIN(rh_percent) AS min_rh_percent,
    MAX(rh_percent) AS max_rh_percent,
    AVG(vpd_kpa) AS avg_vpd_kpa,
    MIN(vpd_kpa) AS min_vpd_kpa,
    MAX(vpd_kpa) AS max_vpd_kpa,
    AVG(pressure_hpa) AS avg_pressure_hpa,
    COUNT(*) AS reading_count
FROM climate_readings
GROUP BY time, node_id;

SELECT add_continuous_aggregate_policy('climate_readings_hourly',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- CO2 readings - hourly aggregates
CREATE MATERIALIZED VIEW IF NOT EXISTS co2_readings_hourly
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', timestamp) AS time,
    node_id,
    AVG(co2_ppm) AS avg_co2_ppm,
    MIN(co2_ppm) AS min_co2_ppm,
    MAX(co2_ppm) AS max_co2_ppm,
    COUNT(*) AS reading_count
FROM co2_readings
GROUP BY time, node_id;

SELECT add_continuous_aggregate_policy('co2_readings_hourly',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- Pressure readings - hourly aggregates
CREATE MATERIALIZED VIEW IF NOT EXISTS pressure_readings_hourly
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', timestamp) AS time,
    node_id,
    AVG(pressure_hpa) AS avg_pressure_hpa,
    MIN(pressure_hpa) AS min_pressure_hpa,
    MAX(pressure_hpa) AS max_pressure_hpa,
    AVG(temperature_c) AS avg_temperature_c,
    AVG(humidity_percent) AS avg_humidity_percent,
    COUNT(*) AS reading_count
FROM pressure_readings
GROUP BY time, node_id;

SELECT add_continuous_aggregate_policy('pressure_readings_hourly',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- Secondary RH readings - hourly aggregates
CREATE MATERIALIZED VIEW IF NOT EXISTS secondary_rh_readings_hourly
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', timestamp) AS time,
    node_id,
    AVG(humidity_percent) AS avg_humidity_percent,
    MIN(humidity_percent) AS min_humidity_percent,
    MAX(humidity_percent) AS max_humidity_percent,
    AVG(temperature_c) AS avg_temperature_c,
    COUNT(*) AS reading_count
FROM secondary_rh_readings
GROUP BY time, node_id;

SELECT add_continuous_aggregate_policy('secondary_rh_readings_hourly',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- Water level readings - hourly aggregates
CREATE MATERIALIZED VIEW IF NOT EXISTS water_level_readings_hourly
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', timestamp) AS time,
    node_id,
    AVG(distance_mm) AS avg_distance_mm,
    MIN(distance_mm) AS min_distance_mm,
    MAX(distance_mm) AS max_distance_mm,
    AVG(ambient) AS avg_ambient,
    AVG(signal) AS avg_signal,
    COUNT(*) AS reading_count
FROM water_level_readings
GROUP BY time, node_id;

SELECT add_continuous_aggregate_policy('water_level_readings_hourly',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- Daily aggregates for all sensor types (for long-term trend analysis)
-- Temperature daily
CREATE MATERIALIZED VIEW IF NOT EXISTS temperature_readings_daily
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 day', timestamp) AS time,
    node_id,
    AVG(dry_bulb_c) AS avg_dry_bulb_c,
    MIN(dry_bulb_c) AS min_dry_bulb_c,
    MAX(dry_bulb_c) AS max_dry_bulb_c,
    AVG(wet_bulb_c) AS avg_wet_bulb_c,
    MIN(wet_bulb_c) AS min_wet_bulb_c,
    MAX(wet_bulb_c) AS max_wet_bulb_c,
    COUNT(*) AS reading_count
FROM temperature_readings
GROUP BY time, node_id;

SELECT add_continuous_aggregate_policy('temperature_readings_daily',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

-- Climate daily
CREATE MATERIALIZED VIEW IF NOT EXISTS climate_readings_daily
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 day', timestamp) AS time,
    node_id,
    AVG(rh_percent) AS avg_rh_percent,
    MIN(rh_percent) AS min_rh_percent,
    MAX(rh_percent) AS max_rh_percent,
    AVG(vpd_kpa) AS avg_vpd_kpa,
    MIN(vpd_kpa) AS min_vpd_kpa,
    MAX(vpd_kpa) AS max_vpd_kpa,
    AVG(pressure_hpa) AS avg_pressure_hpa,
    COUNT(*) AS reading_count
FROM climate_readings
GROUP BY time, node_id;

SELECT add_continuous_aggregate_policy('climate_readings_daily',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

-- CO2 daily
CREATE MATERIALIZED VIEW IF NOT EXISTS co2_readings_daily
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 day', timestamp) AS time,
    node_id,
    AVG(co2_ppm) AS avg_co2_ppm,
    MIN(co2_ppm) AS min_co2_ppm,
    MAX(co2_ppm) AS max_co2_ppm,
    COUNT(*) AS reading_count
FROM co2_readings
GROUP BY time, node_id;

SELECT add_continuous_aggregate_policy('co2_readings_daily',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

-- Notes:
-- - Continuous aggregates automatically refresh based on their policies
-- - Use hourly aggregates for queries showing 1-30 days of data
-- - Use daily aggregates for queries showing >30 days of data
-- - Raw data tables are still available for real-time queries (<24 hours)
-- - Grafana will automatically use the appropriate aggregate based on time range
-- - Queries are 10-100x faster when using aggregates vs raw data

