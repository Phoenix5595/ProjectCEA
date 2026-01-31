"""Ensure single climate schedule row per (location, cluster).

- Cleanup: keep one row with device_name = 'climate' per (location, cluster) (max id).
- Optional: migrate legacy rows with pre_day/pre_night set but device_name != 'climate'.
- Add partial unique index so at most one climate row per (location, cluster).

Revision ID: 003_climate_single_row
Revises: 002_load_percent
Create Date: 2026-01-31

"""

from alembic import op

revision = "003_climate_single_row"
down_revision = "002_load_percent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migrate legacy rows: pre_day_duration/pre_night_duration set but device_name != 'climate'
    op.execute("""
        UPDATE schedules
        SET device_name = 'climate'
        WHERE (pre_day_duration IS NOT NULL OR pre_night_duration IS NOT NULL)
          AND (device_name IS NULL OR device_name != 'climate')
    """)

    # Delete duplicate climate rows, keeping the one with max(id) per (location, cluster)
    op.execute("""
        DELETE FROM schedules s1
        WHERE s1.device_name = 'climate'
          AND EXISTS (
            SELECT 1 FROM schedules s2
            WHERE s2.location = s1.location AND s2.cluster = s1.cluster
              AND s2.device_name = 'climate' AND s2.id > s1.id
          )
    """)

    # Enforce at most one climate row per (location, cluster)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_schedules_one_climate_per_loc_cluster
        ON schedules (location, cluster) WHERE device_name = 'climate'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_schedules_one_climate_per_loc_cluster")
