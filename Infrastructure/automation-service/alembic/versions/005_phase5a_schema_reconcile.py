"""Phase 5a schema reconciliation: CAGGs + measurement_with_metadata view.

Captures the TimescaleDB state that has been live in production since
before this repo switched Alembic as the canonical owner. This revision
is a strict no-op on the existing Pi database but correctly reproduces
the state on a freshly-built DB.

Idempotency strategy
--------------------
The TimescaleDB ``add_*_policy()`` family enforces an OWNERSHIP check
*before* honouring the ``if_not_exists => TRUE`` short-circuit. The Pi
database's continuous aggregates were created by ``postgres`` (the
TimescaleDB extension owner), not ``cea_user`` (who runs migrations).
Calling those functions as ``cea_user`` therefore raises
``InsufficientPrivilege`` even when the requested policy already exists.

To stay safe in both contexts (existing prod / fresh install) every
policy block first queries ``timescaledb_information.jobs`` (which
``cea_user`` is allowed to read). If a matching policy is already
attached, the migration skips the ``add_*_policy()`` call entirely. On a
fresh DB the lookups return zero rows and the ``add_*_policy()`` call is
made for the first time — at which point ``cea_user`` becomes the policy
owner and re-runs are no-ops via the catalogue check.

The CAGG materialised views themselves are guarded by
``CREATE MATERIALIZED VIEW IF NOT EXISTS``, which short-circuits at
parse time without ownership checks.

Coverage
--------
  - continuous aggregates: measurement_1min, _5min, _hourly, _daily
  - their refresh policies
  - compression policy on measurement (compress_after=90 days)
  - compression policy on effective_setpoints (compress_after=7 days)
  - retention policies on both hypertables (drop_after=2 years)
  - measurement_with_metadata: drops the never-returning
    ``LEFT JOIN room ON room_id IS NULL`` placeholder that was committed
    in cea_schema.sql but replaced long ago in
    migrate_remove_facility.sql.

Physical replication on iskraprojectcea replays all of this via WAL.
No iskra-side action required.

Revision ID: 005_phase5a_reconcile
Revises: 003_climate_single_row
Create Date: 2026-04-18
"""

from alembic import op

revision = "005_phase5a_reconcile"
# 004_drop_legacy_setpoint_columns was deleted as part of dead-code removal.
# Point at 003 so `alembic upgrade head` stays safe.
down_revision = "003_climate_single_row"
branch_labels = None
depends_on = None


_CAGG_DEFS = {
    "measurement_1min": "1 minute",
    "measurement_5min": "5 minutes",
    "measurement_hourly": "1 hour",
    "measurement_daily": "1 day",
}

_REFRESH_POLICIES = {
    # (start_offset, end_offset, schedule_interval)
    "measurement_1min": ("2 hours", "1 minute", "1 minute"),
    "measurement_5min": ("30 minutes", "5 minutes", "5 minutes"),
    "measurement_hourly": ("3 hours", "1 hour", "1 hour"),
    "measurement_daily": ("3 days", "1 day", "1 day"),
}

_COMPRESSION_POLICIES = {
    # hypertable -> compress_after
    "measurement": "90 days",
    "effective_setpoints": "7 days",
}

_RETENTION_POLICIES = {
    # hypertable -> drop_after
    "measurement": "2 years",
    "effective_setpoints": "2 years",
}


def _do_block_if_no_job(proc_name: str, hypertable: str, body_sql: str) -> str:
    """Wrap ``body_sql`` so it runs only when no policy job is attached.

    Avoids the ownership check of ``add_*_policy()`` on already-managed
    objects (see module docstring).
    """
    return f"""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
              FROM timescaledb_information.jobs
             WHERE proc_name = '{proc_name}'
               AND hypertable_name = '{hypertable}'
        ) THEN
            {body_sql}
        END IF;
    END
    $$;
    """


def upgrade() -> None:
    for view, bucket in _CAGG_DEFS.items():
        op.execute(
            f"""
            CREATE MATERIALIZED VIEW IF NOT EXISTS {view}
            WITH (timescaledb.continuous) AS
            SELECT time_bucket('{bucket}'::interval, m."time") AS bucket,
                   m.sensor_id,
                   avg(m.value)   AS avg_value,
                   min(m.value)   AS min_value,
                   max(m.value)   AS max_value,
                   count(*)       AS sample_count
              FROM measurement m
             GROUP BY time_bucket('{bucket}'::interval, m."time"), m.sensor_id
            WITH NO DATA;
            """
        )

    for view, (start_off, end_off, sched) in _REFRESH_POLICIES.items():
        op.execute(
            _do_block_if_no_job(
                "policy_refresh_continuous_aggregate",
                view,
                f"""
                PERFORM add_continuous_aggregate_policy(
                    '{view}',
                    start_offset => INTERVAL '{start_off}',
                    end_offset   => INTERVAL '{end_off}',
                    schedule_interval => INTERVAL '{sched}'
                );
                """,
            )
        )

    for hypertable, after in _COMPRESSION_POLICIES.items():
        op.execute(
            _do_block_if_no_job(
                "policy_compression",
                hypertable,
                f"""
                PERFORM add_compression_policy(
                    '{hypertable}',
                    compress_after => INTERVAL '{after}'
                );
                """,
            )
        )

    for hypertable, after in _RETENTION_POLICIES.items():
        op.execute(
            _do_block_if_no_job(
                "policy_retention",
                hypertable,
                f"""
                PERFORM add_retention_policy(
                    '{hypertable}',
                    drop_after => INTERVAL '{after}'
                );
                """,
            )
        )

    op.execute(
        """
        CREATE OR REPLACE VIEW measurement_with_metadata AS
        SELECT m.time,
               m.sensor_id,
               m.value,
               m.status,
               s.name      AS sensor_name,
               s.unit      AS sensor_unit,
               s.data_type AS sensor_data_type,
               d.device_id,
               d.name      AS device_name,
               d.type      AS device_type,
               r.room_id,
               r.name       AS room_name,
               r.target_vpd,
               r.target_temp
          FROM measurement m
          JOIN sensor s ON m.sensor_id = s.sensor_id
          JOIN device d ON s.device_id = d.device_id
          LEFT JOIN rack rk ON d.rack_id = rk.rack_id
          LEFT JOIN room r  ON rk.room_id = r.room_id;
        """
    )


def downgrade() -> None:
    # Intentional no-op. Downgrade would delete production aggregates and
    # multiple days of materialized data; that is outside the guardrails
    # of this campaign (see non-negotiable #7 in the refactor roadmap).
    # If a rebuild is ever needed, drop the CAGGs manually in a planned
    # maintenance window.
    pass
