\set ON_ERROR_STOP on

DO $case$
DECLARE
    winning_intensity DOUBLE PRECISION;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM automation_state
        WHERE location = 'Flower Room'
          AND device_name = 'heater_f_1'
          AND pid_output IS NULL
    ) THEN
        RAISE EXCEPTION 'nullable PID control fixture is missing';
    END IF;

    SELECT target_intensity
    INTO winning_intensity
    FROM light_programs
    WHERE location = 'Flower Room'
      AND cluster = 'main'
      AND enabled
    ORDER BY priority DESC, created_at ASC
    LIMIT 1;

    IF winning_intensity <> 90.0 THEN
        RAISE EXCEPTION 'light program precedence fixture is invalid';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM mode_parameters
        WHERE location = 'Flower Room'
          AND day_start_time > night_start_time
    ) THEN
        RAISE EXCEPTION 'overnight photoperiod fixture is missing';
    END IF;
END
$case$;

SELECT json_build_object(
    'case', 'control-history-tiers-and-precedence',
    'effective_rows', (SELECT count(*) FROM effective_setpoints),
    'automation_rows', (SELECT count(*) FROM automation_state),
    'nullable_pid_rows', (SELECT count(*) FROM automation_state WHERE pid_output IS NULL),
    'winning_light_program', (
        SELECT name
        FROM light_programs
        WHERE location = 'Flower Room'
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
    )
) AS result;
