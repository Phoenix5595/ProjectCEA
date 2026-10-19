\set ON_ERROR_STOP on
\ir grow_data_lifecycle_fixture.sql

-- Match the duplicate default time indexes present in production.
CREATE INDEX IF NOT EXISTS measurement_time_idx ON public.measurement (time DESC);
CREATE INDEX IF NOT EXISTS effective_setpoints_timestamp_idx
    ON public.effective_setpoints (timestamp DESC);
