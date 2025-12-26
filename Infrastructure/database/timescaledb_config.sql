-- TimescaleDB Configuration Optimizations for CEA Sensor Data
-- These settings optimize PostgreSQL for time-series workloads

-- Note: Some settings require PostgreSQL configuration file changes (postgresql.conf)
-- Others can be set at the database or session level

-- ============================================
-- Settings that can be applied via SQL (ALTER SYSTEM or ALTER DATABASE)
-- ============================================

-- Increase work_mem for better query performance with aggregations
-- This allows more memory for sorting and hash operations
-- Default is usually 4MB, increase to 64MB for time-series workloads
-- Note: This should be set in postgresql.conf, but we document it here
-- ALTER SYSTEM SET work_mem = '64MB';

-- Increase maintenance_work_mem for index creation and VACUUM operations
-- ALTER SYSTEM SET maintenance_work_mem = '256MB';

-- Increase shared_buffers (typically 25% of RAM for dedicated database server)
-- This should be set in postgresql.conf
-- ALTER SYSTEM SET shared_buffers = '256MB';  -- Adjust based on available RAM

-- Enable effective_cache_size (typically 50-75% of RAM)
-- ALTER SYSTEM SET effective_cache_size = '1GB';  -- Adjust based on available RAM

-- Increase max_connections if needed (default is usually 100)
-- ALTER SYSTEM SET max_connections = '200';

-- ============================================
-- Database-level settings
-- ============================================

-- Set default statistics target for better query planning
ALTER DATABASE cea_sensors SET default_statistics_target = 100;

-- ============================================
-- Recommended postgresql.conf settings
-- ============================================
-- 
-- Add these to /etc/postgresql/<version>/main/postgresql.conf:
--
-- # Memory settings
-- shared_buffers = 256MB              # 25% of RAM for dedicated DB server
-- effective_cache_size = 1GB          # 50-75% of RAM
-- work_mem = 64MB                     # Per-operation memory
-- maintenance_work_mem = 256MB        # For VACUUM, CREATE INDEX, etc.
--
-- # Connection settings
-- max_connections = 200               # Adjust based on expected load
--
-- # Write performance
-- wal_buffers = 16MB                  # Write-ahead log buffers
-- checkpoint_completion_target = 0.9  # Spread checkpoints over time
--
-- # Query planner
-- random_page_cost = 1.1              # Lower for SSD storage
-- effective_io_concurrency = 200      # For SSD storage
--
-- After making changes, restart PostgreSQL:
-- sudo systemctl restart postgresql
--
-- ============================================
-- Grafana Data Source Connection Settings
-- ============================================
--
-- In Grafana, when configuring the PostgreSQL data source:
--
-- Connection:
--   Host: localhost:5432
--   Database: cea_sensors
--   User: cea_user
--   Password: [your password]
--   SSL Mode: disable
--   TimescaleDB: Enable (checkbox)
--
-- Connection limits:
--   Max open: 100
--   Max idle: 100
--   Max lifetime: 14400 (4 hours)
--
-- These settings allow Grafana to maintain a connection pool for better performance.

-- ============================================
-- Verify current settings
-- ============================================

-- Check current work_mem setting
SHOW work_mem;

-- Check current shared_buffers
SHOW shared_buffers;

-- Check current max_connections
SHOW max_connections;

-- Check TimescaleDB version
SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';

-- ============================================
-- How to Apply Settings
-- ============================================
--
-- 1. For settings requiring postgresql.conf changes:
--    - Edit /etc/postgresql/<version>/main/postgresql.conf
--    - Add or modify the settings listed above
--    - Restart PostgreSQL: sudo systemctl restart postgresql
--
-- 2. For database-level settings (already applied above):
--    - These are applied when running this script
--    - No restart required
--
-- 3. Verify settings after applying:
--    - Run the SHOW commands above
--    - Check that values match recommendations
--
-- 4. Monitor performance:
--    - Use EXPLAIN ANALYZE on slow queries
--    - Check pg_stat_statements for query performance
--    - Monitor connection count and memory usage

