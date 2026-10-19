\set ON_ERROR_STOP on
BEGIN;

-- The production relation is a plain PostgreSQL table, not a hypertable, and
-- has no compression policy. Recreate that deployed state for SQL rollback.
CREATE TABLE IF NOT EXISTS public.actuator_events (
    event_id SERIAL PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES public.device(device_id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    value REAL,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_actuator_events_device_time
    ON public.actuator_events (device_id, time DESC);
COMMENT ON TABLE public.actuator_events IS 'Device control events';

DO $restore_scope_override$
BEGIN
    IF to_regclass('public.archive_scope_override') IS NOT NULL THEN
        ALTER TABLE public.archive_scope_override
            DROP CONSTRAINT IF EXISTS archive_scope_override_source_table_check,
            DROP CONSTRAINT IF EXISTS archive_scope_override_identity_shape,
            DROP CONSTRAINT IF EXISTS archive_scope_override_identity_positive,
            DROP CONSTRAINT IF EXISTS archive_scope_override_no_overlap;
        DROP INDEX IF EXISTS public.archive_scope_override_identity_idx;

        ALTER TABLE public.archive_scope_override
            ADD CONSTRAINT archive_scope_override_source_table_check
                CHECK (source_table IN ('measurement', 'actuator_events')),
            ADD CONSTRAINT archive_scope_override_identity_shape CHECK (
                (source_table = 'measurement'
                    AND source_identity ? 'sensor_id'
                    AND jsonb_typeof(source_identity -> 'sensor_id') = 'number'
                    AND (source_identity - 'sensor_id') = '{}'::JSONB)
                OR (source_table = 'actuator_events'
                    AND source_identity ? 'device_id'
                    AND jsonb_typeof(source_identity -> 'device_id') = 'number'
                    AND (source_identity - 'device_id') = '{}'::JSONB)
            ),
            ADD CONSTRAINT archive_scope_override_identity_positive CHECK (
                CASE source_table
                    WHEN 'measurement' THEN (source_identity ->> 'sensor_id')::BIGINT > 0
                    WHEN 'actuator_events' THEN (source_identity ->> 'device_id')::BIGINT > 0
                END
            ),
            ADD CONSTRAINT archive_scope_override_no_overlap EXCLUDE USING gist (
                source_table WITH =,
                (CASE source_table
                    WHEN 'measurement' THEN (source_identity ->> 'sensor_id')::BIGINT
                    WHEN 'actuator_events' THEN (source_identity ->> 'device_id')::BIGINT
                END) WITH =,
                tstzrange(start_at, end_at, '[)') WITH &&
            );

        CREATE INDEX archive_scope_override_identity_idx
            ON public.archive_scope_override (
                source_table,
                (CASE source_table
                    WHEN 'measurement' THEN (source_identity ->> 'sensor_id')::BIGINT
                    WHEN 'actuator_events' THEN (source_identity ->> 'device_id')::BIGINT
                END),
                start_at,
                end_at
            );
        COMMENT ON TABLE public.archive_scope_override IS
            'Owner-confirmed dated room assignment for unresolved measurement/actuator identities; never a general room-history editor.';
    END IF;
END
$restore_scope_override$;

COMMIT;
