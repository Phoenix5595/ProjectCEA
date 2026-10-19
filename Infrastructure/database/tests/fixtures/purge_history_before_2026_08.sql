\set ON_ERROR_STOP on

DO $disposable_guard$
BEGIN
    IF current_database() !~ '^monitoring_test_[a-z0-9_]+$'
       OR lower(current_database()) LIKE '%cea_sensors%'
       OR lower(current_database()) LIKE '%sj_cea%' THEN
        RAISE EXCEPTION 'purge proof seed requires a guarded monitoring_test_ database';
    END IF;
END
$disposable_guard$;

-- These four optional streams have unknown production semantics and are not
-- purge targets. Seed their pre-cutoff rows to prove they remain byte-identical.
CREATE TABLE light_state_history (
    id BIGINT PRIMARY KEY,
    location TEXT,
    triggered_at TIMESTAMPTZ,
    payload TEXT NOT NULL
);
CREATE TABLE pid_adjustments (
    id BIGINT PRIMARY KEY,
    location TEXT,
    created_at TIMESTAMPTZ,
    payload TEXT NOT NULL
);
CREATE TABLE setpoint_history (
    id BIGINT PRIMARY KEY,
    location TEXT,
    timestamp TIMESTAMPTZ,
    payload TEXT NOT NULL
);
CREATE TABLE rollout_events (
    id BIGINT PRIMARY KEY,
    timestamp TIMESTAMPTZ,
    payload TEXT NOT NULL
);

-- Guard probes ensure registry/current-state and archive-prefixed objects are
-- outside the purge even when the grow fixture does not define those schemas.
CREATE TABLE device_registry (device_id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
INSERT INTO device_registry VALUES (7001, 'protected registry row');
CREATE TABLE device_states (device_id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
INSERT INTO device_states VALUES (7001, 'protected current state');
CREATE TABLE users (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
INSERT INTO users VALUES (7001, 'protected user row');
CREATE TABLE archive_purge_protected_probe (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
INSERT INTO archive_purge_protected_probe VALUES (7001, 'protected archive-prefixed row');
-- Static setpoint/period/schedule identities absent from the grow fixture are
-- represented by disposable name probes so accidental broad deletes fail.
CREATE TABLE device_mappings (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
CREATE TABLE setpoints (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
CREATE TABLE mode_parameters (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
CREATE TABLE room_active_mode (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
CREATE TABLE active_room_modes (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
CREATE TABLE pid_parameters (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
CREATE TABLE climate_periods (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
CREATE TABLE schedules (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
CREATE TABLE rules (id INTEGER PRIMARY KEY, marker TEXT NOT NULL);
INSERT INTO device_mappings VALUES (7001, 'protected mapping row');
INSERT INTO setpoints VALUES (7001, 'protected static setpoint');
INSERT INTO mode_parameters VALUES (7001, 'protected mode parameter');
INSERT INTO room_active_mode VALUES (7001, 'protected room mode');
INSERT INTO active_room_modes VALUES (7001, 'protected active room mode');
INSERT INTO pid_parameters VALUES (7001, 'protected PID parameter');
INSERT INTO climate_periods VALUES (7001, 'protected climate period');
INSERT INTO schedules VALUES (7001, 'protected schedule');
INSERT INTO rules VALUES (7001, 'protected rule');
INSERT INTO grow_plan_idempotency(idempotency_key, grow_plan_id, expires_at)
VALUES (
    '66666666-6666-4666-8666-666666666666',
    '77777777-7777-4777-8777-777777777777',
    '2027-01-01 00:00:00+00'
);
INSERT INTO calendar_sync_connection(
    provider, display_name, account_email, caldav_base_url,
    credentials_encrypted, target_calendar_url
)
VALUES (
    'nextcloud', 'protected sync connection', 'protected@example.invalid',
    'https://example.invalid/caldav/', decode('01', 'hex'),
    'https://example.invalid/calendar/protected/'
);

-- Old, anchor, near-cutoff and post-cutoff rows. The cutoff is 2026-08-01
-- 00:00 Toronto (04:00 UTC). The older rows share the relevant anchor keys;
-- timestamp ties exercise the conservative keep-all-ties rule.
-- Mirror production intervals: automation_state is one day; measurement is
-- one day in the base schema; effective_setpoints and relay_observation keep
-- the seven-day default. Measurement and effective_setpoints use compression.
SELECT public.set_chunk_time_interval('public.automation_state'::REGCLASS, INTERVAL '1 day');
INSERT INTO measurement(time, sensor_id, value, status) VALUES
    ('2026-07-30 12:33:00+00', 7, 70.0, 'ok'),
    ('2026-07-31 12:33:00+00', 7, 71.0, 'ok'),
    ('2026-08-02 12:33:00+00', 7, 82.0, 'ok');

INSERT INTO actuator_events(device_id, action, value, time) VALUES
    (1, 'purge-old', 70.0, '2026-07-30 12:30:00+00'),
    (1, 'purge-anchor', 71.0, '2026-07-31 12:30:00+00'),
    (1, 'purge-post', 82.0, '2026-08-02 12:30:00+00');

INSERT INTO automation_state(
    timestamp, location, cluster, device_name, device_state, device_mode,
    pid_output, duty_cycle_percent, control_reason
) VALUES
    ('2026-07-30 12:30:00+00', 'Purge Room', 'main', 'purge-heater', 1, 'auto', 0.7, 70.0, 'old'),
    ('2026-07-31 12:30:00+00', 'Purge Room', 'main', 'purge-heater', 0, 'auto', 0.0, 0.0, 'anchor one'),
    ('2026-07-31 12:30:00+00', 'Purge Room', 'main', 'purge-heater', 1, 'auto', 0.5, 50.0, 'anchor tie'),
    ('2026-08-02 12:30:00+00', 'Purge Room', 'main', 'purge-heater', 1, 'auto', 0.8, 80.0, 'post');

INSERT INTO effective_setpoints(timestamp, location, cluster, mode, device_name, effective_light_intensity) VALUES
    ('2026-07-30 12:30:00+00', 'Purge Room', 'main', 'DAY', 'purge-light', 70.0),
    ('2026-07-31 12:30:00+00', 'Purge Room', 'main', 'DAY', 'purge-light', 71.0),
    ('2026-08-01 03:58:59+00', 'Purge Room', 'main', 'DAY', 'purge-light', 79.0),
    ('2026-08-01 03:59:00+00', 'Purge Room', 'main', 'DAY', 'purge-light', 79.5),
    ('2026-08-02 12:30:00+00', 'Purge Room', 'main', 'DAY', 'purge-light', 82.0);

INSERT INTO control_history(
    timestamp, location, cluster, device_name, channel, old_state, new_state,
    mode, reason, manual_expires_at
) VALUES
    ('2026-07-30 12:30:00+00', 'Purge Room', 'main', 'purge-relay', 9, 0, 1, 'auto', 'old', NULL),
    ('2026-07-31 12:30:00+00', 'Purge Room', 'main', 'purge-relay', 9, 0, 1, 'auto', 'anchor one', NULL),
    ('2026-07-31 12:30:00+00', 'Purge Room', 'main', 'purge-relay', 9, 1, 0, 'auto', 'anchor tie', NULL),
    ('2026-07-29 12:30:00+00', 'Purge Room', 'main', 'purge-relay', 9, 0, 1, 'manual', 'manual still owned', '2026-07-30 12:30:00+00'),
    ('2026-08-02 12:30:00+00', 'Purge Room', 'main', 'purge-relay', 9, 0, 1, 'auto', 'post', NULL);

INSERT INTO relay_observation(
    observed_at, session_id, channel, observed_state, device_id, device_name,
    device_type, location, cluster, registry_version, reason
) VALUES
    ('2026-07-30 12:00:00+00', '44444444-4444-4444-8444-444444444444', 0, FALSE, 1, 'purge-relay', 'relay', 'Purge Room', 'main', 1, 'initial'),
    ('2026-07-31 12:00:00+00', '44444444-4444-4444-8444-444444444444', 0, TRUE, 1, 'purge-relay', 'relay', 'Purge Room', 'main', 1, 'state_changed'),
    ('2026-08-02 12:00:00+00', '44444444-4444-4444-8444-444444444444', 0, FALSE, 1, 'purge-relay', 'relay', 'Purge Room', 'main', 1, 'state_changed'),
    ('2026-07-30 13:00:00+00', '55555555-5555-4555-8555-555555555555', 14, FALSE, NULL, 'orphan', 'relay', NULL, 'main', 1, 'initial'),
    ('2026-07-31 13:00:00+00', '55555555-5555-4555-8555-555555555555', 14, TRUE, NULL, 'orphan', 'relay', NULL, 'main', 1, 'state_changed');

INSERT INTO monitoring_room_photoperiod(
    observed_at, location, cluster, phase, runtime_snapshot_version, source
) VALUES
    ('2026-07-30 12:30:00+00', 'Purge Room', 'main', 'SUN', 1, 'purge-proof'),
    ('2026-07-31 12:30:00+00', 'Purge Room', 'main', 'MOON', 2, 'purge-proof'),
    ('2026-08-01 03:58:59+00', 'Purge Room', 'main', 'SUN', 3, 'purge-proof'),
    ('2026-08-01 03:59:00+00', 'Purge Room', 'main', 'MOON', 4, 'purge-proof'),
    ('2026-08-02 12:30:00+00', 'Purge Room', 'main', 'SUN', 5, 'purge-proof');

INSERT INTO monitoring_photoperiod_coverage(
    observed_at, location, cluster, state, reason, runtime_snapshot_version
) VALUES
    ('2026-07-30 12:30:00+00', 'Purge Room', 'main', 'available', 'initial', 1),
    ('2026-07-31 12:30:00+00', 'Purge Room', 'main', 'unavailable', 'recording_gap', 2),
    ('2026-08-02 12:30:00+00', 'Purge Room', 'main', 'available', 'recovered', 3);

INSERT INTO mode_transition_history(
    location, cluster, old_mode_id, new_mode_id, triggered_by, triggered_at
) VALUES
    ('Purge Room', 'main', 1, 2, 'system', '2026-07-30 12:30:00+00'),
    ('Purge Room', 'main', 2, 3, 'api', '2026-07-31 12:30:00+00'),
    ('Purge Room', 'main', 3, 2, 'schedule', '2026-08-02 12:30:00+00');

INSERT INTO pid_parameter_history(timestamp, device_type, kp, ki, kd, location, cluster) VALUES
    ('2026-07-30 12:30:00+00', 'purge-proof', 1.0, 0.1, 0.01, 'Purge Room', 'main'),
    ('2026-07-31 12:30:00+00', 'purge-proof', 2.0, 0.2, 0.02, 'Purge Room', 'main'),
    ('2026-08-02 12:30:00+00', 'purge-proof', 3.0, 0.3, 0.03, 'Purge Room', 'main');

INSERT INTO light_state_history VALUES
    (1, 'fallback-post', '2026-07-29 12:00:00+00', 'old one'),
    (2, 'fallback-post', '2026-07-30 12:00:00+00', 'old two'),
    (3, 'fallback-post', '2026-08-02 12:00:00+00', 'post'),
    (4, 'fallback-old', '2026-07-29 12:00:00+00', 'old one'),
    (5, 'fallback-old', '2026-07-30 12:00:00+00', 'latest old');
INSERT INTO pid_adjustments VALUES
    (1, 'fallback-post', '2026-07-29 12:00:00+00', 'old one'),
    (2, 'fallback-post', '2026-07-30 12:00:00+00', 'old two'),
    (3, 'fallback-post', '2026-08-02 12:00:00+00', 'post'),
    (4, 'fallback-old', '2026-07-29 12:00:00+00', 'old one'),
    (5, 'fallback-old', '2026-07-30 12:00:00+00', 'latest old');
INSERT INTO setpoint_history VALUES
    (1, 'fallback-post', '2026-07-29 12:00:00+00', 'old one'),
    (2, 'fallback-post', '2026-07-30 12:00:00+00', 'old two'),
    (3, 'fallback-post', '2026-08-02 12:00:00+00', 'post'),
    (4, 'fallback-old', '2026-07-29 12:00:00+00', 'old one'),
    (5, 'fallback-old', '2026-07-30 12:00:00+00', 'latest old');
INSERT INTO rollout_events VALUES
    (1, '2026-07-29 12:00:00+00', 'old one'),
    (2, '2026-07-30 12:00:00+00', 'old two'),
    (3, '2026-08-02 12:00:00+00', 'post');


ALTER TABLE effective_setpoints SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'location,cluster,device_name,mode',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SELECT compress_chunk(chunk)
  FROM show_chunks('effective_setpoints') AS chunks(chunk);
DO $effective_compressed_guard$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.chunks
         WHERE hypertable_schema = 'public'
           AND hypertable_name = 'effective_setpoints'
           AND is_compressed
    ) THEN
        RAISE EXCEPTION 'purge proof did not create a compressed effective_setpoints chunk';
    END IF;
END
$effective_compressed_guard$;
CALL public.refresh_continuous_aggregate(
    'public.monitoring_effective_setpoints_1min'::REGCLASS,
    TIMESTAMPTZ '2026-01-05 00:00:00+00',
    TIMESTAMPTZ '2026-01-06 00:00:00+00',
    TRUE,
    '{"buckets_per_batch":0}'::JSONB
);
SELECT 'effective_setpoints January CAGG refresh passed after compression';
CALL public.refresh_continuous_aggregate(
    'public.monitoring_effective_setpoints_1min'::REGCLASS,
    TIMESTAMPTZ '2026-07-29 00:00:00+00',
    TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto',
    TRUE,
    '{"buckets_per_batch":0}'::JSONB
);
SELECT 'effective_setpoints CAGG refresh passed after compression';

-- Prove the local TimescaleDB compressed-chunk DELETE path: configure and
-- compress every measurement chunk before the full-row snapshots.
ALTER TABLE measurement SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sensor_id',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT compress_chunk(chunk)
  FROM show_chunks('measurement') AS chunks(chunk);
DO $compressed_chunk_guard$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.chunks
         WHERE hypertable_schema = 'public'
           AND hypertable_name = 'measurement'
           AND is_compressed
    ) THEN
        RAISE EXCEPTION 'purge proof did not create a compressed measurement chunk';
    END IF;
    RAISE NOTICE 'TimescaleDB %: compressed measurement chunks are present',
        (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb');
END
$compressed_chunk_guard$;


-- Materialize pre-purge rows so the proof detects stale aggregate buckets
-- being recomputed/removed after source deletion.
CALL public.refresh_continuous_aggregate('public.monitoring_measurement_1min'::REGCLASS, TIMESTAMPTZ '2026-07-29 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto', TRUE, '{"buckets_per_batch":0}'::JSONB);
CALL public.refresh_continuous_aggregate('public.monitoring_measurement_5min'::REGCLASS, TIMESTAMPTZ '2026-07-29 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto', TRUE, '{"buckets_per_batch":0}'::JSONB);
CALL public.refresh_continuous_aggregate('public.monitoring_measurement_1hour'::REGCLASS, TIMESTAMPTZ '2026-07-29 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto', TRUE, '{"buckets_per_batch":0}'::JSONB);
CALL public.refresh_continuous_aggregate('public.monitoring_measurement_1day'::REGCLASS, TIMESTAMPTZ '2026-07-29 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto', TRUE, '{"buckets_per_batch":0}'::JSONB);
CALL public.refresh_continuous_aggregate('public.monitoring_effective_setpoints_1min'::REGCLASS, TIMESTAMPTZ '2026-07-29 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto', TRUE, '{"buckets_per_batch":0}'::JSONB);
CALL public.refresh_continuous_aggregate('public.monitoring_effective_setpoints_5min'::REGCLASS, TIMESTAMPTZ '2026-07-29 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto', TRUE, '{"buckets_per_batch":0}'::JSONB);
CALL public.refresh_continuous_aggregate('public.monitoring_light_intensity_1hour'::REGCLASS, TIMESTAMPTZ '2026-07-29 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto', TRUE, '{"buckets_per_batch":0}'::JSONB);
CALL public.refresh_continuous_aggregate('public.monitoring_light_intensity_1day'::REGCLASS, TIMESTAMPTZ '2026-07-29 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto', TRUE, '{"buckets_per_batch":0}'::JSONB);
-- Automation-state aggregates use ROW(timestamp,id) partial states. Materialize
-- them one source chunk/day at a time, as the operator planner does.
SELECT format(
           'CALL public.refresh_continuous_aggregate(%L::regclass, %L::timestamptz, %L::timestamptz, TRUE, %L::jsonb);',
           'public.' || cagg.view_name, time_window.start_at, time_window.end_at,
           '{"buckets_per_batch":0}'
       )
  FROM (VALUES
      ('monitoring_automation_state_1min', 10),
      ('monitoring_automation_state_5min', 11),
      ('monitoring_requested_pid_1hour', 20),
      ('monitoring_requested_pid_1day', 30)
  ) AS cagg(view_name, refresh_order)
 CROSS JOIN (VALUES
      (1, TIMESTAMPTZ '2026-07-30 00:00:00+00', TIMESTAMPTZ '2026-07-31 00:00:00+00'),
      (2, TIMESTAMPTZ '2026-07-31 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00+00'),
      (3, TIMESTAMPTZ '2026-08-01 00:00:00+00', TIMESTAMPTZ '2026-08-02 00:00:00+00')
  ) AS time_window(window_order, start_at, end_at)
 ORDER BY cagg.refresh_order, time_window.window_order
\gexec

-- Drain the real procedure's one-UTC-day relay claims. Every generated CALL
-- is a separate top-level command, matching smoke_derived_refresh.py.
SELECT 'CALL public.monitoring_rebuild_relay_overview(NULL, '
       '''{"enabled":true,"acknowledged_failures":{}}''::jsonb);'
FROM generate_series(
    1,
    (
        SELECT GREATEST(
            1,
            CEIL(EXTRACT(EPOCH FROM (
                GREATEST(dirty_through, COALESCE(covered_through, dirty_through))
                - LEAST(dirty_from, COALESCE(covered_through, dirty_from))
            )) / 86400)::INTEGER + 2
        )
        FROM public.monitoring_relay_overview_progress
        WHERE dirty_from IS NOT NULL AND dirty_through IS NOT NULL
    )
)
WHERE EXISTS (SELECT 1 FROM public.monitoring_relay_overview_progress
              WHERE dirty_from IS NOT NULL AND dirty_through IS NOT NULL)
\gexec

CALL public.refresh_continuous_aggregate('public.monitoring_relay_duration_1day'::REGCLASS, TIMESTAMPTZ '2026-07-29 00:00:00+00', TIMESTAMPTZ '2026-08-01 00:00:00 America/Toronto', TRUE, '{"buckets_per_batch":0}'::JSONB);
CALL public.monitoring_rebuild_relay_overview(NULL, '{"enabled":true,"acknowledged_failures":{}}'::JSONB);

DO $seed_assertions$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.monitoring_measurement_1min
         WHERE bucket = TIMESTAMPTZ '2026-07-30 12:33:00+00' AND sensor_id = 7 AND sample_count = 1
    ) THEN
        RAISE EXCEPTION 'purge proof failed to materialize its pre-purge measurement bucket';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.monitoring_relay_duration_1hour
         WHERE bucket = TIMESTAMPTZ '2026-07-30 12:00:00+00'
           AND location = 'Purge Room' AND channel = 0 AND active
    ) THEN
        RAISE EXCEPTION 'purge proof failed to build its pre-purge relay overview';
    END IF;
END
$seed_assertions$;
