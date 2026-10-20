\set ON_ERROR_STOP on

-- Grow data lifecycle schema case.
--
-- Running context: after fixtures/grow_data_lifecycle_fixture.sql and after
-- grow_data_lifecycle.sql has been applied twice (idempotence proof) by
-- test-grow-data-lifecycle.sh. All assertions are plain SQL so they stay
-- valid regardless of how grow-archive-service code or schema.py evolve.

DO $guard$
BEGIN
    IF lower(current_database()) LIKE '%cea_sensors%'
       OR lower(current_database()) LIKE '%sj_cea%'
       OR current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION 'grow data lifecycle schema case requires the monitoring_test_ database namespace';
    END IF;
END
$guard$;

-- ------------------------------------------------------------------
-- Helper: assert one SQL statement raises a specific SQLSTATE.
-- ------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE assert_sqlstate(expected TEXT, action TEXT) LANGUAGE plpgsql AS $$
DECLARE
    caught TEXT := NULL;
BEGIN
    BEGIN
        EXECUTE action;
    EXCEPTION WHEN OTHERS THEN
        caught := SQLSTATE;
    END;
    IF caught IS NULL THEN
        RAISE EXCEPTION 'expected SQLSTATE % but statement succeeded: %', expected, action;
    END IF;
    IF caught <> expected THEN
        RAISE EXCEPTION 'expected SQLSTATE %, got %: %', expected, caught, action;
    END IF;
END;
$$;

-- ------------------------------------------------------------------
-- 1. Lifecycle schema exists and the retired cold schema does not.
-- ------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.grow_lifecycle') IS NULL THEN
        RAISE EXCEPTION 'grow_lifecycle is missing';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'sj_history_cold') THEN
        RAISE EXCEPTION 'retired sj_history_cold schema must not exist';
    END IF;
END;
$$;
-- ------------------------------------------------------------------
-- 2. Source stream inventory: exactly the 11 defined streams exist as
--    ordinary tables and the four descriptor-unavailable streams do not.
-- ------------------------------------------------------------------
DO $$
DECLARE
    missing TEXT;
    extra TEXT;
BEGIN
    SELECT COALESCE(string_agg(r.source_stream, ', ' ORDER BY r.source_stream), '<none>')
      INTO missing
      FROM (VALUES ('measurement'),
                   ('automation_state'), ('effective_setpoints'),
                   ('control_history'), ('relay_observation'),
                   ('monitoring_room_photoperiod'), ('monitoring_photoperiod_coverage'),
                   ('mode_transition_history'), ('pid_parameter_history'),
                   ('config_versions'), ('calendar_mode_application')) AS r(source_stream)
     WHERE NOT EXISTS (
           SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relname = r.source_stream AND c.relkind = 'r');
    IF missing <> '<none>' THEN
        RAISE EXCEPTION 'expected all defined source streams as tables; missing: %', missing;
    END IF;

    SELECT string_agg(r.source_stream, ', ' ORDER BY r.source_stream) INTO extra
      FROM (VALUES ('light_state_history'), ('pid_adjustments'),
                   ('setpoint_history'), ('rollout_events')) AS r(source_stream)
     WHERE EXISTS (
           SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relname = r.source_stream);
    IF extra IS NOT NULL THEN
        RAISE EXCEPTION 'unavailable source streams must be absent from the fixture: %', extra;
    END IF;
END;
$$;
-- ------------------------------------------------------------------
-- 3. Source-resident history tables have no non-internal triggers.
-- ------------------------------------------------------------------
DO $$
DECLARE
    source_trigger_count INTEGER;
BEGIN
    SELECT count(*) INTO source_trigger_count
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname = ANY (ARRAY[
          'measurement', 'automation_state',
          'effective_setpoints', 'control_history', 'relay_observation',
          'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
          'mode_transition_history', 'pid_parameter_history',
          'config_versions', 'calendar_mode_application'
      ])
      AND NOT t.tgisinternal;
    IF source_trigger_count <> 0 THEN
        RAISE EXCEPTION 'expected no non-internal triggers on the 11 source tables, found %',
            source_trigger_count;
    END IF;
END;
$$;

-- ------------------------------------------------------------------
-- 4. The six automation_state indexes remain unchanged.
-- ------------------------------------------------------------------
DO $$
DECLARE
    index_name TEXT;
    index_total INTEGER;
BEGIN
    SELECT count(*) INTO index_total
    FROM pg_indexes
    WHERE schemaname = 'public' AND tablename = 'automation_state';
    IF index_total <> 6 THEN
        RAISE EXCEPTION 'expected six automation_state indexes, found %', index_total;
    END IF;

    FOREACH index_name IN ARRAY ARRAY[
        'automation_state_pkey',
        'automation_state_timestamp_idx',
        'idx_automation_state_location',
        'idx_automation_state_timestamp',
        'idx_automation_state_device',
        'monitoring_automation_state_id_idx'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'automation_state'
              AND indexname = index_name
        ) THEN
            RAISE EXCEPTION 'automation_state index % is missing', index_name;
        END IF;
    END LOOP;
END;
$$;


-- ------------------------------------------------------------------
-- 5. The fixture's verified hypertable-compatible composite primary keys
--    match the documented source descriptors.
-- ------------------------------------------------------------------
DO $$
DECLARE
    stream_name TEXT;
    expected_key TEXT;
    found_count INTEGER;
BEGIN
    FOR stream_name, expected_key IN
        SELECT s.stream_name, s.key_names
        FROM (VALUES
            ('automation_state', 'time,id'),
            ('control_history', 'id,time')
        ) AS s(stream_name, key_names)
    LOOP
        SELECT count(*) INTO found_count
        FROM (
            SELECT string_agg(a.attname, ',' ORDER BY k.ordinality) AS key_names
            FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
            WHERE i.indrelid = format('public.%I', stream_name)::regclass
              AND i.indisunique
            GROUP BY i.indexrelid
        ) keys
        WHERE key_names = expected_key;
        IF found_count = 0 THEN
            RAISE EXCEPTION 'stream % lacks the documented composite unique key (%)', stream_name, expected_key;
        END IF;
    END LOOP;
END;
$$;
-- ------------------------------------------------------------------
-- 6. Removed job and load kinds are rejected by schema constraints.
-- ------------------------------------------------------------------
INSERT INTO archive_dataset (dataset_id, kind, calendar_year, quarter, room)
VALUES ('88888888-8888-4888-8888-888888888888', 'veg_quarter', 2099, 1, 'Veg Room');

CALL assert_sqlstate('23514', $stmt$
    INSERT INTO archive_job (dataset_id, kind, requested_revision)
    VALUES ('88888888-8888-4888-8888-888888888888', 'compact', 1)
$stmt$);

CALL assert_sqlstate('23514', $stmt$
    INSERT INTO archive_load (dataset_id, revision, source_kind, expires_at)
    VALUES ('88888888-8888-4888-8888-888888888888', 1, 'database', clock_timestamp() + INTERVAL '1 hour')
$stmt$);

DELETE FROM archive_dataset WHERE dataset_id = '88888888-8888-4888-8888-888888888888';


-- ------------------------------------------------------------------
-- 7. grow_lifecycle identity, state domain and uniqueness rules.
-- ------------------------------------------------------------------
-- a. Register two grows: Flower grow on batch 1 and Veg grow on batch 2.
INSERT INTO grow_lifecycle (grow_plan_id, crop_batch_id)
VALUES ('11111111-1111-4111-8111-111111111111', 1)
ON CONFLICT (grow_plan_id) DO NOTHING;

INSERT INTO grow_lifecycle (grow_plan_id, crop_batch_id)
VALUES ('22222222-2222-4222-8222-222222222222', 2);

DO $$
BEGIN
    IF (SELECT state FROM grow_lifecycle
        WHERE grow_plan_id = '11111111-1111-4111-8111-111111111111') <> 'open'
       OR (SELECT calendar_revision FROM grow_lifecycle
           WHERE grow_plan_id = '11111111-1111-4111-8111-111111111111') <> 1 THEN
        RAISE EXCEPTION 'a new registration must default to state=open and calendar_revision=1';
    END IF;
END;
$$;

-- b. Primary key on grow_plan_id.
CALL assert_sqlstate('23505', $stmt$
    INSERT INTO grow_lifecycle (grow_plan_id, crop_batch_id)
    VALUES ('11111111-1111-4111-8111-111111111111', 2)
$stmt$);

-- c. crop_batch_id is unique: one crop batch owns one grow.
CALL assert_sqlstate('23505', $stmt$
    INSERT INTO grow_lifecycle (grow_plan_id, crop_batch_id)
    VALUES ('33333333-3333-4333-8333-333333333333', 1)
$stmt$);

-- d. State domain.
CALL assert_sqlstate('23514', $stmt$
    INSERT INTO grow_lifecycle (grow_plan_id, crop_batch_id, state)
    VALUES ('44444444-4444-4444-8444-444444444444', 2, 'defaulted')
$stmt$);

-- e. crop_batch foreign key.
CALL assert_sqlstate('23503', $stmt$
    INSERT INTO grow_lifecycle (grow_plan_id, crop_batch_id)
    VALUES ('55555555-5555-4555-8555-555555555555', 9999)
$stmt$);

DO $$
BEGIN
    IF (SELECT count(*) FROM grow_lifecycle) <> 2 THEN
        RAISE EXCEPTION 'rule violations must roll back; found % stored rows',
            (SELECT count(*) FROM grow_lifecycle);
    END IF;
END;
$$;

-- f. calendar_revision is explicitly storable before first closure.
INSERT INTO crop_batch (batch_id, crop_name, start_date, room_id)
VALUES (3, 'Explicit revision proof', '2026-01-01', 1);
INSERT INTO grow_lifecycle (grow_plan_id, crop_batch_id, state, calendar_revision)
VALUES ('77777777-7777-4777-8777-777777777777', 3, 'open', 3);
DO $$
BEGIN
    IF (SELECT count(*) FROM grow_lifecycle
        WHERE grow_plan_id = '77777777-7777-4777-8777-777777777777'
          AND calendar_revision = 3) <> 1 THEN
        RAISE EXCEPTION 'explicit calendar_revision must be storable before closure';
    END IF;
END;
$$;

DELETE FROM grow_lifecycle WHERE grow_plan_id = '77777777-7777-4777-8777-777777777777';
DO $$
BEGIN
    IF (SELECT count(*) FROM grow_lifecycle) <> 2 THEN
        RAISE EXCEPTION 'delete of a not-yet-closed grow must work; found % rows',
            (SELECT count(*) FROM grow_lifecycle);
    END IF;
END;
$$;

-- ------------------------------------------------------------------
-- 8. First closure: original_drying_end and first_closed_at are written
--    together once, never afterwards.
-- ------------------------------------------------------------------
-- a. Setting only one half of the pair is rejected.
CALL assert_sqlstate('23514', $stmt$
    UPDATE grow_lifecycle SET original_drying_end = DATE '2026-09-02'
    WHERE grow_plan_id = '11111111-1111-4111-8111-111111111111'
$stmt$);

-- b. Writing both together (plus the closure state) is legal.
UPDATE grow_lifecycle
   SET state = 'closed',
       closed_at = TIMESTAMPTZ '2026-09-13 12:34:56+00',
       original_drying_end = DATE '2026-09-02',
       first_closed_at = TIMESTAMPTZ '2026-09-13 12:34:56+00'
 WHERE grow_plan_id = '11111111-1111-4111-8111-111111111111';

-- c. Rewriting either write-once value afterwards is rejected.
CALL assert_sqlstate('23514', $stmt$
    UPDATE grow_lifecycle SET original_drying_end = DATE '2026-09-03'
    WHERE grow_plan_id = '11111111-1111-4111-8111-111111111111'
$stmt$);

DO $$
DECLARE
    frozen_date DATE;
    frozen_at TIMESTAMPTZ;
    caught TEXT;
BEGIN
    SELECT original_drying_end, first_closed_at INTO frozen_date, frozen_at
     FROM grow_lifecycle
    WHERE grow_plan_id = '11111111-1111-4111-8111-111111111111';
    IF frozen_date <> DATE '2026-09-02'
       OR frozen_at <> TIMESTAMPTZ '2026-09-13 12:34:56+00' THEN
        RAISE EXCEPTION 'the first-closure record must stay exactly as frozen: % / %',
            frozen_date, frozen_at;
    END IF;

    BEGIN
        UPDATE grow_lifecycle
           SET first_closed_at = TIMESTAMPTZ '2026-09-14 00:00:00+00'
         WHERE grow_plan_id = '11111111-1111-4111-8111-111111111111';
    EXCEPTION WHEN OTHERS THEN
        caught := SQLSTATE;
    END;
    IF caught IS NULL OR caught <> '23514' THEN
        RAISE EXCEPTION 'expected SQLSTATE 23514 for a settled write-once update, got %', caught;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'grow_lifecycle_first_closure_complete') THEN
        RAISE EXCEPTION 'grow_lifecycle_first_closure_complete check is missing';
    END IF;
END;
$$;
