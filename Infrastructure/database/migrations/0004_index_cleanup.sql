-- migration: downtime=false
\set ON_ERROR_STOP on

BEGIN;

DROP INDEX IF EXISTS public.idx_measurement_time;
DROP INDEX IF EXISTS public.idx_measurement_sensor_id;
DROP INDEX IF EXISTS public.idx_effective_setpoints_timestamp;
DROP INDEX IF EXISTS public.idx_effective_setpoints_location;

COMMIT;

-- TimescaleDB requires transaction_per_chunk index creation to run outside an
-- explicit transaction block. An interrupted build leaves the root index present
-- but invalid with some chunks unindexed, and IF NOT EXISTS would then skip it on
-- retry; drop an invalid leftover first so a re-run rebuilds every chunk.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relname = 'idx_effective_setpoints_device_mode_time'
           AND NOT i.indisvalid
    ) THEN
        DROP INDEX public.idx_effective_setpoints_device_mode_time;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_effective_setpoints_device_mode_time
    ON public.effective_setpoints (location, cluster, device_name, mode, "timestamp" DESC)
    WITH (timescaledb.transaction_per_chunk);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relname = 'idx_effective_setpoints_device_mode_time'
           AND NOT i.indisvalid
    ) THEN
        RAISE EXCEPTION 'idx_effective_setpoints_device_mode_time_invalid';
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_pid_parameter_history_room_type_time
    ON public.pid_parameter_history (location, cluster, device_type, "timestamp" DESC);
