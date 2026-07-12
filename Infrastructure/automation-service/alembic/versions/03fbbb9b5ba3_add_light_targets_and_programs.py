"""Add light_target_intensity and light_programs tables.

Revision ID: 03fbbb9b5ba3
Revises: 009_seed_device_registry
Create Date: 2026-07-12 08:54:22.952819

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "03fbbb9b5ba3"
down_revision: str | Sequence[str] | None = "009_seed_device_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create lighting tables and migrate per-mode intensities."""
    if op.get_context().dialect.name != "postgresql":
        raise NotImplementedError("Migration requires PostgreSQL")

    # ------------------------------------------------------------------
    # light_target_intensity: per (device, mode) anchor for intensities.
    # ------------------------------------------------------------------
    op.create_table(
        "light_target_intensity",
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("device_registry.device_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mode_id",
            sa.Integer(),
            sa.ForeignKey("room_modes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_intensity", sa.REAL(), server_default=sa.text("10.0"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("device_id", "mode_id", name="pk_light_target_intensity"),
        sa.CheckConstraint(
            "target_intensity >= 0 AND target_intensity <= 100",
            name="ck_light_target_intensity_range",
        ),
    )
    op.create_index("idx_light_target_intensity_device", "light_target_intensity", ["device_id"])
    op.create_index("idx_light_target_intensity_mode", "light_target_intensity", ["mode_id"])

    # ------------------------------------------------------------------
    # light_programs: future program rows; created empty on migration.
    # ------------------------------------------------------------------
    op.create_table(
        "light_programs",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("device_registry.device_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("cluster", sa.Text(), server_default=sa.text("'main'"), nullable=False),
        sa.Column(
            "mode_id",
            sa.Integer(),
            sa.ForeignKey("room_modes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("program_type", sa.Text(), nullable=False),
        sa.Column("start_time", sa.TIME(), nullable=False),
        sa.Column("end_time", sa.TIME(), nullable=False),
        sa.Column("cycle_enabled", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("cycle_on_seconds", sa.Integer(), nullable=True),
        sa.Column("cycle_off_seconds", sa.Integer(), nullable=True),
        sa.Column("target_intensity", sa.REAL(), nullable=False),
        sa.Column("ramp_up_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("ramp_down_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "program_type IN ('supplemental', 'override')", name="ck_light_programs_type"
        ),
        sa.CheckConstraint(
            "target_intensity IS NULL OR (target_intensity >= 0 AND target_intensity <= 100)",
            name="ck_light_programs_intensity_range",
        ),
    )
    op.create_index(
        "idx_light_programs_lookup", "light_programs", ["location", "cluster", "enabled"]
    )
    op.create_index("idx_light_programs_device", "light_programs", ["device_id", "enabled"])

    # ------------------------------------------------------------------
    # Data migration: one light_target_intensity row per light per mode.
    # Use mode_parameters.main_light_intensity as the source value.
    # Idempotent via ON CONFLICT DO UPDATE.
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO light_target_intensity (device_id, mode_id, target_intensity, created_at, updated_at)
        SELECT DISTINCT ON (d.device_id, mp.mode_id)
            d.device_id,
            mp.mode_id,
            mp.main_light_intensity::REAL,
            NOW(),
            NOW()
        FROM device_registry d
        JOIN mode_parameters mp
          ON d.location = mp.location
         AND d.cluster = mp.cluster
        WHERE d.device_type = 'light'
        ORDER BY d.device_id, mp.mode_id
        ON CONFLICT (device_id, mode_id)
        DO UPDATE SET
            target_intensity = EXCLUDED.target_intensity,
            updated_at = NOW()
    """)


def downgrade() -> None:
    """Drop lighting tables."""
    op.drop_index("idx_light_programs_device", table_name="light_programs")
    op.drop_index("idx_light_programs_lookup", table_name="light_programs")
    op.drop_table("light_programs")

    op.drop_index("idx_light_target_intensity_mode", table_name="light_target_intensity")
    op.drop_index("idx_light_target_intensity_device", table_name="light_target_intensity")
    op.drop_table("light_target_intensity")
