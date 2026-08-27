-- Migration: add the global automation cursor access path
-- TimescaleDB hypertables do not support CREATE INDEX CONCURRENTLY.
-- Do not run inside an explicit transaction; owner authorization is required before application.
CREATE INDEX IF NOT EXISTS monitoring_automation_state_id_idx
    ON public.automation_state (id)
    WITH (timescaledb.transaction_per_chunk);
