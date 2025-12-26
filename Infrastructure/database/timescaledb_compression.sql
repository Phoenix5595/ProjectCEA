-- TimescaleDB Compression Policies for CEA Sensor Data
-- Compresses data older than 90 days to save storage space (~70% reduction)
-- Compression is transparent - queries work normally on compressed data

-- Enable compression on all sensor reading hypertables
-- Compression will be applied to data older than 90 days

-- Temperature readings compression
ALTER TABLE temperature_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'node_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

-- Add compression policy: compress data older than 90 days
SELECT add_compression_policy('temperature_readings', INTERVAL '90 days', if_not_exists => TRUE);

-- Climate readings compression
ALTER TABLE climate_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'node_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('climate_readings', INTERVAL '90 days', if_not_exists => TRUE);

-- CO2 readings compression
ALTER TABLE co2_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'node_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('co2_readings', INTERVAL '90 days', if_not_exists => TRUE);

-- Pressure readings compression
ALTER TABLE pressure_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'node_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('pressure_readings', INTERVAL '90 days', if_not_exists => TRUE);

-- Secondary RH readings compression
ALTER TABLE secondary_rh_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'node_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('secondary_rh_readings', INTERVAL '90 days', if_not_exists => TRUE);

-- Water level readings compression
ALTER TABLE water_level_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'node_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('water_level_readings', INTERVAL '90 days', if_not_exists => TRUE);

-- Heartbeat readings compression (less critical, but still useful)
ALTER TABLE heartbeat_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'node_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('heartbeat_readings', INTERVAL '90 days', if_not_exists => TRUE);

-- Notes:
-- - Compression is automatic and runs in the background
-- - Compressed data can be queried normally - no special syntax needed
-- - Compression typically reduces storage by 70-90%
-- - Segmenting by node_id improves compression ratio and query performance
-- - Ordering by timestamp DESC optimizes time-range queries

