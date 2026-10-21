\set ON_ERROR_STOP on

-- Complete disposable source fixture for the grow data lifecycle proof.
--
-- Guard: the fixture only runs inside the guarded monitoring_test_ namespace
-- and rejects both production database names (cea_sensors and sj_cea,
-- including substrings).
--
-- The observation-stream DDL below is copied from the current repository
-- definitions (never from a live database) so the checked-in descriptors in
-- Infrastructure/grow-archive-service/grow_archive_service/source_schema.json
-- and this fixture describe the same schema:
--   * room/rack/device/sensor/sensor_registry/crop_batch  -> cea_schema.sql
--   * room_modes/flower_submodes                          -> app/migrations.py
--   * calendar_event                                      -> add_calendar_tables.sql
--   * measurement                                         -> cea_schema.sql
--   * automation_state/effective_setpoints/control_history/
--     pid_parameter_history/config_versions               -> alembic 001/002/005/006/007
--   * relay_observation                                   -> alembic 05_relay_observation
--   * monitoring_room_photoperiod/coverage                -> monitoring_read_models.sql
--   * mode_transition_history                             -> migrations/0012_mode_transition_history.sql
--   * calendar_mode_application                           -> add_calendar_tables.sql
--
-- light_state_history, pid_adjustments, setpoint_history and rollout_events
-- have no repository definition and are intentionally absent; the preflight
-- records them as absent and blocks if they ever appear without a descriptor.
--
-- Hypertable primary keys: repository baseline keys for automation_state and
-- control_history omit the partitioning event column; this fixture uses the
-- verified composite variants (time, id) and (id, time), respectively,
-- documented in source_schema.json.
-- Column order, types and nullability are unchanged; phase 0007 renames the
-- event-time indexes without changing their definitions.
--
-- Edge rows cover: float32 normal/subnormal/signed-zero/maximum magnitudes,
-- float64-edged JSONB numbers, legitimate NULLs, empty and NULL-bearing
-- arrays, duplicate equal rows, Unicode text, microsecond timestamps and
-- explicit identities.

-- Grow data lifecycle fixture guard. The lifecycle fixture is loaded by
-- with_monitoring_test_db; the guarded production names are rejected here too.
DO $guard$
BEGIN
    IF lower(current_database()) LIKE '%cea_sensors%'
       OR lower(current_database()) LIKE '%sj_cea%'
       OR current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION 'grow data lifecycle fixtures require the monitoring_test_ database namespace';
    END IF;
END
$guard$;

CREATE TABLE grow_data_lifecycle_fixture_metadata (
    fixture_name TEXT PRIMARY KEY,
    fixture_version INTEGER NOT NULL CHECK (fixture_version > 0)
);

INSERT INTO grow_data_lifecycle_fixture_metadata (fixture_name, fixture_version)
VALUES ('grow-data-lifecycle', 1);

-- =====================================================================
-- Metadata hierarchy (cea_schema.sql)
-- =====================================================================

CREATE TABLE room (
    room_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    target_vpd REAL,
    target_temp REAL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE rack (
    rack_id SERIAL PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES room(room_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(room_id, name)
);

CREATE TABLE device (
    device_id SERIAL PRIMARY KEY,
    rack_id INTEGER REFERENCES rack(rack_id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    ip_address TEXT,
    serial_number TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE sensor (
    sensor_id SERIAL PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    data_type TEXT NOT NULL,
    channel INTEGER CHECK (channel IS NULL OR (channel >= 0 AND channel <= 15)),
    calibration_offset REAL DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(device_id, name)
);

CREATE TABLE sensor_registry (
    registry_id BIGSERIAL PRIMARY KEY,
    bus TEXT NOT NULL CHECK (bus IN ('can', 'rs485')),
    hardware_address INTEGER NOT NULL CHECK (hardware_address > 0),
    device_id INTEGER REFERENCES device(device_id) ON DELETE SET NULL,
    display_name TEXT NOT NULL CHECK (length(btrim(display_name)) > 0),
    room_id INTEGER REFERENCES room(room_id) ON DELETE RESTRICT,
    rack_id INTEGER REFERENCES rack(rack_id) ON DELETE RESTRICT,
    location_in_room TEXT CHECK (location_in_room IN ('front', 'back', 'main')),
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT sensor_registry_bus_address_unique UNIQUE (bus, hardware_address),
    CONSTRAINT sensor_registry_device_unique UNIQUE (device_id),
    CONSTRAINT sensor_registry_legal_state CHECK (
        (room_id IS NULL AND rack_id IS NULL AND location_in_room IS NULL)
        OR (
            bus = 'can'
            AND room_id IS NOT NULL
            AND location_in_room IS NOT NULL
            AND rack_id IS NULL
        )
        OR (
            bus = 'rs485'
            AND rack_id IS NOT NULL
            AND room_id IS NULL
            AND location_in_room IS NULL
        )
    )
);

CREATE UNIQUE INDEX sensor_registry_can_position_unique
    ON sensor_registry (room_id, location_in_room)
WHERE bus = 'can'
  AND room_id IS NOT NULL
  AND location_in_room IS NOT NULL;

CREATE TABLE crop_batch (
    batch_id SERIAL PRIMARY KEY,
    crop_name TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    room_id INTEGER NOT NULL REFERENCES room(room_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =====================================================================
-- Room modes (app/migrations.py runtime schema)
-- =====================================================================

CREATE TABLE room_modes (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    photoperiod_hours INTEGER CHECK (photoperiod_hours >= 0 AND photoperiod_hours <= 24),
    is_constant BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE flower_submodes (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    week_start INTEGER CHECK (week_start >= 1),
    week_end INTEGER CHECK (week_end >= 1),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =====================================================================
-- Calendar (add_calendar_tables.sql)
-- =====================================================================

CREATE TABLE calendar_event (
    id SERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL DEFAULT 'main',
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    all_day BOOLEAN NOT NULL DEFAULT true,
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    crop_batch_id INTEGER REFERENCES crop_batch(batch_id) ON DELETE SET NULL,
    grow_plan_id UUID,
    recurrence_rule TEXT,
    recurrence_parent_id INTEGER REFERENCES calendar_event(id) ON DELETE SET NULL,
    ical_uid TEXT NOT NULL,
    external_provider TEXT,
    external_calendar_id TEXT,
    external_event_id TEXT,
    external_etag TEXT,
    last_synced_at TIMESTAMPTZ,
    sync_status TEXT NOT NULL DEFAULT 'synced',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT
);

CREATE UNIQUE INDEX idx_calendar_event_ical_uid ON calendar_event (ical_uid);
CREATE UNIQUE INDEX idx_calendar_event_external
    ON calendar_event (external_provider, external_calendar_id, external_event_id)
    WHERE external_event_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX idx_calendar_event_active
    ON calendar_event (location, start_date, end_date)
    WHERE deleted_at IS NULL;
CREATE INDEX idx_calendar_event_grow_plan
    ON calendar_event (grow_plan_id)
    WHERE deleted_at IS NULL AND grow_plan_id IS NOT NULL;


-- =====================================================================
-- Observation streams
-- =====================================================================

CREATE TABLE measurement (
    time TIMESTAMPTZ NOT NULL CHECK (time >= '2020-01-01'::timestamptz AND time <= NOW() + INTERVAL '1 day'),
    sensor_id INTEGER NOT NULL REFERENCES sensor(sensor_id) ON DELETE CASCADE,
    value REAL NOT NULL CHECK (value > -1e6 AND value < 1e6),
    status TEXT DEFAULT 'ok' CHECK (status IS NULL OR status IN ('ok', 'error', 'warning', 'calibrating')),
    PRIMARY KEY (time, sensor_id)
);

SELECT create_hypertable('measurement', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

CREATE INDEX idx_measurement_sensor_time ON measurement (sensor_id, time DESC);
CREATE INDEX idx_measurement_time ON measurement (time DESC);
CREATE INDEX idx_measurement_sensor_id ON measurement (sensor_id);


CREATE TABLE automation_state (
    id BIGSERIAL,
    "time" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    device_name TEXT NOT NULL,
    device_state INTEGER NOT NULL,
    device_mode TEXT NOT NULL,
    pid_output REAL,
    duty_cycle_percent REAL,
    active_rule_ids INTEGER[],
    active_schedule_ids INTEGER[],
    control_reason TEXT,
    schedule_ramp_up_duration INTEGER,
    schedule_ramp_down_duration INTEGER,
    schedule_photoperiod_hours REAL,
    pid_kp REAL,
    pid_ki REAL,
    pid_kd REAL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("time", id)
);

SELECT create_hypertable('automation_state', 'time', if_not_exists => TRUE);

CREATE INDEX idx_automation_state_location ON automation_state(location, cluster);
CREATE INDEX idx_automation_state_timestamp ON automation_state("time" DESC);
CREATE INDEX idx_automation_state_device ON automation_state(location, cluster, device_name);
-- Keep the production index name even though this post-rename fixture declares
-- the event-time column as "time".
DROP INDEX IF EXISTS automation_state_time_idx;
CREATE INDEX IF NOT EXISTS automation_state_timestamp_idx ON automation_state ("time" DESC);
CREATE INDEX IF NOT EXISTS monitoring_automation_state_id_idx ON automation_state (id)
    WITH (timescaledb.transaction_per_chunk);

CREATE TABLE effective_setpoints (
    "time" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    mode TEXT,
    device_name TEXT,
    effective_heating_setpoint REAL,
    effective_cooling_setpoint REAL,
    effective_humidity_setpoint REAL,
    effective_co2_setpoint REAL,
    effective_vpd_setpoint REAL,
    effective_light_intensity REAL,
    nominal_heating_setpoint REAL,
    nominal_cooling_setpoint REAL,
    nominal_humidity_setpoint REAL,
    nominal_co2_setpoint REAL,
    nominal_vpd_setpoint REAL,
    nominal_light_intensity REAL,
    ramp_progress_heating REAL,
    ramp_progress_cooling REAL,
    ramp_progress_humidity REAL,
    ramp_progress_co2 REAL,
    ramp_progress_vpd REAL,
    ramp_progress_light REAL,
    monitoring_ingest_id BIGINT,
    ingested_at TIMESTAMPTZ
);

CREATE SEQUENCE monitoring_effective_setpoints_ingest_id_seq AS BIGINT;
ALTER SEQUENCE monitoring_effective_setpoints_ingest_id_seq
    OWNED BY effective_setpoints.monitoring_ingest_id;
ALTER TABLE effective_setpoints
    ALTER COLUMN monitoring_ingest_id
        SET DEFAULT nextval('monitoring_effective_setpoints_ingest_id_seq'::regclass),
    ALTER COLUMN ingested_at SET DEFAULT now();

SELECT create_hypertable('effective_setpoints', 'time', if_not_exists => TRUE);

CREATE INDEX idx_effective_setpoints_location ON effective_setpoints(location, cluster);
CREATE INDEX idx_effective_setpoints_time ON effective_setpoints("time" DESC);
CREATE INDEX idx_effective_setpoints_device
    ON effective_setpoints(location, cluster, device_name, "time" DESC);
CREATE INDEX monitoring_effective_setpoints_ingest_id_idx
    ON effective_setpoints (monitoring_ingest_id)
    WHERE monitoring_ingest_id IS NOT NULL;
CREATE INDEX monitoring_effective_setpoints_ingested_at_idx
    ON effective_setpoints (ingested_at)
    WHERE ingested_at IS NOT NULL;

CREATE TABLE control_history (
    id BIGSERIAL,
    "time" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    device_name TEXT NOT NULL,
    channel INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15),
    old_state INTEGER CHECK (old_state IS NULL OR old_state IN (0, 1)),
    new_state INTEGER CHECK (new_state IS NULL OR new_state IN (0, 1)),
    mode TEXT CHECK (mode IS NULL OR mode IN ('manual', 'auto', 'scheduled')),
    reason TEXT,
    sensor_value REAL,
    setpoint REAL,
    load_percent REAL,
    manual_expires_at TIMESTAMPTZ,
    PRIMARY KEY (id, "time")
);

SELECT create_hypertable('control_history', 'time', if_not_exists => TRUE);

CREATE INDEX idx_control_history_location ON control_history(location, cluster);
CREATE INDEX idx_control_history_time ON control_history("time" DESC);
CREATE INDEX idx_control_history_manual_expires
    ON control_history(manual_expires_at)
    WHERE manual_expires_at IS NOT NULL AND mode = 'manual';

CREATE TABLE relay_observation (
    observation_id BIGINT GENERATED ALWAYS AS IDENTITY,
    "time" TIMESTAMPTZ NOT NULL,
    session_id UUID NOT NULL,
    channel SMALLINT NULL,
    observed_state BOOLEAN NULL,
    device_id INTEGER NULL,
    device_name TEXT NULL,
    device_type TEXT NULL,
    location TEXT NULL,
    cluster TEXT NULL,
    registry_version BIGINT NOT NULL,
    reason TEXT NOT NULL,
    CONSTRAINT pk_relay_observation
        PRIMARY KEY ("time", observation_id),
    CONSTRAINT ck_relay_observation_channel
        CHECK (channel IS NULL OR channel BETWEEN 0 AND 15),
    CONSTRAINT ck_relay_observation_reason
        CHECK (reason IN (
            'initial', 'state_changed', 'stale', 'recovered',
            'assignment_changed', 'recording_gap', 'heartbeat'
        )),
    CONSTRAINT ck_relay_observation_channel_reason
        CHECK ((channel IS NULL) = (reason = 'heartbeat')),
    CONSTRAINT ck_relay_observation_heartbeat_payload
        CHECK (
            reason <> 'heartbeat'
            OR (
                observed_state IS NULL
                AND device_id IS NULL
                AND device_name IS NULL
                AND device_type IS NULL
                AND location IS NULL
                AND cluster IS NULL
            )
        )
);

SELECT create_hypertable('relay_observation', 'time', if_not_exists => TRUE);

CREATE INDEX idx_relay_observation_location_cluster_device_time
    ON relay_observation (location, cluster, device_id, "time", observation_id);
CREATE INDEX idx_relay_observation_channel_time
    ON relay_observation (channel, "time", observation_id);

CREATE TABLE monitoring_room_photoperiod (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "time" TIMESTAMPTZ NOT NULL,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('SUN', 'MOON', 'UNKNOWN')),
    mode_id INTEGER,
    submode_id INTEGER,
    runtime_snapshot_version BIGINT NOT NULL CHECK (runtime_snapshot_version >= 0),
    source TEXT NOT NULL CHECK (btrim(source) <> '')
);
CREATE INDEX monitoring_room_photoperiod_room_time_idx
    ON monitoring_room_photoperiod (location, cluster, "time" DESC);
CREATE INDEX monitoring_room_photoperiod_snapshot_idx
    ON monitoring_room_photoperiod (runtime_snapshot_version);

CREATE TABLE monitoring_photoperiod_coverage (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "time" TIMESTAMPTZ NOT NULL,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('available', 'unavailable')),
    reason TEXT NOT NULL CHECK (reason IN (
        'initial', 'started', 'stopped', 'control_failure',
        'recording_gap', 'recovered', 'unclean_restart'
    )),
    runtime_snapshot_version BIGINT NOT NULL CHECK (runtime_snapshot_version >= 0),
    UNIQUE (location, cluster, "time", state)
);
CREATE INDEX monitoring_photoperiod_coverage_room_time_idx
    ON monitoring_photoperiod_coverage (location, cluster, "time" DESC, id DESC);

CREATE TABLE mode_transition_history (
    id SERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    old_mode_id INTEGER REFERENCES room_modes(id),
    old_submode_id INTEGER REFERENCES flower_submodes(id),
    new_mode_id INTEGER NOT NULL REFERENCES room_modes(id),
    new_submode_id INTEGER REFERENCES flower_submodes(id),
    triggered_by TEXT NOT NULL CHECK (triggered_by IN ('api', 'schedule', 'system')),
    "time" TIMESTAMPTZ DEFAULT NOW(),
    parameters_synced JSONB,
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_mode_transition_history_loc_clus_time
    ON mode_transition_history (location, cluster, "time" DESC);

CREATE TABLE pid_parameter_history (
    id BIGSERIAL PRIMARY KEY,
    "time" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    device_type TEXT NOT NULL,
    kp REAL NOT NULL,
    ki REAL NOT NULL,
    kd REAL NOT NULL,
    updated_by TEXT,
    source TEXT,
    location TEXT,
    cluster TEXT,
    binary_hysteresis REAL
);

CREATE INDEX idx_pid_parameter_history_device_type
    ON pid_parameter_history(device_type, "time" DESC);

CREATE TABLE config_versions (
    version_id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    author TEXT,
    comment TEXT,
    config_type TEXT NOT NULL,
    location TEXT,
    cluster TEXT,
    changes JSONB
);

CREATE INDEX idx_config_versions_timestamp ON config_versions(timestamp DESC);
CREATE INDEX idx_config_versions_type ON config_versions(config_type);

CREATE TABLE calendar_mode_application (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES calendar_event(id) ON DELETE CASCADE,
    applied_date DATE NOT NULL,
    mode_id INTEGER,
    submode_id INTEGER,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    triggered_by TEXT NOT NULL,
    UNIQUE (event_id, applied_date)
);

-- =====================================================================
-- Metadata rows
-- =====================================================================

-- Use the canonical Calendar prerequisites (idempotency, connection and
-- room profiles) rather than a reduced route fixture.
\ir ../../add_calendar_tables.sql
INSERT INTO room (room_id, name, target_vpd, target_temp)
VALUES
    (1, 'Flower Room', 1.20, 24.0),
    (2, 'Veg Room', 1.00, 23.0);
SELECT setval(pg_get_serial_sequence('room', 'room_id'), 2, TRUE);

INSERT INTO rack (rack_id, room_id, name)
VALUES
    (1, 1, 'Front Bed'),
    (2, 1, 'Back Bed'),
    (3, 2, 'Veg Bench');
SELECT setval(pg_get_serial_sequence('rack', 'rack_id'), 3, TRUE);

INSERT INTO device (device_id, rack_id, name, type)
VALUES
    (1, 2, 'Flower Back Node', 'sensor-node'),
    (2, 1, 'Flower Front Node', 'sensor-node'),
    (3, 3, 'Veg Main Node', 'sensor-node'),
    (4, NULL, 'Weather Station', 'weather-station');
SELECT setval(pg_get_serial_sequence('device', 'device_id'), 4, TRUE);

INSERT INTO sensor (sensor_id, device_id, name, unit, data_type, channel)
VALUES
    (1, 1, 'dry_bulb_b', '°C', 'temperature', 0),
    (2, 1, 'rh_b', '%', 'humidity', 1),
    (3, 1, 'vpd_b', 'kPa', 'vpd', 2),
    (4, 2, 'dry_bulb_f', '°C', 'temperature', 0),
    (5, 3, 'dry_bulb_v', '°C', 'temperature', 0),
    (6, 3, 'co2_v', 'ppm', 'co2', 1),
    (7, 4, 'outside_temp', '°C', 'temperature', NULL);
SELECT setval(pg_get_serial_sequence('sensor', 'sensor_id'), 7, TRUE);

INSERT INTO sensor_registry (
    registry_id, bus, hardware_address, device_id, display_name, room_id, rack_id, location_in_room
)
VALUES
    (1, 'can', 1, 1, 'Flower Back Node', 1, NULL, 'back'),
    (2, 'can', 2, 2, 'Flower Front Node', 1, NULL, 'front'),
    (3, 'can', 3, 3, 'Veg Main Node', 2, NULL, 'main'),
    (4, 'rs485', 231, NULL, 'Back Bed probe', NULL, 2, NULL),
    (5, 'can', 42, NULL, 'Node 42', NULL, NULL, NULL);
SELECT setval(pg_get_serial_sequence('sensor_registry', 'registry_id'), 5, TRUE);

INSERT INTO crop_batch (batch_id, crop_name, start_date, end_date, room_id)
VALUES
    (1, 'Flower Grow 2026-A', '2026-01-05', NULL, 1),
    (2, 'Veg Batch 2026-A', '2026-01-05', NULL, 2);
SELECT setval(pg_get_serial_sequence('crop_batch', 'batch_id'), 2, TRUE);

INSERT INTO room_modes (id, name, description, photoperiod_hours, is_constant)
VALUES
    (1, 'veg', 'Vegetative growth - 18/6 photoperiod', 18, FALSE),
    (2, 'flower', 'Flowering - 12/12 photoperiod', 12, FALSE),
    (3, 'drying', 'Drying - 24h constant conditions', 0, TRUE),
    (4, 'sleep', 'Sleep mode - minimal energy', 0, TRUE);
SELECT setval(pg_get_serial_sequence('room_modes', 'id'), 4, TRUE);

INSERT INTO flower_submodes (id, name, description, week_start, week_end)
VALUES
    (1, 'stretch', 'Stretch phase - weeks 1-3', 1, 3),
    (2, 'bulk', 'Bulk phase - weeks 4-6', 4, 6),
    (3, 'ripen', 'Ripen phase - weeks 7-9', 7, 9);
SELECT setval(pg_get_serial_sequence('flower_submodes', 'id'), 3, TRUE);

INSERT INTO calendar_event (
    id, location, cluster, event_type, title, start_date, end_date, all_day,
    notes, metadata, crop_batch_id, grow_plan_id, ical_uid, sync_status, created_by
)
VALUES
    (1, 'Flower Room', 'main', 'flower', 'Flower Grow 2026-A', '2026-02-01', '2026-04-01', TRUE,
     NULL, '{}', 1, '11111111-1111-4111-8111-111111111111',
     'sjcea-grow-2026-a@sjcea', 'synced', 'fixture');
SELECT setval(pg_get_serial_sequence('calendar_event', 'id'), 1, TRUE);

-- =====================================================================
-- Edge rows
-- =====================================================================

INSERT INTO measurement (time, sensor_id, value, status)
VALUES
    ('2026-01-05 00:00:00.123456+00', 1, 23.456789::real, 'ok'),
    ('2026-01-05 00:01:00.000001+00', 1, '-0'::real, 'ok'),
    ('2026-01-05 00:02:00+00', 1, 0::real, 'ok'),
    ('2026-01-05 00:03:00+00', 1, 999999.94::real, 'warning'),
    ('2026-01-05 00:04:00+00', 1, -999999.94::real, 'error'),
    ('2026-01-05 00:05:00+00', 1, 1.1754943508222875e-38::real, NULL),
    ('2026-01-05 00:06:00+00', 1, (1.1754943508222875e-38::real / 2::real), 'calibrating'),
    ('2026-01-05 00:07:00+00', 1, 23.456789::real, 'ok'),
    ('2026-01-05 00:08:00+00', 2, 55.5::real, 'ok'),
    ('2026-01-05 00:09:00+00', 5, 24.5::real, 'ok'),
    ('2026-01-05 00:10:00+00', 7, 12.25::real, 'ok');


INSERT INTO automation_state (
    id, "time", location, cluster, device_name, device_state, device_mode,
    pid_output, duty_cycle_percent, active_rule_ids, active_schedule_ids, control_reason,
    schedule_ramp_up_duration, schedule_ramp_down_duration, schedule_photoperiod_hours,
    pid_kp, pid_ki, pid_kd, updated_at
)
VALUES
    (1, '2026-01-05 00:00:00.123456+00', 'Flower Room', 'main', 'heater_f_1', 1, 'auto',
     NULL, 100.0::real, NULL, '{}', 'Ünïcode Δοκιμή 温度 heater engaged',
     NULL, NULL, NULL, NULL, NULL, NULL, '2026-01-05 00:00:01+00'),
    (2, '2026-01-05 00:01:00+00', 'Flower Room', 'main', 'heater_f_1', 1, 'auto',
     '-0'::real, 0::real, '{1,NULL,3}', '{}', 'PID demand',
     NULL, NULL, NULL, 0.5::real, 0.1::real, 0.05::real, '2026-01-05 00:01:01+00'),
    (3, '2026-01-05 00:02:00+00', 'Veg Room', 'main', 'fan_v_1', 0, 'scheduled',
     NULL, NULL, NULL, '{2,4}', 'photoperiod off',
     NULL, NULL, 18.0::real, NULL, NULL, NULL, '2026-01-05 00:02:01+00'),
    (4, '2026-01-05 00:01:00+00', 'Flower Room', 'main', 'heater_f_1', 1, 'auto',
     '-0'::real, 0::real, '{1,NULL,3}', '{}', 'PID demand',
     NULL, NULL, NULL, 0.5::real, 0.1::real, 0.05::real, '2026-01-05 00:01:01+00');
SELECT setval(pg_get_serial_sequence('automation_state', 'id'), 4, TRUE);

INSERT INTO effective_setpoints (
    "time", location, cluster, mode, device_name,
    effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint,
    effective_co2_setpoint, effective_vpd_setpoint, effective_light_intensity,
    nominal_heating_setpoint, nominal_cooling_setpoint, nominal_humidity_setpoint,
    nominal_co2_setpoint, nominal_vpd_setpoint, nominal_light_intensity,
    ramp_progress_heating, ramp_progress_cooling, ramp_progress_humidity,
    ramp_progress_co2, ramp_progress_vpd, ramp_progress_light,
    monitoring_ingest_id, ingested_at
)
VALUES
    ('2026-01-05 00:00:00.123456+00', 'Flower Room', 'main', 'DAY', 'light_f_1',
     23.0::real, 27.0::real, 58.0::real, 1000.0::real, 1.2::real, 80.0::real,
     22.5::real, 26.5::real, 57.0::real, 950.0::real, 1.15::real, 75.0::real,
     0.0::real, 1.0::real, 0.0::real, 1.0::real, 0.0::real, 1.0::real,
     NULL, NULL),
    ('2026-01-05 00:10:00+00', 'Veg Room', 'main', 'DAY', 'light_v_1',
     22.0::real, 26.0::real, 62.0::real, 900.0::real, 1.0::real, 65.0::real,
     21.5::real, 25.5::real, 61.0::real, 850.0::real, 0.95::real, 60.0::real,
     0.5::real, 0.5::real, 0.5::real, 0.5::real, 0.5::real, 0.5::real,
     NULL, NULL),
    ('2026-01-05 00:10:00+00', 'Veg Room', 'main', 'DAY', 'light_v_1',
     22.0::real, 26.0::real, 62.0::real, 900.0::real, 1.0::real, 65.0::real,
     21.5::real, 25.5::real, 61.0::real, 850.0::real, 0.95::real, 60.0::real,
     0.5::real, 0.5::real, 0.5::real, 0.5::real, 0.5::real, 0.5::real,
     NULL, NULL),
    ('2026-01-05 00:20:00+00', 'Veg Room', 'main', NULL, NULL,
     NULL, NULL, NULL, NULL, NULL, NULL,
     NULL, NULL, NULL, NULL, NULL, NULL,
     NULL, NULL, NULL, NULL, NULL, NULL,
     NULL, NULL);

INSERT INTO control_history (
    id, "time", location, cluster, device_name, channel,
    old_state, new_state, mode, reason, sensor_value, setpoint, load_percent, manual_expires_at
)
VALUES
    (1, '2026-01-05 00:00:00.000001+00', 'Flower Room', 'main', 'heater_f_1', 0,
     0, 1, 'auto', 'PID demand', 21.5::real, 23.0::real, 0.0::real, NULL),
    (2, '2026-01-05 00:01:00+00', 'Flower Room', 'main', 'heater_f_1', 0,
     1, 0, 'manual', 'owner override Δ 温度', 22.0::real, 23.0::real, 100.0::real,
     '2026-01-06 00:00:00+00'),
    (3, '2026-01-05 00:02:00+00', 'Veg Room', 'main', 'fan_v_1', 5,
     NULL, NULL, NULL, 'photoperiod off', NULL, NULL, '-0'::real, NULL),
    (4, '2026-01-05 00:01:00+00', 'Flower Room', 'main', 'heater_f_1', 0,
     1, 0, 'manual', 'owner override Δ 温度', 22.0::real, 23.0::real, 100.0::real,
     '2026-01-06 00:00:00+00');
SELECT setval(pg_get_serial_sequence('control_history', 'id'), 4, TRUE);

INSERT INTO relay_observation (
    observation_id, "time", session_id, channel, observed_state,
    device_id, device_name, device_type, location, cluster, registry_version, reason
)
OVERRIDING SYSTEM VALUE
VALUES
    (1, '2026-01-05 00:00:00.000001+00', '22222222-2222-4222-8222-222222222222',
     0, FALSE, 1, 'Flower Back Node', 'sensor-node', 'Flower Room', 'main', 0, 'initial'),
    (2, '2026-01-05 00:01:00+00', '22222222-2222-4222-8222-222222222222',
     0, TRUE, 1, 'Flower Back Node', 'sensor-node', 'Flower Room', 'main', 0, 'state_changed'),
    (3, '2026-01-05 00:02:00+00', '33333333-3333-4333-8333-333333333333',
     NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, 'heartbeat'),
    (4, '2026-01-05 00:03:00+00', '33333333-3333-4333-8333-333333333333',
     15, FALSE, 4, 'Weather Relay', 'relay', 'Veg Room', 'main', 1, 'recovered');
SELECT setval(pg_get_serial_sequence('relay_observation', 'observation_id'), 4, TRUE);

INSERT INTO monitoring_room_photoperiod (
    id, "time", location, cluster, phase, mode_id, submode_id,
    runtime_snapshot_version, source
)
OVERRIDING SYSTEM VALUE
VALUES
    (1, '2026-01-05 00:00:00.000001+00', 'Flower Room', 'main', 'SUN', 2, 1, 0, 'Δ photoperiod 温度'),
    (2, '2026-01-05 12:00:00+00', 'Flower Room', 'main', 'MOON', 2, 1, 3, 'photoperiod'),
    (3, '2026-01-05 13:00:00+00', 'Veg Room', 'main', 'UNKNOWN', NULL, NULL, 0, 'unclean_restart');
SELECT setval(pg_get_serial_sequence('monitoring_room_photoperiod', 'id'), 3, TRUE);

INSERT INTO monitoring_photoperiod_coverage (
    id, "time", location, cluster, state, reason, runtime_snapshot_version
)
OVERRIDING SYSTEM VALUE
VALUES
    (1, '2026-01-05 00:00:00.000001+00', 'Flower Room', 'main', 'available', 'initial', 0),
    (2, '2026-01-05 01:00:00+00', 'Flower Room', 'main', 'unavailable', 'recording_gap', 1),
    (3, '2026-01-05 02:00:00+00', 'Veg Room', 'main', 'available', 'recovered', 2);
SELECT setval(pg_get_serial_sequence('monitoring_photoperiod_coverage', 'id'), 3, TRUE);

INSERT INTO mode_transition_history (
    id, location, cluster, old_mode_id, old_submode_id, new_mode_id, new_submode_id,
    triggered_by, "time", parameters_synced, success, error_message, created_at
)
VALUES
    (1, 'Flower Room', 'main', NULL, NULL, 2, 1, 'api',
     '2026-01-05 00:00:00.000001+00',
     '{"old_mode_name": "veg", "new_mode_name": "flower", "note": "Ünïcode 温度",
       "thresholds": [1e308, 5e-324, -0.0, null, true]}', TRUE, NULL,
     '2026-01-05 00:00:01+00'),
    (2, 'Veg Room', 'main', 2, 3, 1, NULL, 'schedule', NULL,
     NULL, NULL, 'error Δ 温度', '2026-01-05 00:01:01+00'),
    (3, 'Flower Room', 'main', 2, 1, 3, NULL, 'system',
     '2026-01-05 00:02:00+00', '{}', TRUE, NULL, '2026-01-05 00:02:01+00');
SELECT setval(pg_get_serial_sequence('mode_transition_history', 'id'), 3, TRUE);

INSERT INTO pid_parameter_history (
    id, "time", device_type, kp, ki, kd, updated_by, source,
    location, cluster, binary_hysteresis
)
VALUES
    (1, '2026-01-05 00:00:00.000001+00', 'heater',
     '-0'::real, 1.1754943508222875e-38::real, 3.4028235e38::real,
     'operator Δ', 'cli', NULL, NULL, NULL),
    (2, '2026-01-05 00:01:00+00', 'light',
     0.0::real, 0.0::real, 0.0::real, 'Ünïcode 温度', 'api',
     'Flower Room', 'main', 0.1::real),
    (3, '2026-01-05 00:02:00+00', 'heater',
     1.5::real, 0.25::real, 0.05::real, NULL, NULL,
     'Veg Room', 'main', NULL);
SELECT setval(pg_get_serial_sequence('pid_parameter_history', 'id'), 3, TRUE);

INSERT INTO config_versions (version_id, timestamp, author, comment, config_type, location, cluster, changes)
VALUES
    (1, '2026-01-05 00:00:00.000001+00', 'Äuthor ünïcode', 'comment 温度', 'climate',
     'Flower Room', 'main',
     '{"nested": {"values": [1e308, 5e-324, -0.0], "unicode": "温度 Δοκιμή", "flag": true},
       "null_value": null, "empty_object": {}, "empty_array": []}'),
    (2, '2026-01-05 00:01:00+00', NULL, NULL, 'pid', NULL, NULL, NULL);
SELECT setval(pg_get_serial_sequence('config_versions', 'version_id'), 2, TRUE);

INSERT INTO calendar_mode_application (
    id, event_id, applied_date, mode_id, submode_id, applied_at, triggered_by
)
VALUES
    (1, 1, '2026-02-01', 2, 1, '2026-02-01 00:00:00.000001+00', 'Ünïcode scheduler'),
    (2, 1, '2026-02-02', 2, NULL, '2026-02-02 00:00:00+00', 'system');
SELECT setval(pg_get_serial_sequence('calendar_mode_application', 'id'), 2, TRUE);
