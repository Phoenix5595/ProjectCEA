\set ON_ERROR_STOP on

CREATE TABLE control_history (
    timestamp TIMESTAMPTZ NOT NULL,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    device_name TEXT NOT NULL,
    channel INTEGER NOT NULL,
    old_state INTEGER,
    new_state INTEGER,
    mode TEXT NOT NULL,
    reason TEXT
);
SELECT create_hypertable('control_history', by_range('timestamp', INTERVAL '1 day'));

INSERT INTO automation_state (timestamp, location, cluster, device_name, device_state, device_mode, control_reason)
VALUES
    (NOW() - INTERVAL '15 days', 'Veg Room', 'main', 'old_relay', 0, 'auto', 'old'),
    (NOW() - INTERVAL '7 days' + INTERVAL '1 second', 'Veg Room', 'main', 'boundary_relay', 1, 'auto', 'boundary'),
    (NOW() - INTERVAL '38 days', 'Veg Room', 'main', 'old_aggregate', 0, 'auto', 'old aggregate'),
    (NOW() - INTERVAL '29 days', 'Veg Room', 'main', 'recent_aggregate', 1, 'auto', 'recent aggregate');
INSERT INTO effective_setpoints (timestamp, location, cluster)
VALUES
    (NOW() - INTERVAL '15 days', 'Raw Retention Old', 'main'),
    (NOW() - INTERVAL '7 days' + INTERVAL '1 second', 'Raw Retention Recent', 'main'),
    (NOW() - INTERVAL '38 days', 'Aggregate Room', 'main'),
    (NOW() - INTERVAL '29 days', 'Recent Aggregate Room', 'main');
INSERT INTO control_history (timestamp, location, cluster, device_name, channel, old_state, new_state, mode, reason)
VALUES
    (NOW() - INTERVAL '31 days', 'Veg Room', 'main', 'alarm:old', -1, 1, 0, 'alarm:clear:error', 'old'),
    (NOW() - INTERVAL '30 days' + INTERVAL '1 second', 'Veg Room', 'main', 'alarm:recent', -1, 0, 1, 'alarm:open:error', 'recent'),
    (NOW() - INTERVAL '1 day', 'Veg Room', 'main', 'heater_1', 0, 0, 1, 'manual', 'relay');

\ir ../../monitoring_read_models.sql
CALL refresh_continuous_aggregate('monitoring_automation_state_1min', NOW() - INTERVAL '32 days', NOW());
CALL refresh_continuous_aggregate('monitoring_automation_state_5min', NOW() - INTERVAL '32 days', NOW());
CALL refresh_continuous_aggregate('monitoring_effective_setpoints_1min', NOW() - INTERVAL '32 days', NOW());
CALL refresh_continuous_aggregate('monitoring_effective_setpoints_5min', NOW() - INTERVAL '32 days', NOW());
SELECT set_config('app.operational_retention_disposable', '1', FALSE);
\ir ../../operational_history_retention.sql
\ir ../../operational_history_retention.sql

DO $case$
DECLARE
    retention_count INTEGER;
    refresh_count INTEGER;
    retention_targets TEXT[];
    refresh_targets TEXT[];
    invalid_retention_policy_count INTEGER;
    invalid_refresh_policy_count INTEGER;
BEGIN
    SELECT count(*) INTO retention_count
    FROM timescaledb_information.jobs
    WHERE proc_name = 'policy_retention';
    IF retention_count <> 7 THEN
        RAISE EXCEPTION 'expected seven idempotent retention policies, got %', retention_count;
    END IF;

    SELECT array_agg(hypertable_name::TEXT ORDER BY hypertable_name)
    INTO retention_targets
    FROM timescaledb_information.jobs
    WHERE proc_name = 'policy_retention';
    IF retention_targets IS DISTINCT FROM ARRAY[
        'automation_state',
        'control_history',
        'effective_setpoints',
        'monitoring_automation_state_1min',
        'monitoring_automation_state_5min',
        'monitoring_effective_setpoints_1min',
        'monitoring_effective_setpoints_5min'
    ]::TEXT[] THEN
        RAISE EXCEPTION 'retention targets must remain control-only, got %', retention_targets;
    END IF;

    SELECT count(*) INTO invalid_retention_policy_count
    FROM timescaledb_information.jobs
    WHERE proc_name = 'policy_retention'
      AND (
          (hypertable_name IN ('automation_state', 'effective_setpoints')
           AND (config ->> 'drop_after')::INTERVAL <> INTERVAL '7 days')
          OR (hypertable_name NOT IN ('automation_state', 'effective_setpoints')
              AND (config ->> 'drop_after')::INTERVAL <> INTERVAL '30 days')
      );
    IF invalid_retention_policy_count <> 0 THEN
        RAISE EXCEPTION 'retention intervals must be seven-day raw and thirty-day control history';
    END IF;

    SELECT count(*) INTO refresh_count
    FROM timescaledb_information.jobs
    WHERE proc_name = 'policy_refresh_continuous_aggregate'
      AND hypertable_name IN (
          'monitoring_automation_state_1min',
          'monitoring_automation_state_5min',
          'monitoring_effective_setpoints_1min',
          'monitoring_effective_setpoints_5min'
      );
    IF refresh_count <> 4 THEN
        RAISE EXCEPTION 'expected four aggregate refresh policies, got %', refresh_count;
    END IF;

    SELECT array_agg(hypertable_name::TEXT ORDER BY hypertable_name)
    INTO refresh_targets
    FROM timescaledb_information.jobs
    WHERE proc_name = 'policy_refresh_continuous_aggregate';
    IF refresh_targets IS DISTINCT FROM ARRAY[
        'monitoring_automation_state_1min',
        'monitoring_automation_state_5min',
        'monitoring_effective_setpoints_1min',
        'monitoring_effective_setpoints_5min'
    ]::TEXT[] THEN
        RAISE EXCEPTION 'refresh targets must remain control aggregates, got %', refresh_targets;
    END IF;

    SELECT count(*) INTO invalid_refresh_policy_count
    FROM timescaledb_information.jobs
    WHERE proc_name = 'policy_refresh_continuous_aggregate'
      AND (config ->> 'start_offset')::INTERVAL <> INTERVAL '6 days';
    IF invalid_refresh_policy_count <> 0 THEN
        RAISE EXCEPTION 'control aggregate refresh start offset must remain six days';
    END IF;
END
$case$;

DO $run_retention$
DECLARE
    retention_job RECORD;
BEGIN
    FOR retention_job IN
        SELECT job_id
        FROM timescaledb_information.jobs
        WHERE proc_name = 'policy_retention'
    LOOP
        EXECUTE format('CALL run_job(%s)', retention_job.job_id);
    END LOOP;
END
$run_retention$;

DO $outcomes$
BEGIN
    IF EXISTS (SELECT 1 FROM automation_state WHERE device_name = 'old_relay') THEN
        RAISE EXCEPTION 'old automation_state row survived retention';
    END IF;
    IF EXISTS (SELECT 1 FROM effective_setpoints WHERE location = 'Raw Retention Old') THEN
        RAISE EXCEPTION 'old effective_setpoints row survived retention';
    END IF;
    IF EXISTS (SELECT 1 FROM control_history WHERE device_name = 'alarm:old') THEN
        RAISE EXCEPTION 'old control_history row survived retention';
    END IF;
    IF EXISTS (SELECT 1 FROM monitoring_automation_state_1min WHERE device_name = 'old_aggregate') THEN
        RAISE EXCEPTION 'old automation aggregate row survived retention';
    END IF;
    IF EXISTS (SELECT 1 FROM monitoring_effective_setpoints_1min WHERE location = 'Aggregate Room') THEN
        RAISE EXCEPTION 'old setpoint aggregate row survived retention';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM monitoring_automation_state_1min WHERE device_name = 'old_relay')
       OR NOT EXISTS (
           SELECT 1
           FROM monitoring_effective_setpoints_1min
           WHERE location = 'Raw Retention Old'
       ) THEN
        RAISE EXCEPTION 'monitoring control read models lost results after raw expiry';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM automation_state WHERE device_name = 'boundary_relay')
       OR NOT EXISTS (SELECT 1 FROM effective_setpoints WHERE location = 'Raw Retention Recent')
       OR NOT EXISTS (SELECT 1 FROM control_history WHERE device_name = 'alarm:recent')
       OR NOT EXISTS (SELECT 1 FROM monitoring_automation_state_1min WHERE device_name = 'recent_aggregate')
       OR NOT EXISTS (SELECT 1 FROM monitoring_effective_setpoints_1min WHERE location = 'Recent Aggregate Room') THEN
        RAISE EXCEPTION 'recent operational rows were removed by retention';
    END IF;
    IF (SELECT count(*) FROM control_history
        WHERE location = 'Veg Room' AND cluster = 'main' AND channel >= 0) <> 1 THEN
        RAISE EXCEPTION 'normal relay reads must exclude reserved channel -1 alarm rows';
    END IF;
END
$outcomes$;

SELECT json_build_object('case', 'operational-history-retention', 'retention_policies', 7) AS result;
