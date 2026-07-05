"""Create device_registry table.

Creates the canonical DB-backed device registry that replaces the YAML
``devices:`` block as the source of truth for device identity, type,
dimming bindings, and relay channel assignments.

Revision ID: 008_device_registry
Revises: 007_pid_per_room
Create Date: 2026-07-05

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008_device_registry"
down_revision = "007_pid_per_room"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        raise NotImplementedError("Migration 008 requires PostgreSQL")

    op.create_table(
        "device_registry",
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.Identity(),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("cluster", sa.Text(), nullable=False),
        sa.Column("device_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("device_type", sa.Text(), nullable=False),
        sa.Column("channel", sa.Integer(), nullable=True),
        sa.Column("dimming_enabled", sa.Boolean(), nullable=True),
        sa.Column("dimming_type", sa.Text(), nullable=True),
        sa.Column("dimming_board_id", sa.Integer(), nullable=True),
        sa.Column("dimming_channel", sa.SmallInteger(), nullable=True),
        sa.Column("safety_level", sa.Integer(), nullable=True),
        sa.Column("pid_enabled", sa.Boolean(), nullable=True),
        sa.Column("interlock_with", postgresql.JSONB(), nullable=True),
        sa.Column("pid_setpoints", postgresql.JSONB(), nullable=True),
        sa.Column("per_room_index", sa.Integer(), nullable=True),
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
        sa.UniqueConstraint(
            "location",
            "cluster",
            "device_name",
            name="uq_device_registry_loc_cluster_name",
        ),
        sa.CheckConstraint(
            "cluster = 'main'",
            name="ck_device_registry_cluster_main",
        ),
    )

    # Partial unique indexes (SQLAlchemy 2.0 UniqueConstraint does not accept
    # postgresql_where; use unique indexes instead).
    op.create_index(
        "uq_device_registry_dfr_channel",
        "device_registry",
        ["dimming_board_id", "dimming_channel"],
        unique=True,
        postgresql_where=sa.text("dimming_board_id IS NOT NULL"),
    )
    op.create_index(
        "uq_device_registry_loc_room_index",
        "device_registry",
        ["location", "per_room_index"],
        unique=True,
        postgresql_where=sa.text("device_type = 'light'"),
    )

    # Partial indexes
    op.create_index(
        "idx_device_registry_location",
        "device_registry",
        ["location"],
    )
    op.create_index(
        "idx_device_registry_light",
        "device_registry",
        ["location", "device_type"],
        postgresql_where=sa.text("device_type = 'light'"),
    )
    op.create_index(
        "idx_device_registry_relay",
        "device_registry",
        ["channel"],
        postgresql_where=sa.text("channel IS NOT NULL AND device_type = 'light'"),
    )


def downgrade() -> None:
    op.drop_index("idx_device_registry_relay", table_name="device_registry")
    op.drop_index("idx_device_registry_light", table_name="device_registry")
    op.drop_index("idx_device_registry_location", table_name="device_registry")
    op.drop_table("device_registry")
