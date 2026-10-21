BEGIN;

CREATE INDEX IF NOT EXISTS idx_setpoints_location_cluster_mode_updated
    ON public.setpoints USING btree (location, cluster, mode, updated_at DESC);
-- idx_notes_lookup existed only in production, where public.notes is owned by
-- postgres while migrations run as cea_user (the schema owner may DROP the index
-- but not CREATE one). It duplicates the UNIQUE notes_location_cluster_mode_key
-- index, so skip it when the table is absent or not owned by the current role.
DO $restore_notes_index$
DECLARE
    notes_owner oid;
BEGIN
    SELECT relowner INTO notes_owner FROM pg_class WHERE oid = to_regclass('public.notes');
    IF notes_owner IS NULL THEN
        RAISE NOTICE 'skipping idx_notes_lookup: public.notes does not exist';
    ELSIF pg_has_role(current_user, notes_owner, 'USAGE') THEN
        CREATE INDEX IF NOT EXISTS idx_notes_lookup
            ON public.notes USING btree (location, cluster, mode);
    ELSE
        RAISE NOTICE 'skipping idx_notes_lookup: % does not own public.notes', current_user;
    END IF;
END;
$restore_notes_index$;
-- Structure only: the dropped rows are intentionally not restorable.
CREATE TABLE IF NOT EXISTS public.setpoints_backup (
    id bigint,
    location text,
    cluster text,
    heating_setpoint real,
    humidity real,
    co2 real,
    updated_at timestamptz,
    mode text,
    vpd real,
    cooling_setpoint real,
    ramp_in_duration integer
);

COMMIT;
