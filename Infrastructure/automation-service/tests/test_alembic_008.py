"""Test alembic migration 008_device_registry.

Failing-first TDD: asserts table does NOT exist before migration,
asserts it DOES exist after upgrade, asserts downgrade cleans,
and asserts idempotency of downgrade + upgrade cycle.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
_DB_URL = "postgresql://cea_user:9GxVyxUDLu1zy8jNCYDjveRbx7mCCVIw@localhost:5432/cea_sensors"


def _alembic_cmd(*args: str) -> subprocess.CompletedProcess:
    """Run an alembic CLI command with correct PYTHONPATH and env."""
    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(
        [
            str(Path(__file__).resolve().parent.parent.parent),  # Infrastructure/
            str(Path(__file__).resolve().parent.parent),  # automation-service/
        ]
    )
    env["POSTGRES_PASSWORD"] = "9GxVyxUDLu1zy8jNCYDjveRbx7mCCVIw"
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(_ALEMBIC_INI), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture
def engine():
    """SQLAlchemy engine for DB introspection."""
    eng = create_engine(_DB_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def inspector(engine):
    """SQLAlchemy inspector for the test DB."""
    return inspect(engine)


class TestDeviceRegistryMigration:
    """End-to-end alembic migration test for 008_device_registry."""

    def setup_class(self):
        """Ensure we start from 007 so the 'before' assertion is valid."""
        _alembic_cmd("downgrade", "007_pid_per_room").check_returncode()

    def _table_exists(self, engine, table_name: str) -> bool:
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = :name"
                ),
                {"name": table_name},
            ).fetchone()
        return row is not None

    def test_table_does_not_exist_before_migration(self, engine):
        """FAILING test first: device_registry must not exist yet."""
        assert not self._table_exists(engine, "device_registry"), (
            "device_registry already exists — migration not needed or stale state"
        )

    def test_upgrade_creates_table_with_all_columns(self, engine, inspector):
        """After alembic upgrade head, table exists with correct columns."""
        result = _alembic_cmd("upgrade", "head")
        assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"

        assert self._table_exists(engine, "device_registry"), (
            "device_registry table missing after upgrade"
        )

        columns = {c["name"]: c for c in inspector.get_columns("device_registry")}
        expected_columns = [
            "device_id",
            "location",
            "cluster",
            "device_name",
            "display_name",
            "device_type",
            "channel",
            "dimming_enabled",
            "dimming_type",
            "dimming_board_id",
            "dimming_channel",
            "safety_level",
            "pid_enabled",
            "interlock_with",
            "pid_setpoints",
            "per_room_index",
            "created_at",
            "updated_at",
        ]
        for col in expected_columns:
            assert col in columns, f"Missing column: {col}"

        # Verify specific column types / nullability
        assert columns["device_id"]["nullable"] is False
        assert columns["location"]["nullable"] is False
        assert columns["cluster"]["nullable"] is False
        assert columns["device_name"]["nullable"] is False
        assert columns["device_type"]["nullable"] is False
        assert columns["display_name"]["nullable"] is True
        assert columns["channel"]["nullable"] is True
        assert columns["dimming_board_id"]["nullable"] is True
        assert columns["dimming_channel"]["nullable"] is True
        assert columns["per_room_index"]["nullable"] is True

    def test_unique_constraints_exist(self, inspector):
        """Verify all UNIQUE constraints including partial ones."""
        uqs = inspector.get_unique_constraints("device_registry")
        uq_names = {uq["name"] for uq in uqs}

        assert "uq_device_registry_loc_cluster_name" in uq_names, (
            "Missing UNIQUE(location, cluster, device_name)"
        )

        # SQLAlchemy 2.0 partial unique constraints are implemented as
        # unique indexes with postgresql_where.
        indexes = {idx["name"]: idx for idx in inspector.get_indexes("device_registry")}
        assert "uq_device_registry_dfr_channel" in indexes, (
            "Missing UNIQUE(dimming_board_id, dimming_channel) WHERE dimming_board_id IS NOT NULL"
        )
        assert indexes["uq_device_registry_dfr_channel"]["unique"] is True
        assert "uq_device_registry_loc_room_index" in indexes, (
            "Missing UNIQUE(location, per_room_index) WHERE device_type='light'"
        )
        assert indexes["uq_device_registry_loc_room_index"]["unique"] is True

    def test_check_constraint_exists(self, engine):
        """Verify CHECK(cluster='main') exists."""
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'device_registry'::regclass AND contype = 'c'"
                )
            ).fetchone()
        assert row is not None, "No CHECK constraint found on device_registry"
        assert "ck_device_registry_cluster_main" in row[0], (
            f"Expected ck_device_registry_cluster_main, got {row[0]}"
        )

    def test_indexes_exist(self, inspector):
        """Verify all indexes including partial ones."""
        indexes = {idx["name"]: idx for idx in inspector.get_indexes("device_registry")}

        assert "idx_device_registry_location" in indexes, "Missing idx_device_registry_location"
        assert "idx_device_registry_light" in indexes, "Missing idx_device_registry_light"
        assert "idx_device_registry_relay" in indexes, "Missing idx_device_registry_relay"

    def test_downgrade_removes_table(self, engine):
        """Downgrade by one revision drops device_registry."""
        result = _alembic_cmd("downgrade", "-1")
        assert result.returncode == 0, f"alembic downgrade -1 failed:\n{result.stderr}"

        assert not self._table_exists(engine, "device_registry"), (
            "device_registry still exists after downgrade"
        )

    def test_idempotency_downgrade_upgrade(self, engine, inspector):
        """Downgrade then upgrade again leaves table in identical state."""
        # Ensure we're at head first
        _alembic_cmd("upgrade", "head").check_returncode()

        # Downgrade
        _alembic_cmd("downgrade", "-1").check_returncode()
        assert not self._table_exists(engine, "device_registry")

        # Upgrade again
        _alembic_cmd("upgrade", "head").check_returncode()
        assert self._table_exists(engine, "device_registry")

        # Verify constraints and indexes are still present
        uqs = inspector.get_unique_constraints("device_registry")
        uq_names = {uq["name"] for uq in uqs}
        assert "uq_device_registry_loc_cluster_name" in uq_names

        indexes = {idx["name"]: idx for idx in inspector.get_indexes("device_registry")}
        assert "uq_device_registry_dfr_channel" in indexes
        assert indexes["uq_device_registry_dfr_channel"]["unique"] is True
        assert "uq_device_registry_loc_room_index" in indexes
        assert indexes["uq_device_registry_loc_room_index"]["unique"] is True
        assert "idx_device_registry_location" in indexes
        assert "idx_device_registry_light" in indexes
        assert "idx_device_registry_relay" in indexes
