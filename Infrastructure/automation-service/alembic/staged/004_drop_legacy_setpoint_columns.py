"""Drop legacy 4-period setpoint columns from mode_parameters.

Climate setpoints are now managed by the climate_periods table.
The mode_parameters table retains only photoperiod/light fields.

Revision ID: 004_drop_legacy
Revises: 003_climate_single_row
Create Date: 2026-03-26
"""

from alembic import op

revision = "004_drop_legacy"
down_revision = "003_climate_single_row"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop legacy 4-period climate setpoint columns
    # Using IF EXISTS for idempotency - safe to re-run
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS ramp_up_minutes")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS ramp_down_minutes")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_day_minutes")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_night_minutes")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_day_heat_temp")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_day_cool_temp")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_day_vpd")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_day_co2")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS day_heat_temp")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS day_cool_temp")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS day_vpd")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS day_co2")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS day_leaf_delta")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_night_heat_temp")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_night_cool_temp")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_night_vpd")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS pre_night_co2")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS night_heat_temp")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS night_cool_temp")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS night_vpd")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS night_co2")
    op.execute("ALTER TABLE mode_parameters DROP COLUMN IF EXISTS night_leaf_delta")


def downgrade() -> None:
    # Re-add legacy 4-period climate setpoint columns with original types and defaults
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS ramp_up_minutes INTEGER NOT NULL DEFAULT 30
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS ramp_down_minutes INTEGER NOT NULL DEFAULT 30
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_day_minutes INTEGER NOT NULL DEFAULT 30
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_night_minutes INTEGER NOT NULL DEFAULT 30
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_day_heat_temp REAL NOT NULL DEFAULT 22.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_day_cool_temp REAL NOT NULL DEFAULT 26.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_day_vpd REAL NOT NULL DEFAULT 0.9
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_day_co2 INTEGER NOT NULL DEFAULT 700
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS day_heat_temp REAL NOT NULL DEFAULT 24.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS day_cool_temp REAL NOT NULL DEFAULT 28.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS day_vpd REAL NOT NULL DEFAULT 1.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS day_co2 INTEGER NOT NULL DEFAULT 800
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS day_leaf_delta REAL NOT NULL DEFAULT -2.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_night_heat_temp REAL NOT NULL DEFAULT 22.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_night_cool_temp REAL NOT NULL DEFAULT 26.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_night_vpd REAL NOT NULL DEFAULT 0.9
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS pre_night_co2 INTEGER NOT NULL DEFAULT 700
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS night_heat_temp REAL NOT NULL DEFAULT 20.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS night_cool_temp REAL NOT NULL DEFAULT 24.0
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS night_vpd REAL NOT NULL DEFAULT 0.8
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS night_co2 INTEGER NOT NULL DEFAULT 600
    """)
    op.execute("""
        ALTER TABLE mode_parameters
        ADD COLUMN IF NOT EXISTS night_leaf_delta REAL NOT NULL DEFAULT -1.0
    """)
