"""Add per-room PID parameters.

Add location, cluster, binary_hysteresis columns to pid_parameters and
pid_parameter_history. Change the primary key on pid_parameters from
device_type alone to (location, cluster, device_type) so each room/cluster
combination can have its own PID tuning.

Revision ID: 007_pid_per_room
Revises: 006_manual_expires_at
Create Date: 2026-07-05

"""

from alembic import op

revision = "007_pid_per_room"
down_revision = "006_manual_expires_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- pid_parameters ---
    op.execute("ALTER TABLE pid_parameters ADD COLUMN IF NOT EXISTS location TEXT;")
    op.execute("ALTER TABLE pid_parameters ADD COLUMN IF NOT EXISTS cluster TEXT;")
    op.execute("ALTER TABLE pid_parameters ADD COLUMN IF NOT EXISTS binary_hysteresis REAL;")

    # Backfill existing rows before dropping the old PK
    op.execute(
        "UPDATE pid_parameters SET location = 'Flower Room', cluster = 'main', binary_hysteresis = 0.1 WHERE location IS NULL;"
    )

    # Drop old single-column PK
    op.execute("ALTER TABLE pid_parameters DROP CONSTRAINT IF EXISTS pid_parameters_pkey;")

    # Add composite PK
    op.execute("ALTER TABLE pid_parameters ADD PRIMARY KEY (location, cluster, device_type);")

    # --- pid_parameter_history ---
    op.execute("ALTER TABLE pid_parameter_history ADD COLUMN IF NOT EXISTS location TEXT;")
    op.execute("ALTER TABLE pid_parameter_history ADD COLUMN IF NOT EXISTS cluster TEXT;")
    op.execute("ALTER TABLE pid_parameter_history ADD COLUMN IF NOT EXISTS binary_hysteresis REAL;")

    # Backfill existing rows
    op.execute(
        "UPDATE pid_parameter_history SET location = 'Flower Room', cluster = 'main', binary_hysteresis = 0.1 WHERE location IS NULL;"
    )


def downgrade() -> None:
    # --- pid_parameter_history ---
    op.execute("ALTER TABLE pid_parameter_history DROP COLUMN IF EXISTS binary_hysteresis;")
    op.execute("ALTER TABLE pid_parameter_history DROP COLUMN IF EXISTS cluster;")
    op.execute("ALTER TABLE pid_parameter_history DROP COLUMN IF EXISTS location;")

    # --- pid_parameters ---
    # Revert PK first (must drop composite before restoring old one)
    op.execute("ALTER TABLE pid_parameters DROP CONSTRAINT IF EXISTS pid_parameters_pkey;")
    op.execute("ALTER TABLE pid_parameters ADD PRIMARY KEY (device_type);")

    op.execute("ALTER TABLE pid_parameters DROP COLUMN IF EXISTS binary_hysteresis;")
    op.execute("ALTER TABLE pid_parameters DROP COLUMN IF EXISTS cluster;")
    op.execute("ALTER TABLE pid_parameters DROP COLUMN IF EXISTS location;")
