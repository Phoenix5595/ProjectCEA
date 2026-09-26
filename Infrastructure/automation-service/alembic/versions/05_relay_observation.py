"""Persist sample-time physical relay and assignment observations.

Revision ID: 05_relay_observation
Revises: 04fbbb9b5ba4
Create Date: 2026-09-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "05_relay_observation"
down_revision: str | Sequence[str] | None = "04fbbb9b5ba4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the append-only observation hypertable and its query indexes."""
    if op.get_context().dialect.name != "postgresql":
        raise NotImplementedError("Migration requires PostgreSQL")

    op.execute("""
        CREATE TABLE relay_observation (
            observation_id BIGINT GENERATED ALWAYS AS IDENTITY,
            observed_at TIMESTAMPTZ NOT NULL,
            session_id UUID NOT NULL,
            channel SMALLINT NULL,
            observed_state BOOLEAN NULL,
            device_id INTEGER NULL,
            device_name TEXT NULL,
            device_type TEXT NULL,
            location TEXT NULL,
            cluster TEXT NULL,
            registry_version BIGINT NOT NULL,
            reason TEXT NOT NULL,
            CONSTRAINT pk_relay_observation
                PRIMARY KEY (observed_at, observation_id),
            CONSTRAINT ck_relay_observation_channel
                CHECK (channel IS NULL OR channel BETWEEN 0 AND 15),
            CONSTRAINT ck_relay_observation_reason
                CHECK (reason IN (
                    'initial', 'state_changed', 'stale', 'recovered',
                    'assignment_changed', 'recording_gap', 'heartbeat'
                )),
            CONSTRAINT ck_relay_observation_channel_reason
                CHECK ((channel IS NULL) = (reason = 'heartbeat')),
            CONSTRAINT ck_relay_observation_heartbeat_payload
                CHECK (
                    reason <> 'heartbeat'
                    OR (
                        observed_state IS NULL
                        AND device_id IS NULL
                        AND device_name IS NULL
                        AND device_type IS NULL
                        AND location IS NULL
                        AND cluster IS NULL
                    )
                )
        )
    """)
    op.execute(
        "SELECT create_hypertable('relay_observation', 'observed_at', if_not_exists => TRUE)"
    )
    op.execute("""
        CREATE INDEX idx_relay_observation_location_cluster_device_time
        ON relay_observation (location, cluster, device_id, observed_at, observation_id)
    """)
    op.execute("""
        CREATE INDEX idx_relay_observation_channel_time
        ON relay_observation (channel, observed_at, observation_id)
    """)


def downgrade() -> None:
    """Drop only the relay observation hypertable and indexes introduced here."""
    if op.get_context().dialect.name != "postgresql":
        raise NotImplementedError("Migration requires PostgreSQL")

    op.execute("DROP INDEX IF EXISTS idx_relay_observation_location_cluster_device_time")
    op.execute("DROP INDEX IF EXISTS idx_relay_observation_channel_time")
    op.execute("DROP TABLE IF EXISTS relay_observation")
