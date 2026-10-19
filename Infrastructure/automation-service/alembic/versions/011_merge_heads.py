"""Merge the relay-observation and device-name heads.

Revision ID: 011_merge_heads
Revises: 010_canonicalize_device_names, 05_relay_observation
Create Date: 2026-10-19

"""

from __future__ import annotations

revision = "011_merge_heads"
down_revision = ("010_canonicalize_device_names", "05_relay_observation")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change; joins the two heads into one."""


def downgrade() -> None:
    """No schema change."""
