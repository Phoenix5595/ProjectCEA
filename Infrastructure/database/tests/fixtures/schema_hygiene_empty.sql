\set ON_ERROR_STOP on

DO $guard$
BEGIN
    IF current_database() !~ '^monitoring_test_[a-z0-9_]+$'
       OR lower(current_database()) LIKE '%cea_sensors%'
       OR lower(current_database()) LIKE '%sj_cea%' THEN
        RAISE EXCEPTION 'schema hygiene fixture requires a guarded monitoring_test_ database';
    END IF;
END
$guard$;

-- The core schema references these runtime tables; the lifecycle fixture
-- supplies the same app/migrations.py baseline before loading cea_schema.sql.
CREATE TABLE public.room_modes (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    photoperiod_hours INTEGER CHECK (photoperiod_hours >= 0 AND photoperiod_hours <= 24),
    is_constant BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE public.flower_submodes (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    week_start INTEGER CHECK (week_start >= 1),
    week_end INTEGER CHECK (week_end >= 1),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE public.mode_parameters (
    id SERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    mode_id INTEGER NOT NULL REFERENCES public.room_modes(id),
    submode_id INTEGER REFERENCES public.flower_submodes(id),
    day_start_time TIME NOT NULL DEFAULT '17:00',
    night_start_time TIME NOT NULL DEFAULT '11:00',
    light_ramp_up_minutes INTEGER NOT NULL DEFAULT 15,
    light_ramp_down_minutes INTEGER NOT NULL DEFAULT 15,
    main_light_intensity INTEGER NOT NULL DEFAULT 100,
    supplemental_light_intensity INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (location, cluster, mode_id, submode_id)
);
CREATE INDEX idx_mode_parameters_lookup
    ON public.mode_parameters(location, cluster, mode_id, submode_id);
-- The checked-in Alembic baseline uses an id-only primary key here, which
-- TimescaleDB cannot partition. Match the lifecycle harness's disposable
-- composite-key variant so the actual Alembic baseline can run.
CREATE TABLE public.control_history (
    id BIGSERIAL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
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
    PRIMARY KEY (id, timestamp)
);

-- automation_state needs the same disposable composite-key variant so
-- TimescaleDB can partition it while applying the Alembic baseline.
CREATE TABLE public.automation_state (
    id BIGSERIAL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
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
    PRIMARY KEY (timestamp, id)
);
