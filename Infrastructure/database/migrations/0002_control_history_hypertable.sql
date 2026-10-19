-- migration: downtime=true
\set ON_ERROR_STOP on
SET search_path = public, pg_catalog;

BEGIN;

DO $database_guard$
BEGIN
    IF current_database() <> 'sj_cea'
       AND current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION
            '0002_control_history_hypertable requires sj_cea or a monitoring_test_ database (current database: %)',
            current_database();
    END IF;
END
$database_guard$;

-- A fresh Alembic baseline may already have made this a hypertable. Remove any
-- policy from that source relation before renaming it; the replacement gets
-- its own three-month policy below.
DO $remove_old_compression_policy$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
          AND hypertable_name = 'control_history'
    ) THEN
        PERFORM remove_compression_policy(
            'public.control_history'::regclass,
            if_exists => TRUE);
    END IF;
END
$remove_old_compression_policy$;

ALTER TABLE public.control_history RENAME TO control_history_old;
-- A source hypertable (as on a fresh Alembic install) already has Timescale's
-- default time index. Free its conventional name for the replacement.
DO $rename_old_timestamp_index$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_index AS i
        WHERE i.indrelid = 'public.control_history_old'::regclass
          AND i.indexrelid = to_regclass('public.control_history_timestamp_idx')
    ) THEN
        ALTER INDEX public.control_history_timestamp_idx
            RENAME TO control_history_old_timestamp_idx;
    END IF;
END
$rename_old_timestamp_index$;

-- LIKE preserves column order, defaults and CHECK constraints. The temporary
-- primary-key name avoids colliding with control_history_pkey on the old table.
CREATE TABLE public.control_history (
    LIKE public.control_history_old INCLUDING DEFAULTS INCLUDING CONSTRAINTS
);
ALTER TABLE public.control_history
    ADD CONSTRAINT control_history_hypertable_pkey PRIMARY KEY (id, "timestamp");

SELECT create_hypertable(
    'public.control_history',
    'timestamp',
    chunk_time_interval => INTERVAL '7 days');

ALTER SEQUENCE public.control_history_id_seq OWNED BY public.control_history.id;
ALTER TABLE public.control_history
    ALTER COLUMN id SET DEFAULT nextval('public.control_history_id_seq'::regclass);

INSERT INTO public.control_history
SELECT * FROM public.control_history_old;

DO $verify_row_count$
DECLARE
    old_count BIGINT;
    new_count BIGINT;
BEGIN
    SELECT count(*) INTO old_count FROM public.control_history_old;
    SELECT count(*) INTO new_count FROM public.control_history;
    IF old_count <> new_count THEN
        RAISE EXCEPTION 'control_history_row_count_mismatch old=% new=%',
            old_count, new_count;
    END IF;
END
$verify_row_count$;

-- Dropping the source relation frees its original index names. The hypertable
-- primary-key constraint is then renamed to the conventional name.
DROP TABLE public.control_history_old;
ALTER TABLE public.control_history
    RENAME CONSTRAINT control_history_hypertable_pkey TO control_history_pkey;

CREATE INDEX idx_control_history_location
    ON public.control_history (location, cluster);
CREATE INDEX idx_control_history_manual_expires
    ON public.control_history (manual_expires_at)
    WHERE manual_expires_at IS NOT NULL AND mode = 'manual';
CREATE INDEX idx_control_history_device_channel_time
    ON public.control_history (location, cluster, device_name, channel, "timestamp" DESC);

ALTER TABLE public.control_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'location,cluster,device_name,channel',
    timescaledb.compress_orderby = '"timestamp" DESC'
);
SELECT add_compression_policy(
    'public.control_history'::regclass,
    compress_after => INTERVAL '3 months');

COMMIT;
