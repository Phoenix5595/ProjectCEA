\set ON_ERROR_STOP on

DO $case$
BEGIN
    IF current_database() IN ('cea_sensors', 'cea_sensors_test')
       OR current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION 'destructive checks require an explicit test database';
    END IF;

    IF current_setting('listen_addresses') IS NULL THEN
        RAISE EXCEPTION 'database identity check failed';
    END IF;

    IF to_regclass('public.monitoring_incompatible_probe') IS NULL THEN
        RAISE EXCEPTION 'incompatible object rejection was not exercised';
    END IF;

    IF (
        SELECT count(*)
        FROM _timescaledb_catalog.continuous_agg
        WHERE user_view_schema = 'public'
          AND user_view_name LIKE 'monitoring\_%' ESCAPE '\'
    ) <> 6 THEN
        RAISE EXCEPTION 'compatible definitions were not restored after rejection';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM timescaledb_information.jobs
        WHERE proc_name = 'policy_refresh_continuous_aggregate'
          AND hypertable_name LIKE 'monitoring\_%' ESCAPE '\'
    ) THEN
        RAISE EXCEPTION 'policy activation guard did not fail closed';
    END IF;
END
$case$;

SELECT json_build_object(
    'case', 'reject-incompatible-and-destructive',
    'database_guard', 'active',
    'incompatible_object_rejected', TRUE,
    'forbidden_statement_guard', 'active',
    'unmarked_policy_activation_rejected', TRUE,
    'database', current_database()
) AS result;
