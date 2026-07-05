"""Add manual_expires_at to control_history.

Revision ID: 006_manual_expires_at
Revises: 005_phase5a_reconcile
Create Date: 2026-07-05

"""

from alembic import op

revision = "006_manual_expires_at"
down_revision = "005_phase5a_reconcile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE control_history ADD COLUMN IF NOT EXISTS manual_expires_at TIMESTAMPTZ;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_control_history_manual_expires "
        "ON control_history(manual_expires_at) "
        "WHERE manual_expires_at IS NOT NULL AND mode = 'manual';"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_control_history_manual_expires;")
    op.execute("ALTER TABLE control_history DROP COLUMN IF EXISTS manual_expires_at;")
