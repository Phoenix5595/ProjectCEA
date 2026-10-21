BEGIN;

CREATE INDEX IF NOT EXISTS idx_setpoints_location_cluster_mode_updated
    ON public.setpoints USING btree (location, cluster, mode, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_lookup
    ON public.notes USING btree (location, cluster, mode);
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
