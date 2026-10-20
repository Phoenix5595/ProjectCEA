-- migration: downtime=true
\set ON_ERROR_STOP on


DO $phase4_preflight$
DECLARE
    cagg_count INTEGER;
    overview_refresh_count INTEGER;
    relay_job_count INTEGER;
BEGIN
    IF current_database() <> 'sj_cea'
       AND current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION '0007_time_column_rename may only run on sj_cea or a monitoring_test_ database (current database: %)',
            current_database();
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
            ('automation_state', 'timestamp'),
            ('effective_setpoints', 'timestamp'),
            ('control_history', 'timestamp'),
            ('pid_parameter_history', 'timestamp'),
            ('relay_observation', 'observed_at'),
            ('monitoring_room_photoperiod', 'observed_at'),
            ('monitoring_photoperiod_coverage', 'observed_at'),
            ('mode_transition_history', 'triggered_at')
        ) AS expected(table_name, column_name)
        WHERE NOT EXISTS (
            SELECT 1
            FROM information_schema.columns AS column_info
            WHERE column_info.table_schema = 'public'
              AND column_info.table_name = expected.table_name
              AND column_info.column_name = expected.column_name
              AND column_info.data_type = 'timestamp with time zone'
        ) OR EXISTS (
            SELECT 1
            FROM information_schema.columns AS column_info
            WHERE column_info.table_schema = 'public'
              AND column_info.table_name = expected.table_name
              AND column_info.column_name = 'time'
        )
    ) THEN
        RAISE EXCEPTION '0007_time_column_rename source columns drifted; refusing partial rename';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'measurement'
          AND column_name = 'time'
          AND data_type = 'timestamp with time zone'
    ) THEN
        RAISE EXCEPTION 'measurement.time is missing or has an unexpected type';
    END IF;

    SELECT count(*)
    INTO cagg_count
    FROM _timescaledb_catalog.continuous_agg
    WHERE user_view_schema = 'public'
      AND user_view_name = ANY (ARRAY[
          'monitoring_measurement_1min', 'monitoring_measurement_5min',
          'monitoring_measurement_1hour', 'monitoring_measurement_1day',
          'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
          'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
          'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
          'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
          'monitoring_relay_duration_1day'
      ]);
    IF cagg_count <> 13 THEN
        RAISE EXCEPTION '0007_time_column_rename expected all 13 monitoring aggregates, found %', cagg_count;
    END IF;

    SELECT count(*) FILTER (WHERE proc_name = 'monitoring_refresh_overview_incremental'),
           count(*) FILTER (WHERE proc_name = 'monitoring_rebuild_relay_overview')
    INTO overview_refresh_count, relay_job_count
    FROM timescaledb_information.jobs
    WHERE proc_schema = 'public'
      AND proc_name IN ('monitoring_refresh_overview_incremental',
                        'monitoring_rebuild_relay_overview');
    IF overview_refresh_count <> 3 OR relay_job_count <> 1 THEN
        RAISE EXCEPTION '0007_time_column_rename expected three incremental overview jobs and one relay rebuild job; found % and %',
            overview_refresh_count, relay_job_count;
    END IF;

    IF to_regclass('public.monitoring_index_snapshot_0007') IS NOT NULL
       OR to_regclass('public.monitoring_dependent_view_snapshot_0007') IS NOT NULL THEN
        RAISE EXCEPTION '0007_time_column_rename main-phase objects already exist';
    END IF;
END
$phase4_preflight$;
-- Snapshot and quiesce every Timescale job whose target 0007 changes. This
-- transaction commits before any aggregate/source DDL so the scheduler sees
-- the pause even if the following in-flight-worker wait times out.
BEGIN;
SET LOCAL search_path = public, pg_catalog;
CREATE TABLE IF NOT EXISTS public.monitoring_job_snapshot_0007 (
    job_id INTEGER PRIMARY KEY,
    proc_schema TEXT NOT NULL,
    proc_name TEXT NOT NULL,
    scheduled BOOLEAN NOT NULL,
    schedule_interval INTERVAL NOT NULL,
    max_runtime INTERVAL,
    max_retries INTEGER,
    retry_period INTERVAL,
    config JSONB NOT NULL,
    next_start TIMESTAMPTZ,
    target_schema TEXT,
    target_name TEXT,
    target_is_cagg BOOLEAN NOT NULL DEFAULT FALSE,
    restore_after_down BOOLEAN NOT NULL DEFAULT TRUE,
    check_schema TEXT,
    check_name TEXT,
    fixed_schedule BOOLEAN,
    initial_start TIMESTAMPTZ,
    timezone TEXT
);
CREATE TABLE IF NOT EXISTS public.monitoring_refresh_policy_snapshot_0007 (
    view_name TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL,
    scheduled BOOLEAN NOT NULL,
    schedule_interval INTERVAL NOT NULL,
    max_runtime INTERVAL,
    max_retries INTEGER,
    retry_period INTERVAL,
    config JSONB NOT NULL,
    next_start TIMESTAMPTZ,
    check_schema TEXT,
    check_name TEXT,
    fixed_schedule BOOLEAN,
    initial_start TIMESTAMPTZ,
    timezone TEXT
);
WITH cagg_targets AS (
    SELECT cagg.user_view_schema, cagg.user_view_name, cagg.mat_hypertable_id
    FROM _timescaledb_catalog.continuous_agg AS cagg
    WHERE cagg.user_view_schema = 'public'
      AND cagg.user_view_name = ANY (ARRAY[
          'monitoring_measurement_1min', 'monitoring_measurement_5min',
          'monitoring_measurement_1hour', 'monitoring_measurement_1day',
          'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
          'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
          'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
          'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
          'monitoring_relay_duration_1day'
      ])
),
job_targets AS (
    SELECT job.job_id, job.proc_schema, job.proc_name, job.scheduled,
           job.schedule_interval, job.max_runtime, job.max_retries,
           job.retry_period, COALESCE(job.config, '{}'::JSONB) AS config,
           stats.next_start, job.check_schema::TEXT AS check_schema,
           job.check_name::TEXT AS check_name,
           job.fixed_schedule, job.initial_start, config_job.timezone,
           CASE WHEN cagg.user_view_name IS NOT NULL
                THEN cagg.user_view_schema ELSE job.hypertable_schema END AS target_schema,
           CASE WHEN cagg.user_view_name IS NOT NULL
                THEN cagg.user_view_name ELSE job.hypertable_name END AS target_name,
           cagg.user_view_name IS NOT NULL AS target_is_cagg,
           job.proc_name <> 'monitoring_backfill_aggregates' AS restore_after_down
    FROM timescaledb_information.jobs AS job
    LEFT JOIN timescaledb_information.job_stats AS stats USING (job_id)
    LEFT JOIN _timescaledb_config.bgw_job AS config_job
      ON config_job.id = job.job_id
    LEFT JOIN cagg_targets AS cagg
      ON cagg.mat_hypertable_id = CASE
          WHEN job.proc_name IN (
              'policy_refresh_continuous_aggregate',
              'policy_compression', 'policy_columnstore', 'policy_retention')
          THEN COALESCE(
              NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
              NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
          ELSE NULL
      END
    WHERE (job.proc_schema = 'public'
           AND job.proc_name IN (
               'monitoring_refresh_overview_incremental',
               'monitoring_rebuild_relay_overview',
               'monitoring_backfill_aggregates'))
       OR (job.proc_name IN ('policy_compression', 'policy_columnstore', 'policy_retention')
           AND (
               (job.hypertable_schema = 'public'
                AND job.hypertable_name = ANY (ARRAY[
                    'automation_state', 'effective_setpoints', 'control_history',
                    'pid_parameter_history', 'relay_observation',
                    'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
                    'mode_transition_history'
                ]))
               OR cagg.user_view_name IS NOT NULL
           ))
       OR (job.proc_schema = '_timescaledb_functions'
           AND job.proc_name = 'policy_refresh_continuous_aggregate'
           AND cagg.user_view_name = ANY (ARRAY[
               'monitoring_measurement_1min', 'monitoring_relay_duration_1day'
           ]))
)
INSERT INTO public.monitoring_job_snapshot_0007 (
    job_id, proc_schema, proc_name, scheduled, schedule_interval, max_runtime,
    max_retries, retry_period, config, next_start, target_schema, target_name,
    target_is_cagg, restore_after_down, check_schema, check_name, fixed_schedule,
    initial_start, timezone)
SELECT job_id, proc_schema, proc_name, scheduled, schedule_interval, max_runtime,
       max_retries, retry_period, config, next_start, target_schema, target_name,
       target_is_cagg, restore_after_down, check_schema, check_name,
       fixed_schedule, initial_start, timezone
FROM job_targets
ON CONFLICT (job_id) DO NOTHING;

INSERT INTO public.monitoring_refresh_policy_snapshot_0007 (
    view_name, job_id, scheduled, schedule_interval, max_runtime, max_retries,
    retry_period, config, next_start, check_schema, check_name, fixed_schedule,
    initial_start, timezone)
SELECT cagg.user_view_name AS view_name, job.job_id, job.scheduled,
       job.schedule_interval, job.max_runtime, job.max_retries,
       job.retry_period, COALESCE(job.config, '{}'::JSONB), stats.next_start,
       job.check_schema::TEXT, job.check_name::TEXT, job.fixed_schedule,
       job.initial_start, config_job.timezone
FROM _timescaledb_catalog.continuous_agg AS cagg
JOIN timescaledb_information.jobs AS job
  ON COALESCE(
       NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
       NULLIF(job.config ->> 'hypertable_id', '')::INTEGER) = cagg.mat_hypertable_id
LEFT JOIN timescaledb_information.job_stats AS stats USING (job_id)
LEFT JOIN _timescaledb_config.bgw_job AS config_job
  ON config_job.id = job.job_id
WHERE cagg.user_view_schema = 'public'
  AND cagg.user_view_name = ANY (ARRAY[
      'monitoring_measurement_5min',
      'monitoring_measurement_1hour', 'monitoring_measurement_1day',
      'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
      'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
      'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
      'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day'
  ])
  AND job.proc_schema = '_timescaledb_functions'
  AND job.proc_name = 'policy_refresh_continuous_aggregate'
ON CONFLICT (view_name) DO NOTHING;
DO $unschedule_phase4_jobs$
BEGIN
    PERFORM alter_job(job.job_id, scheduled => FALSE)
    FROM timescaledb_information.jobs AS job
    WHERE job.job_id IN (
        SELECT job_id FROM public.monitoring_job_snapshot_0007
        UNION
        SELECT job_id FROM public.monitoring_refresh_policy_snapshot_0007
    );
END
$unschedule_phase4_jobs$;
COMMIT;

DO $wait_for_phase4_jobs$
DECLARE
    jobs_running BOOLEAN;
    wait_timeout INTERVAL := INTERVAL '10 minutes';
    test_wait_timeout TEXT;
    wait_deadline TIMESTAMPTZ;
    running_job_details TEXT;
BEGIN
    IF current_database() ~ '^monitoring_test_[a-z0-9_]+$' THEN
        test_wait_timeout := NULLIF(
            current_setting('app.time_rename_test_quiesce_timeout', TRUE), '');
        IF test_wait_timeout IS NOT NULL THEN
            wait_timeout := test_wait_timeout::INTERVAL;
        END IF;
    END IF;
    wait_deadline := clock_timestamp() + wait_timeout;

    LOOP
        WITH target_jobs AS (
            SELECT job_id FROM public.monitoring_job_snapshot_0007
            UNION
            SELECT job_id FROM public.monitoring_refresh_policy_snapshot_0007
        )
        SELECT EXISTS (
            SELECT 1
            FROM target_jobs AS target
            WHERE EXISTS (
                SELECT 1
                FROM pg_stat_activity AS activity
                WHERE activity.datname = current_database()
                  AND activity.backend_type <> 'client backend'
                  AND activity.application_name LIKE
                      '%[' || target.job_id::TEXT || ']%'
            )
        )
        INTO jobs_running;
        EXIT WHEN NOT jobs_running;
        IF clock_timestamp() >= wait_deadline THEN
            WITH target_jobs AS (
                SELECT job_id, proc_schema, proc_name, target_schema, target_name
                FROM public.monitoring_job_snapshot_0007
                UNION
                SELECT job_id, '_timescaledb_functions'::TEXT,
                       'policy_refresh_continuous_aggregate'::TEXT,
                       'public'::TEXT, view_name::TEXT
                FROM public.monitoring_refresh_policy_snapshot_0007
            )
            SELECT string_agg(
                format(
                    'job_id=%s proc_name=%s.%s hypertable/view=%s worker_pid=%s worker_app=%s runtime=%s',
                    target.job_id,
                    COALESCE(target.proc_schema, job.proc_schema::TEXT, '<unknown>'),
                    COALESCE(target.proc_name, job.proc_name::TEXT, '<unknown>'),
                    CASE
                        WHEN COALESCE(target.target_name, job.hypertable_name::TEXT) IS NULL
                            THEN '<none>'
                        ELSE format(
                            '%I.%I',
                            COALESCE(target.target_schema, job.hypertable_schema::TEXT, 'public'),
                            COALESCE(target.target_name, job.hypertable_name::TEXT))
                    END,
                    COALESCE(worker.pid::TEXT, 'unknown'),
                    COALESCE(worker.application_name::TEXT, 'unknown'),
                    COALESCE(
                        (clock_timestamp() - stats.last_run_started_at)::TEXT,
                        (clock_timestamp() - worker.backend_start)::TEXT,
                        'unknown')),
                E'\n' ORDER BY target.job_id)
            INTO running_job_details
            FROM target_jobs AS target
            LEFT JOIN timescaledb_information.jobs AS job USING (job_id)
            LEFT JOIN timescaledb_information.job_stats AS stats USING (job_id)
            LEFT JOIN LATERAL (
                SELECT activity.pid, activity.backend_start, activity.application_name
                FROM pg_stat_activity AS activity
                WHERE activity.datname = current_database()
                  AND activity.backend_type <> 'client backend'
                  AND activity.application_name LIKE
                      '%[' || target.job_id::TEXT || ']%'
                ORDER BY activity.pid
                LIMIT 1
            ) AS worker ON TRUE
            WHERE worker.pid IS NOT NULL;
            IF running_job_details IS NULL THEN
                EXIT;
            END IF;
            RAISE EXCEPTION 'migration_jobs_still_running'
                USING DETAIL = running_job_details,
                      HINT = 'Jobs are unscheduled and snapshotted; let these workers finish (do not kill them), then re-run the migration.';
        END IF;
        PERFORM pg_sleep(1);
    END LOOP;
END
$wait_for_phase4_jobs$;

DO $remove_stale_backfill_jobs$
DECLARE
    local_job_id INTEGER;
BEGIN
    FOR local_job_id IN
        SELECT job_id
        FROM timescaledb_information.jobs
        WHERE proc_schema = 'public'
          AND proc_name = 'monitoring_backfill_aggregates'
    LOOP
        PERFORM delete_job(local_job_id);
    END LOOP;
END
$remove_stale_backfill_jobs$;
BEGIN;
SET LOCAL lock_timeout = '0';
SET LOCAL statement_timeout = '0';
SET LOCAL search_path = public, pg_catalog;

CREATE TABLE public.monitoring_index_snapshot_0007 (
    table_name TEXT NOT NULL,
    old_index_name TEXT PRIMARY KEY,
    new_index_name TEXT NOT NULL,
    old_constraint_name TEXT
);
INSERT INTO public.monitoring_index_snapshot_0007 (
    table_name, old_index_name, new_index_name, old_constraint_name)
SELECT indexes.tablename,
       indexes.indexname,
       replace(replace(indexes.indexname, 'timestamp', 'time'), 'observed_at', 'time'),
       constraint_info.conname
FROM pg_indexes AS indexes
JOIN pg_class AS index_class
  ON index_class.relname = indexes.indexname
 AND index_class.relnamespace = 'public'::regnamespace
LEFT JOIN pg_constraint AS constraint_info
  ON constraint_info.conindid = index_class.oid
 AND constraint_info.conrelid = format('public.%I', indexes.tablename)::regclass
WHERE indexes.schemaname = 'public'
  AND indexes.tablename = ANY (ARRAY[
      'automation_state', 'effective_setpoints', 'control_history',
      'pid_parameter_history', 'relay_observation',
      'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
      'mode_transition_history'
  ])
  AND (indexes.indexname LIKE '%timestamp%'
       OR indexes.indexname LIKE '%observed_at%')
  AND indexes.tablename <> 'automation_state';

DO $snapshot_shape$
BEGIN
    IF (SELECT count(*) FROM public.monitoring_job_snapshot_0007
        WHERE proc_schema = 'public'
          AND proc_name IN ('monitoring_refresh_overview_incremental',
                            'monitoring_rebuild_relay_overview')) <> 4 THEN
        RAISE EXCEPTION '0007_time_column_rename did not snapshot all four overview jobs';
    END IF;
    IF (SELECT count(*) FROM public.monitoring_refresh_policy_snapshot_0007
        WHERE view_name = ANY (ARRAY[
            'monitoring_measurement_5min',
            'monitoring_measurement_1hour', 'monitoring_measurement_1day',
            'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
            'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
            'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
            'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day'
        ])) <> 11 THEN
        RAISE EXCEPTION '0007_time_column_rename expected eleven refresh policies for rebuilt aggregates, found %',
            (SELECT count(*) FROM public.monitoring_refresh_policy_snapshot_0007);
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.monitoring_index_snapshot_0007 AS snapshot
        JOIN pg_class AS existing_index
          ON existing_index.relnamespace = 'public'::regnamespace
         AND existing_index.relname = snapshot.new_index_name
    ) THEN
        RAISE EXCEPTION '0007_time_column_rename index rename target already exists';
    END IF;
END
$snapshot_shape$;

DO $pause_overview_jobs$
DECLARE
    snapshot_row RECORD;
BEGIN
    FOR snapshot_row IN
        SELECT snapshot.job_id
        FROM public.monitoring_job_snapshot_0007 AS snapshot
        JOIN timescaledb_information.jobs AS job USING (job_id)
        ORDER BY snapshot.job_id
    LOOP
        PERFORM alter_job(snapshot_row.job_id, scheduled => FALSE);
    END LOOP;

    FOR snapshot_row IN
        SELECT snapshot.job_id
        FROM public.monitoring_refresh_policy_snapshot_0007 AS snapshot
        JOIN timescaledb_information.jobs AS job USING (job_id)
        ORDER BY snapshot.job_id
    LOOP
        PERFORM alter_job(snapshot_row.job_id, scheduled => FALSE);
    END LOOP;
END
$pause_overview_jobs$;

-- Snapshot the full regular-view dependency closure rooted at every rebuilt
-- CAGG and every source table whose event-time column is renamed. Record the
-- greatest dependency depth so teardown is deepest-first and rollback is
-- shallowest-first; CAGG wrapper views and TimescaleDB internals are excluded.
CREATE TABLE public.monitoring_dependent_view_snapshot_0007 (
    view_schema TEXT NOT NULL,
    view_name TEXT NOT NULL,
    view_definition TEXT NOT NULL,
    dependency_depth INTEGER NOT NULL CHECK (dependency_depth > 0),
    view_owner TEXT NOT NULL,
    view_owner_oid OID NOT NULL,
    view_acl ACLITEM[],
    PRIMARY KEY (view_schema, view_name)
);
WITH RECURSIVE seed_relations AS (
    SELECT relation.oid AS relation_oid
    FROM pg_class AS relation
    JOIN pg_namespace AS relation_namespace
      ON relation_namespace.oid = relation.relnamespace
    WHERE relation_namespace.nspname = 'public'
      AND relation.relname = ANY (ARRAY[
          'monitoring_light_intensity_1day',
          'monitoring_requested_pid_1day',
          'monitoring_measurement_1day',
          'monitoring_light_intensity_1hour',
          'monitoring_requested_pid_1hour',
          'monitoring_measurement_1hour',
          'monitoring_measurement_5min',
          'monitoring_effective_setpoints_5min',
          'monitoring_effective_setpoints_1min',
          'monitoring_automation_state_5min',
          'monitoring_automation_state_1min',
          'automation_state', 'effective_setpoints', 'control_history',
          'pid_parameter_history', 'relay_observation',
          'monitoring_room_photoperiod', 'monitoring_photoperiod_coverage',
          'mode_transition_history'
      ])
      AND relation.relkind IN ('m', 'p', 'r', 'v')
),
dependent_views(view_oid, dependency_depth, path) AS (
    SELECT view_class.oid,
           1,
           ARRAY[seed.relation_oid, view_class.oid]::OID[]
    FROM seed_relations AS seed
    JOIN pg_depend AS dependency
      ON dependency.refclassid = 'pg_class'::REGCLASS
     AND dependency.refobjid = seed.relation_oid
     AND dependency.classid = 'pg_rewrite'::REGCLASS
     AND dependency.deptype = 'n'
    JOIN pg_rewrite AS view_rule
      ON view_rule.oid = dependency.objid
     AND view_rule.rulename = '_RETURN'
    JOIN pg_class AS view_class
      ON view_class.oid = view_rule.ev_class
     AND view_class.relkind = 'v'
    JOIN pg_namespace AS view_namespace
      ON view_namespace.oid = view_class.relnamespace
    WHERE view_namespace.nspname NOT IN ('pg_catalog', 'information_schema')
      AND view_namespace.nspname !~ '^_timescaledb_'
      AND NOT EXISTS (
          SELECT 1
          FROM _timescaledb_catalog.continuous_agg AS own_cagg
          WHERE own_cagg.user_view_schema = view_namespace.nspname
            AND own_cagg.user_view_name = view_class.relname
      )

    UNION ALL

    SELECT view_class.oid,
           parent.dependency_depth + 1,
           parent.path || view_class.oid
    FROM dependent_views AS parent
    JOIN pg_depend AS dependency
      ON dependency.refclassid = 'pg_class'::REGCLASS
     AND dependency.refobjid = parent.view_oid
     AND dependency.classid = 'pg_rewrite'::REGCLASS
     AND dependency.deptype = 'n'
    JOIN pg_rewrite AS view_rule
      ON view_rule.oid = dependency.objid
     AND view_rule.rulename = '_RETURN'
    JOIN pg_class AS view_class
      ON view_class.oid = view_rule.ev_class
     AND view_class.relkind = 'v'
    JOIN pg_namespace AS view_namespace
      ON view_namespace.oid = view_class.relnamespace
    WHERE NOT (view_class.oid = ANY (parent.path))
      AND view_namespace.nspname NOT IN ('pg_catalog', 'information_schema')
      AND view_namespace.nspname !~ '^_timescaledb_'
      AND NOT EXISTS (
          SELECT 1
          FROM _timescaledb_catalog.continuous_agg AS own_cagg
          WHERE own_cagg.user_view_schema = view_namespace.nspname
            AND own_cagg.user_view_name = view_class.relname
      )
),
view_depths AS (
    SELECT view_oid, max(dependency_depth)::INTEGER AS dependency_depth
    FROM dependent_views
    GROUP BY view_oid
)
INSERT INTO public.monitoring_dependent_view_snapshot_0007 (
    view_schema, view_name, view_definition, dependency_depth,
    view_owner, view_owner_oid, view_acl)
SELECT view_namespace.nspname,
       view_class.relname,
       pg_get_viewdef(view_class.oid, TRUE),
       view_depths.dependency_depth,
       pg_get_userbyid(view_class.relowner),
       view_class.relowner,
       view_class.relacl
FROM view_depths
JOIN pg_class AS view_class
  ON view_class.oid = view_depths.view_oid
JOIN pg_namespace AS view_namespace
  ON view_namespace.oid = view_class.relnamespace;
DO $drop_dependent_views$
DECLARE
    snapshot_row RECORD;
BEGIN
    FOR snapshot_row IN
        SELECT view_schema, view_name
        FROM public.monitoring_dependent_view_snapshot_0007
        ORDER BY dependency_depth DESC, view_schema, view_name
    LOOP
        EXECUTE format('DROP VIEW %I.%I',
                       snapshot_row.view_schema, snapshot_row.view_name);
    END LOOP;
END
$drop_dependent_views$;

-- Dependents first. The 1-minute measurement aggregate and relay-duration day
-- aggregate do not depend on a renamed source column and remain in place.
-- A scheduled policy worker (or the launcher updating catalog rows while it
-- starts a job) can be mid-commit on these catalog rows, so a concurrent
-- catalog update surfaces as 'tuple concurrently deleted'. Every drop is
-- retried briefly inside this transaction before giving up.
DO $drop_renamed_aggregates$
DECLARE
    dropped_view TEXT;
    attempt INTEGER;
BEGIN
    FOREACH dropped_view IN ARRAY ARRAY[
        'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1day',
        'monitoring_measurement_1day',
        'monitoring_light_intensity_1hour',
        'monitoring_requested_pid_1hour',
        'monitoring_measurement_1hour',
        'monitoring_measurement_5min',
        'monitoring_effective_setpoints_5min',
        'monitoring_effective_setpoints_1min',
        'monitoring_automation_state_5min',
        'monitoring_automation_state_1min'
    ] LOOP
        FOR attempt IN 1..5 LOOP
            BEGIN
                EXECUTE format('DROP MATERIALIZED VIEW public.%I', dropped_view);
                EXIT;
            EXCEPTION WHEN OTHERS THEN
                IF SQLERRM NOT LIKE '%tuple concurrently deleted%'
                   OR attempt = 5 THEN
                    RAISE;
                END IF;
                PERFORM pg_sleep(0.5);
            END;
        END LOOP;
    END LOOP;
END
$drop_renamed_aggregates$;

DO $rename_event_columns$
DECLARE
    source_column RECORD;
BEGIN
    FOR source_column IN
        SELECT * FROM (VALUES
            ('automation_state', 'timestamp'),
            ('effective_setpoints', 'timestamp'),
            ('control_history', 'timestamp'),
            ('pid_parameter_history', 'timestamp'),
            ('relay_observation', 'observed_at'),
            ('monitoring_room_photoperiod', 'observed_at'),
            ('monitoring_photoperiod_coverage', 'observed_at'),
            ('mode_transition_history', 'triggered_at')
        ) AS columns_to_rename(table_name, old_column)
    LOOP
        EXECUTE format('ALTER TABLE public.%I RENAME COLUMN %I TO %I',
                       source_column.table_name, source_column.old_column, 'time');
    END LOOP;
END
$rename_event_columns$;

DO $rename_event_indexes$
DECLARE
    index_row RECORD;
BEGIN
    FOR index_row IN
        SELECT table_name, old_index_name, new_index_name, old_constraint_name
        FROM public.monitoring_index_snapshot_0007
        ORDER BY old_index_name
    LOOP
        IF index_row.old_constraint_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I RENAME CONSTRAINT %I TO %I',
                           index_row.table_name,
                           index_row.old_constraint_name,
                           index_row.new_index_name);
        ELSE
            EXECUTE format('ALTER INDEX public.%I RENAME TO %I',
                           index_row.old_index_name,
                           index_row.new_index_name);
        END IF;
    END LOOP;
END
$rename_event_indexes$;

-- TimescaleDB 2.28.3 permits renames on compressed hypertables. It permits
-- adding NOT VALID CHECK constraints, but VALIDATE CONSTRAINT is unsupported
-- on columnstore-enabled hypertables, so prove existing rows explicitly and
-- leave PostgreSQL to enforce new writes.
DO $event_time_bounds$
DECLARE
    source_table RECORD;
    is_compressed_hypertable BOOLEAN;
    invalid_row_count BIGINT;
    lower_bound TIMESTAMPTZ := '2020-01-01'::TIMESTAMPTZ;
    constraint_name TEXT;
BEGIN
    FOR source_table IN
        SELECT table_name FROM (VALUES
            ('automation_state'),
            ('effective_setpoints'),
            ('control_history'),
            ('pid_parameter_history'),
            ('relay_observation'),
            ('monitoring_room_photoperiod'),
            ('monitoring_photoperiod_coverage'),
            ('mode_transition_history'),
            ('measurement')
        ) AS source_tables(table_name)
    LOOP
        SELECT hypertable.compression_enabled
        INTO is_compressed_hypertable
        FROM timescaledb_information.hypertables AS hypertable
        WHERE hypertable.hypertable_schema = 'public'
          AND hypertable.hypertable_name = source_table.table_name;

        constraint_name := source_table.table_name || '_time_bounds';
        IF COALESCE(is_compressed_hypertable, FALSE) THEN
            EXECUTE format(
                'SELECT count(*) FROM public.%I WHERE "time" IS NOT NULL AND ("time" < $1 OR "time" > now() + INTERVAL ''1 day'')',
                source_table.table_name)
            INTO invalid_row_count
            USING lower_bound;
            IF invalid_row_count <> 0 THEN
                RAISE EXCEPTION 'time_bounds_existing_rows_out_of_range: table=public.% count=%',
                    source_table.table_name, invalid_row_count;
            END IF;

            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK ("time" >= %L::TIMESTAMPTZ AND "time" <= now() + INTERVAL ''1 day'') NOT VALID',
                source_table.table_name, constraint_name, lower_bound);
        ELSE
            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK ("time" >= %L::TIMESTAMPTZ AND "time" <= now() + INTERVAL ''1 day'') NOT VALID',
                source_table.table_name, constraint_name, lower_bound);
            EXECUTE format('ALTER TABLE public.%I VALIDATE CONSTRAINT %I',
                           source_table.table_name, constraint_name);
        END IF;
    END LOOP;
END
$event_time_bounds$;

-- The runner invokes psql -f with an absolute migration path and no cwd
-- override. \\ir resolves these includes relative to this migration file.
SET LOCAL app.grow_cycle_history_authorized = '1';
SET LOCAL app.grow_cycle_history_disposable = '1';
\set monitoring_time_rename_migration true
\ir ../monitoring_read_models.sql
\ir ../monitoring_history_overview.sql
DO $restore_canonical_view_security$
DECLARE
    snapshot_row RECORD;
    acl_entry RECORD;
    grantee_sql TEXT;
BEGIN
    -- Canonical files recreated only views they own. Restore the saved access
    -- metadata on those objects without recreating any missing orphan views.
    FOR snapshot_row IN
        SELECT snapshot.view_schema, snapshot.view_name, snapshot.view_owner,
               snapshot.view_owner_oid, snapshot.view_acl,
               relation.oid AS existing_oid, relation.relowner AS existing_owner
        FROM public.monitoring_dependent_view_snapshot_0007 AS snapshot
        JOIN pg_namespace AS namespace
          ON namespace.nspname = snapshot.view_schema
        JOIN pg_class AS relation
          ON relation.relnamespace = namespace.oid
         AND relation.relname = snapshot.view_name
         AND relation.relkind = 'v'
        ORDER BY snapshot.dependency_depth, snapshot.view_schema, snapshot.view_name
    LOOP
        FOR acl_entry IN
            SELECT exploded.grantee, exploded.privilege_type, exploded.is_grantable
            FROM aclexplode(COALESCE(
                snapshot_row.view_acl,
                acldefault('r', snapshot_row.view_owner_oid))) AS exploded
            WHERE exploded.grantee <> snapshot_row.view_owner_oid
        LOOP
            IF NOT EXISTS (
                SELECT 1
                FROM pg_class AS current_view
                CROSS JOIN LATERAL aclexplode(COALESCE(
                    current_view.relacl,
                    acldefault('r', current_view.relowner))) AS current_acl
                WHERE current_view.oid = snapshot_row.existing_oid
                  AND current_acl.grantee = acl_entry.grantee
                  AND current_acl.privilege_type = acl_entry.privilege_type
                  AND current_acl.is_grantable = acl_entry.is_grantable
            ) THEN
                grantee_sql := CASE
                    WHEN acl_entry.grantee = 0 THEN 'PUBLIC'
                    ELSE format('%I', pg_get_userbyid(acl_entry.grantee))
                END;
                EXECUTE format(
                    'GRANT %s ON TABLE %I.%I TO %s%s',
                    acl_entry.privilege_type,
                    snapshot_row.view_schema,
                    snapshot_row.view_name,
                    grantee_sql,
                    CASE WHEN acl_entry.is_grantable
                         THEN ' WITH GRANT OPTION' ELSE '' END);
            END IF;
        END LOOP;

        IF snapshot_row.existing_owner IS DISTINCT FROM snapshot_row.view_owner_oid THEN
            EXECUTE format('ALTER VIEW %I.%I OWNER TO %I',
                           snapshot_row.view_schema,
                           snapshot_row.view_name,
                           snapshot_row.view_owner);
        END IF;
    END LOOP;
END
$restore_canonical_view_security$;
DO $report_noncanonical_dependent_views$
DECLARE
    missing_views TEXT;
BEGIN
    SELECT string_agg(
               format('%I.%I', snapshot.view_schema, snapshot.view_name),
               ', ' ORDER BY snapshot.view_schema, snapshot.view_name)
    INTO missing_views
    FROM public.monitoring_dependent_view_snapshot_0007 AS snapshot
    LEFT JOIN pg_namespace AS namespace
      ON namespace.nspname = snapshot.view_schema
    LEFT JOIN pg_class AS relation
      ON relation.relnamespace = namespace.oid
     AND relation.relname = snapshot.view_name
    WHERE relation.oid IS NULL
       OR relation.relkind <> 'v';

    IF missing_views IS NOT NULL THEN
        RAISE NOTICE '0007_time_column_rename dropped non-canonical dependent views (not recreated): %',
            missing_views;
    END IF;
END
$report_noncanonical_dependent_views$;
SET LOCAL lock_timeout = '0';
SET LOCAL statement_timeout = '0';

-- The six base policies are rebuilt paused as well; the backfill job enables
-- every policy only after all eleven views have reached their frontier.
DO $base_refresh_policies$
DECLARE
    policy_spec RECORD;
    refresh_job_id INTEGER;
BEGIN
    FOR policy_spec IN
        SELECT * FROM (VALUES
            ('monitoring_measurement_5min', INTERVAL '7 days', INTERVAL '5 minutes', INTERVAL '5 minutes'),
            ('monitoring_effective_setpoints_1min', INTERVAL '7 days', INTERVAL '1 minute', INTERVAL '1 minute'),
            ('monitoring_effective_setpoints_5min', INTERVAL '7 days', INTERVAL '5 minutes', INTERVAL '5 minutes'),
            ('monitoring_automation_state_1min', INTERVAL '7 days', INTERVAL '1 minute', INTERVAL '1 minute'),
            ('monitoring_automation_state_5min', INTERVAL '7 days', INTERVAL '5 minutes', INTERVAL '5 minutes')
        ) AS policies(view_name, start_offset, end_offset, schedule_interval)
    LOOP
        refresh_job_id := add_continuous_aggregate_policy(
            format('public.%I', policy_spec.view_name)::REGCLASS,
            start_offset => policy_spec.start_offset,
            end_offset => policy_spec.end_offset,
            schedule_interval => policy_spec.schedule_interval,
            if_not_exists => FALSE);
        IF refresh_job_id IS NULL OR refresh_job_id < 1 THEN
            RAISE EXCEPTION 'failed to create paused refresh policy for %', policy_spec.view_name;
        END IF;
        PERFORM alter_job(refresh_job_id,
                          max_runtime => INTERVAL '60 seconds',
                          max_retries => 0,
                          scheduled => FALSE);
    END LOOP;
END
$base_refresh_policies$;

DO $aggregate_compression$
DECLARE
    view_name TEXT;
    materialization_id INTEGER;
    compression_job_id INTEGER;
    compression_job_count INTEGER;
BEGIN
    FOREACH view_name IN ARRAY ARRAY[
        'monitoring_measurement_1min', 'monitoring_measurement_5min',
        'monitoring_measurement_1hour', 'monitoring_measurement_1day',
        'monitoring_effective_setpoints_1min', 'monitoring_effective_setpoints_5min',
        'monitoring_automation_state_1min', 'monitoring_automation_state_5min',
        'monitoring_light_intensity_1hour', 'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1hour', 'monitoring_requested_pid_1day',
        'monitoring_relay_duration_1day'
    ] LOOP
        EXECUTE format('ALTER MATERIALIZED VIEW public.%I SET (timescaledb.compress = true)',
                       view_name);
        PERFORM add_compression_policy(
            format('public.%I', view_name)::REGCLASS,
            compress_after => INTERVAL '3 months',
            if_not_exists => TRUE);

        SELECT cagg.mat_hypertable_id
        INTO materialization_id
        FROM _timescaledb_catalog.continuous_agg AS cagg
        WHERE cagg.user_view_schema = 'public'
          AND cagg.user_view_name = view_name;
        SELECT count(*)
        INTO compression_job_count
        FROM timescaledb_information.jobs AS job
        WHERE job.proc_schema = '_timescaledb_functions'
          AND job.proc_name IN ('policy_compression', 'policy_columnstore')
          AND COALESCE(
                  NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
                  NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
              = materialization_id;
        IF compression_job_count < 1 THEN
            RAISE EXCEPTION '0007_time_column_rename missing compression policy for %',
                view_name;
        END IF;
        FOR compression_job_id IN
            SELECT job.job_id
            FROM timescaledb_information.jobs AS job
            WHERE job.proc_schema = '_timescaledb_functions'
              AND job.proc_name IN ('policy_compression', 'policy_columnstore')
              AND COALESCE(
                      NULLIF(job.config ->> 'mat_hypertable_id', '')::INTEGER,
                      NULLIF(job.config ->> 'hypertable_id', '')::INTEGER)
                  = materialization_id
        LOOP
            PERFORM alter_job(compression_job_id, scheduled => FALSE);
        END LOOP;
    END LOOP;
END
$aggregate_compression$;


DO $create_backfill_job$
DECLARE
    source_minimum TIMESTAMPTZ;
    start_cursor TIMESTAMPTZ;
    configured JSONB;
    new_job_id INTEGER;
    backfill_views TEXT[] := ARRAY[
        'monitoring_measurement_5min',
        'monitoring_measurement_1hour',
        'monitoring_measurement_1day',
        'monitoring_effective_setpoints_1min',
        'monitoring_effective_setpoints_5min',
        'monitoring_automation_state_1min',
        'monitoring_automation_state_5min',
        'monitoring_light_intensity_1hour',
        'monitoring_light_intensity_1day',
        'monitoring_requested_pid_1hour',
        'monitoring_requested_pid_1day'
    ];
BEGIN
    SELECT min(source_time)
    INTO source_minimum
    FROM (
        SELECT min("time") AS source_time FROM public.measurement
        UNION ALL
        SELECT min("time") FROM public.effective_setpoints
        UNION ALL
        SELECT min("time") FROM public.automation_state
    ) AS source_minima;
    IF source_minimum IS NULL THEN
        RAISE EXCEPTION 'monitoring_backfill_no_source_rows';
    END IF;

    start_cursor := time_bucket(INTERVAL '1 day', source_minimum);
    configured := jsonb_build_object(
        'views', to_jsonb(backfill_views),
        'start', start_cursor,
        'cursor', start_cursor,
        'done', FALSE);
    new_job_id := add_job(
        'public.monitoring_backfill_aggregates(integer,jsonb)'::REGPROCEDURE::REGPROC,
        schedule_interval => INTERVAL '1 minute',
        config => configured,
        initial_start => clock_timestamp() + INTERVAL '1 minute',
        scheduled => TRUE,
        job_name => 'monitoring_backfill_aggregates_0007');
    IF new_job_id IS NULL OR new_job_id < 1 THEN
        RAISE EXCEPTION 'add_job returned no id for monitoring_backfill_aggregates';
    END IF;
    PERFORM alter_job(new_job_id,
                      max_runtime => INTERVAL '10 minutes',
                      max_retries => -1,
                      scheduled => TRUE);
END
$create_backfill_job$;

COMMIT;
