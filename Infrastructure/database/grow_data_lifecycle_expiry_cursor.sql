-- Forward-only lifecycle support for bounded observation-time expiry.
-- Apply after grow_data_lifecycle.sql, as cea_user (the archive object owner).
-- No source rows, schedules, compression policies, or retention policies are changed.
\set ON_ERROR_STOP on
SET search_path = public, pg_catalog;
SET lock_timeout = '1s';
SET statement_timeout = '5s';

BEGIN;

DO $prerequisite$
BEGIN
    IF to_regclass('public.archive_dataset') IS NULL
       OR to_regclass('public.archive_availability') IS NULL THEN
        RAISE EXCEPTION 'grow_data_lifecycle_expiry_cursor requires grow_data_lifecycle.sql first';
    END IF;
END
$prerequisite$;

-- Monotone forward frontier for non-Veg, per-source/per-room expiry. A row
-- below processed_before has either been removed or was safely protected at
-- that time; released scopes explicitly reset the cursor through the service.
CREATE TABLE IF NOT EXISTS public.archive_expiry_cursor (
    source_table TEXT NOT NULL,
    room TEXT NOT NULL,
    processed_before TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT archive_expiry_cursor_pk PRIMARY KEY (source_table, room),
    CONSTRAINT archive_expiry_cursor_source_nonempty CHECK (btrim(source_table) <> ''),
    CONSTRAINT archive_expiry_cursor_room_nonempty CHECK (btrim(room) <> '')
);

CREATE INDEX IF NOT EXISTS archive_expiry_cursor_frontier_idx
    ON public.archive_expiry_cursor (processed_before, source_table, room);

COMMENT ON TABLE public.archive_expiry_cursor IS
    'Forward-only per-source/room nongrow expiry frontier; active/open/captured and unresolved rows are never deleted by the frontier.';

CREATE INDEX IF NOT EXISTS archive_load_staging_idx
    ON public.archive_load (staging_path) WHERE staging_path IS NOT NULL;
CREATE INDEX IF NOT EXISTS archive_artifact_superseded_idx
    ON public.archive_artifact (dataset_id, revision) WHERE state = 'superseded';

DO $grants$
BEGIN
    IF to_regrole('cea_user') IS NOT NULL THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON public.archive_expiry_cursor TO cea_user';
    END IF;
END
$grants$;

COMMIT;
