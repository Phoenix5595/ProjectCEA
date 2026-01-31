"""Add load_percent to control_history.

Revision ID: 002_load_percent
Revises: 001_baseline
Create Date: 2026-01-31

"""

from alembic import op

revision = "002_load_percent"
down_revision = "001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE control_history ADD COLUMN IF NOT EXISTS load_percent REAL;")


def downgrade() -> None:
    op.execute("ALTER TABLE control_history DROP COLUMN IF EXISTS load_percent;")
