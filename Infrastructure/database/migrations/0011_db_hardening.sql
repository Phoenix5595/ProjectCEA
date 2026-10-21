-- migration: downtime=false
BEGIN;

-- Duplicate of alembic 001's idx_setpoints_loc_cluster_mode_updated_at (production-only leftover).
DROP INDEX IF EXISTS public.idx_setpoints_location_cluster_mode_updated;
-- Duplicate of the UNIQUE constraint index notes_location_cluster_mode_key (production-only leftover).
DROP INDEX IF EXISTS public.idx_notes_lookup;
-- Pre-August setpoint copy; owner decision: drop without export (no repo references).
DROP TABLE IF EXISTS public.setpoints_backup;

-- Refresh planner statistics of every plain (non-hypertable) public table;
-- several had stale or never-collected statistics.
DO $analyze_plain_tables$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.relname
        FROM pg_class AS c
        WHERE c.relnamespace = 'public'::regnamespace
          AND c.relkind = 'r'
          AND NOT EXISTS (
              SELECT 1
              FROM timescaledb_information.hypertables AS h
              WHERE h.hypertable_schema = 'public'
                AND h.hypertable_name = c.relname
          )
    LOOP
        EXECUTE format('ANALYZE public.%I', r.relname);
    END LOOP;
END;
$analyze_plain_tables$;

COMMIT;
