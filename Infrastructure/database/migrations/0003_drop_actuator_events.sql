-- migration: downtime=false
\set ON_ERROR_STOP on
BEGIN;

DO $assert_actuator_events_empty$
DECLARE
    row_count BIGINT;
BEGIN
    IF to_regclass('public.actuator_events') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM public.actuator_events' INTO row_count;
        IF row_count <> 0 THEN
            RAISE EXCEPTION 'actuator_events_not_empty';
        END IF;
    END IF;
END
$assert_actuator_events_empty$;

DROP TABLE IF EXISTS public.actuator_events;

DO $retire_actuator_scope$
DECLARE
    row_count BIGINT;
BEGIN
    IF to_regclass('public.archive_scope_override') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM public.archive_scope_override '
             || 'WHERE source_table = ''actuator_events''' INTO row_count;
        IF row_count <> 0 THEN
            RAISE EXCEPTION 'actuator_events_scope_overrides_not_empty';
        END IF;

        ALTER TABLE public.archive_scope_override
            DROP CONSTRAINT IF EXISTS archive_scope_override_source_table_check,
            DROP CONSTRAINT IF EXISTS archive_scope_override_identity_shape,
            DROP CONSTRAINT IF EXISTS archive_scope_override_identity_positive,
            DROP CONSTRAINT IF EXISTS archive_scope_override_no_overlap;
        DROP INDEX IF EXISTS public.archive_scope_override_identity_idx;

        ALTER TABLE public.archive_scope_override
            ADD CONSTRAINT archive_scope_override_source_table_check
                CHECK (source_table = 'measurement'),
            ADD CONSTRAINT archive_scope_override_identity_shape CHECK (
                source_identity ? 'sensor_id'
                AND jsonb_typeof(source_identity -> 'sensor_id') = 'number'
                AND (source_identity - 'sensor_id') = '{}'::JSONB
            ),
            ADD CONSTRAINT archive_scope_override_identity_positive CHECK (
                (source_identity ->> 'sensor_id')::BIGINT > 0
            ),
            ADD CONSTRAINT archive_scope_override_no_overlap EXCLUDE USING gist (
                source_table WITH =,
                ((source_identity ->> 'sensor_id')::BIGINT) WITH =,
                tstzrange(start_at, end_at, '[)') WITH &&
            );

        CREATE INDEX archive_scope_override_identity_idx
            ON public.archive_scope_override (
                source_table,
                ((source_identity ->> 'sensor_id')::BIGINT),
                start_at,
                end_at
            );
        COMMENT ON TABLE public.archive_scope_override IS
            'Owner-confirmed dated room assignment for unresolved measurement identities; never a general room-history editor.';
    END IF;
END
$retire_actuator_scope$;

COMMIT;
