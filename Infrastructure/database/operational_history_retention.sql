\set ON_ERROR_STOP on

DO $guard$
BEGIN
    IF current_database() = 'cea_sensors'
       OR current_database() !~ '^monitoring_test_[a-z0-9_]+$'
       OR current_setting('app.operational_retention_disposable', TRUE) IS DISTINCT FROM '1' THEN
        RAISE EXCEPTION 'operational retention policy requires an explicitly marked disposable database';
    END IF;
END
$guard$;

SELECT add_retention_policy('automation_state', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('effective_setpoints', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('control_history', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('monitoring_automation_state_1min', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('monitoring_automation_state_5min', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('monitoring_effective_setpoints_1min', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('monitoring_effective_setpoints_5min', INTERVAL '30 days', if_not_exists => TRUE);

SELECT add_continuous_aggregate_policy(
    'monitoring_automation_state_1min',
    start_offset => INTERVAL '6 days',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE
);
SELECT add_continuous_aggregate_policy(
    'monitoring_automation_state_5min',
    start_offset => INTERVAL '6 days',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);
SELECT add_continuous_aggregate_policy(
    'monitoring_effective_setpoints_1min',
    start_offset => INTERVAL '6 days',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE
);
SELECT add_continuous_aggregate_policy(
    'monitoring_effective_setpoints_5min',
    start_offset => INTERVAL '6 days',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);
