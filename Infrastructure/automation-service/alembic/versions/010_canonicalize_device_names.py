"""Canonicalize non-light device names and move human-readable names to display_name.

Revision ID: 010_canonicalize_device_names
Revises: 03fbbb9b5ba3
Create Date: 2026-07-12

"""

from __future__ import annotations

from alembic import op

revision = "010_canonicalize_device_names"
down_revision = "04fbbb9b5ba4"
branch_labels = None
depends_on = None

# Regex for the canonical device_name pattern: <type>_<room_prefix>_<index>
_CANONICAL_PATTERN = r"^[a-z][a-z0-9]*_[fvlo]_\d+$"


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        raise NotImplementedError("Migration 010 requires PostgreSQL")

    # ------------------------------------------------------------------
    # Step 1: Copy current human-readable device_name into display_name
    # for non-light devices that do NOT already have a canonical name.
    # Only write display_name when it is NULL or empty so we do not
    # clobber data that may have been set by other means.
    # ------------------------------------------------------------------
    op.execute(f"""
        UPDATE device_registry
        SET display_name = device_name
        WHERE device_type != 'light'
          AND (display_name IS NULL OR display_name = '')
          AND device_name !~ '{_CANONICAL_PATTERN}'
    """)

    # ------------------------------------------------------------------
    # Step 2: Assign canonical device_name = <type>_<room_prefix>_<index>
    # Index is 1-based within each (device_type, location) group,
    # ordered by device_id for stability.
    # ------------------------------------------------------------------
    op.execute(f"""
        WITH ranked AS (
            SELECT
                device_id,
                device_type,
                location,
                ROW_NUMBER() OVER (
                    PARTITION BY device_type, location
                    ORDER BY device_id
                ) AS idx
            FROM device_registry
            WHERE device_type != 'light'
              AND device_name !~ '{_CANONICAL_PATTERN}'
        )
        UPDATE device_registry d
        SET device_name = (
            CASE r.device_type
                WHEN 'heating' THEN 'heater'
                WHEN 'dehumidifier' THEN 'dehumidifier'
                WHEN 'fan' THEN 'fan'
                WHEN 'exhaust' THEN 'exhaust'
                WHEN 'humidifier' THEN 'humidifier'
                WHEN 'co2' THEN 'co2'
                ELSE r.device_type
            END
            || '_'
            || CASE r.location
                WHEN 'Flower Room' THEN 'f'
                WHEN 'Veg Room' THEN 'v'
                WHEN 'Lab' THEN 'l'
                WHEN 'Outside' THEN 'o'
            END
            || '_'
            || r.idx::text
        )
        FROM ranked r
        WHERE d.device_id = r.device_id
    """)


def downgrade() -> None:
    """Restore human-readable names from display_name for non-light devices."""
    if op.get_context().dialect.name != "postgresql":
        raise NotImplementedError("Migration 010 requires PostgreSQL")

    op.execute(f"""
        UPDATE device_registry
        SET device_name = display_name
        WHERE device_type != 'light'
          AND display_name IS NOT NULL
          AND display_name != ''
          AND device_name ~ '{_CANONICAL_PATTERN}'
    """)
