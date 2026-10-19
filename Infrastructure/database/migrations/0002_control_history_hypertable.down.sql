\set ON_ERROR_STOP on
SET search_path = public, pg_catalog;

BEGIN;

DO $database_guard$
BEGIN
    IF current_database() <> 'sj_cea'
       AND current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION
            '0002_control_history_hypertable rollback requires sj_cea or a monitoring_test_ database (current database: %)',
            current_database();
    END IF;
END
$database_guard$;

-- The compression job can have compressed chunks by the time this migration is
-- rolled back. Stop it and decompress those chunks before copying to a heap.
DO $remove_compression_policy$
DECLARE
    chunk_relation REGCLASS;
BEGIN
    PERFORM remove_compression_policy(
        'public.control_history'::regclass,
        if_exists => TRUE);

    FOR chunk_relation IN
        SELECT format('%I.%I', c.chunk_schema, c.chunk_name)::regclass
        FROM timescaledb_information.chunks AS c
        WHERE c.hypertable_schema = 'public'
          AND c.hypertable_name = 'control_history'
          AND c.is_compressed
    LOOP
        PERFORM decompress_chunk(chunk_relation, if_compressed => TRUE);
    END LOOP;
END
$remove_compression_policy$;

ALTER TABLE public.control_history RENAME TO control_history_hypertable_old;
CREATE TABLE public.control_history (
    LIKE public.control_history_hypertable_old INCLUDING DEFAULTS INCLUDING CONSTRAINTS
);
ALTER TABLE public.control_history
    ADD CONSTRAINT control_history_restore_pkey PRIMARY KEY (id);

ALTER SEQUENCE public.control_history_id_seq OWNED BY public.control_history.id;
ALTER TABLE public.control_history
    ALTER COLUMN id SET DEFAULT nextval('public.control_history_id_seq'::regclass);

INSERT INTO public.control_history
SELECT * FROM public.control_history_hypertable_old;

DO $verify_row_count$
DECLARE
    old_count BIGINT;
    new_count BIGINT;
BEGIN
    SELECT count(*) INTO old_count FROM public.control_history_hypertable_old;
    SELECT count(*) INTO new_count FROM public.control_history;
    IF old_count <> new_count THEN
        RAISE EXCEPTION 'control_history_row_count_mismatch old=% new=%',
            old_count, new_count;
    END IF;
END
$verify_row_count$;

DROP TABLE public.control_history_hypertable_old;
ALTER TABLE public.control_history
    RENAME CONSTRAINT control_history_restore_pkey TO control_history_pkey;

CREATE INDEX idx_control_history_location
    ON public.control_history (location, cluster);
CREATE INDEX idx_control_history_timestamp
    ON public.control_history ("timestamp" DESC);
CREATE INDEX idx_control_history_manual_expires
    ON public.control_history (manual_expires_at)
    WHERE manual_expires_at IS NOT NULL AND mode = 'manual';

COMMIT;
