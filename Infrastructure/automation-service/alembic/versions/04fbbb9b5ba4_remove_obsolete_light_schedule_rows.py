"""Remove obsolete light SUN/MOON schedule rows and room_schedule rows.

Revision ID: 04fbbb9b5ba4
Revises: 03fbbb9b5ba3
Create Date: 2026-07-12 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "04fbbb9b5ba4"
down_revision: str | Sequence[str] | None = "03fbbb9b5ba3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Delete obsolete per-device SUN/MOON rows for lights and room_schedule rows."""
    if op.get_context().dialect.name != "postgresql":
        raise NotImplementedError("Migration requires PostgreSQL")

    # ------------------------------------------------------------------
    # Pre-flight check: verify EVERY light with a SUN row has at least
    # one light_target_intensity row. If this returns ANY rows, abort.
    # ------------------------------------------------------------------
    op.execute("""
        DO $$
        DECLARE
            missing_count INTEGER;
        BEGIN
            SELECT COUNT(*) INTO missing_count
            FROM device_registry d
            JOIN schedules s ON s.device_name = d.device_name
            WHERE d.device_type = 'light'
              AND s.mode = 'SUN'
              AND d.device_id NOT IN (
                  SELECT device_id FROM light_target_intensity
              );

            IF missing_count > 0 THEN
                RAISE EXCEPTION 'Aborting migration: % lights have SUN rows but no light_target_intensity rows. Run T1 migration first.', missing_count;
            END IF;
        END $$;
    """)

    # ------------------------------------------------------------------
    # Delete per-device SUN/MOON rows for lights.
    # Use device_type = 'light' from device_registry, NOT device_name LIKE.
    # Non-light DAY/NIGHT rows are NOT deleted.
    # ------------------------------------------------------------------
    op.execute("""
        DELETE FROM schedules s
        USING device_registry d
        WHERE s.device_name = d.device_name
          AND d.device_type = 'light'
          AND s.mode IN ('SUN', 'MOON')
    """)

    # ------------------------------------------------------------------
    # Delete room_schedule rows (photoperiod now comes from mode_parameters).
    # ------------------------------------------------------------------
    op.execute("""
        DELETE FROM schedules WHERE device_name = 'room_schedule'
    """)


def downgrade() -> None:
    """No-op downgrade: deleted data cannot be restored."""
    pass
