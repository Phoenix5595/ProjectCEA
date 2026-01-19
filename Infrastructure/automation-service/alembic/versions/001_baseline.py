"""Baseline migration - current schema state.

This is a baseline migration representing the schema as of January 2026.
Production databases should be marked as already having this migration.

Revision ID: 001_baseline
Revises:
Create Date: 2026-01-19
"""

from alembic import op
import sqlalchemy as sa

revision = "001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Device states table
    op.execute("""
        CREATE TABLE IF NOT EXISTS device_states (
            id BIGSERIAL PRIMARY KEY,
            location TEXT NOT NULL,
            cluster TEXT NOT NULL,
            device_name TEXT NOT NULL,
            channel INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15),
            state INTEGER NOT NULL CHECK (state IN (0, 1)),
            mode TEXT NOT NULL CHECK (mode IN ('manual', 'auto', 'scheduled')),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(location, cluster, device_name)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_device_states_location_cluster 
        ON device_states(location, cluster)
    """)

    # Control history table (TimescaleDB hypertable)
    op.execute("""
        CREATE TABLE IF NOT EXISTS control_history (
            id BIGSERIAL PRIMARY KEY,
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
            setpoint REAL
        )
    """)
    op.execute("SELECT create_hypertable('control_history', 'timestamp', if_not_exists => TRUE)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_control_history_location ON control_history(location, cluster)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_control_history_timestamp ON control_history(timestamp DESC)"
    )

    # Setpoints table
    op.execute("""
        CREATE TABLE IF NOT EXISTS setpoints (
            id BIGSERIAL PRIMARY KEY,
            location TEXT NOT NULL,
            cluster TEXT NOT NULL,
            heating_setpoint REAL,
            cooling_setpoint REAL,
            humidity REAL,
            co2 REAL,
            vpd REAL,
            mode TEXT CHECK (mode IS NULL OR mode IN ('DAY', 'NIGHT', 'TRANSITION', 'PRE_DAY', 'PRE_NIGHT')),
            ramp_in_duration INTEGER CHECK (ramp_in_duration IS NULL OR (ramp_in_duration >= 0 AND ramp_in_duration <= 240)),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_setpoints_loc_cluster_mode_updated_at ON setpoints(location, cluster, mode, updated_at DESC)"
    )

    # Schedules table
    op.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            cluster TEXT NOT NULL,
            device_name TEXT NOT NULL,
            day_of_week INTEGER,
            start_time TIME NOT NULL,
            end_time TIME NOT NULL,
            enabled BOOLEAN DEFAULT TRUE,
            mode TEXT,
            target_intensity REAL,
            ramp_up_duration INTEGER,
            ramp_down_duration INTEGER,
            pre_day_duration INTEGER CHECK (pre_day_duration IS NULL OR (pre_day_duration >= 0 AND pre_day_duration <= 240)),
            pre_night_duration INTEGER CHECK (pre_night_duration IS NULL OR (pre_night_duration >= 0 AND pre_night_duration <= 240)),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # Rules table
    op.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            enabled BOOLEAN DEFAULT TRUE,
            location TEXT NOT NULL,
            cluster TEXT NOT NULL,
            condition_sensor TEXT NOT NULL,
            condition_operator TEXT NOT NULL,
            condition_value REAL NOT NULL,
            action_device TEXT NOT NULL,
            action_state INTEGER NOT NULL,
            priority INTEGER DEFAULT 0,
            schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # Automation state table (TimescaleDB hypertable)
    op.execute("""
        CREATE TABLE IF NOT EXISTS automation_state (
            id BIGSERIAL PRIMARY KEY,
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
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("SELECT create_hypertable('automation_state', 'timestamp', if_not_exists => TRUE)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_automation_state_location ON automation_state(location, cluster)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_automation_state_timestamp ON automation_state(timestamp DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_automation_state_device ON automation_state(location, cluster, device_name)"
    )

    # Effective setpoints table (TimescaleDB hypertable)
    op.execute("""
        CREATE TABLE IF NOT EXISTS effective_setpoints (
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
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
            ramp_progress_light REAL
        )
    """)
    op.execute(
        "SELECT create_hypertable('effective_setpoints', 'timestamp', if_not_exists => TRUE)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_effective_setpoints_location ON effective_setpoints(location, cluster)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_effective_setpoints_timestamp ON effective_setpoints(timestamp DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_effective_setpoints_device ON effective_setpoints(location, cluster, device_name, timestamp DESC)"
    )

    # PID parameters table
    op.execute("""
        CREATE TABLE IF NOT EXISTS pid_parameters (
            device_type TEXT PRIMARY KEY,
            kp REAL NOT NULL,
            ki REAL NOT NULL,
            kd REAL NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by TEXT,
            source TEXT
        )
    """)

    # PID parameter history table
    op.execute("""
        CREATE TABLE IF NOT EXISTS pid_parameter_history (
            id BIGSERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            device_type TEXT NOT NULL,
            kp REAL NOT NULL,
            ki REAL NOT NULL,
            kd REAL NOT NULL,
            updated_by TEXT,
            source TEXT
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pid_parameter_history_device_type ON pid_parameter_history(device_type, timestamp DESC)"
    )

    # Config versions table
    op.execute("""
        CREATE TABLE IF NOT EXISTS config_versions (
            version_id BIGSERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            author TEXT,
            comment TEXT,
            config_type TEXT NOT NULL,
            location TEXT,
            cluster TEXT,
            changes JSONB
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_config_versions_timestamp ON config_versions(timestamp DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_config_versions_type ON config_versions(config_type)"
    )

    # Device mappings table
    op.execute("""
        CREATE TABLE IF NOT EXISTS device_mappings (
            id BIGSERIAL PRIMARY KEY,
            location TEXT NOT NULL,
            cluster TEXT NOT NULL,
            device_name TEXT NOT NULL,
            channel INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15),
            active_high BOOLEAN NOT NULL DEFAULT TRUE,
            safe_state INTEGER NOT NULL CHECK (safe_state IN (0, 1)),
            mcp_board_id INTEGER,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(location, cluster, device_name)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_mappings_location_cluster ON device_mappings(location, cluster)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS device_mappings CASCADE")
    op.execute("DROP TABLE IF EXISTS config_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS pid_parameter_history CASCADE")
    op.execute("DROP TABLE IF EXISTS pid_parameters CASCADE")
    op.execute("DROP TABLE IF EXISTS effective_setpoints CASCADE")
    op.execute("DROP TABLE IF EXISTS automation_state CASCADE")
    op.execute("DROP TABLE IF EXISTS rules CASCADE")
    op.execute("DROP TABLE IF EXISTS schedules CASCADE")
    op.execute("DROP TABLE IF EXISTS setpoints CASCADE")
    op.execute("DROP TABLE IF EXISTS control_history CASCADE")
    op.execute("DROP TABLE IF EXISTS device_states CASCADE")
