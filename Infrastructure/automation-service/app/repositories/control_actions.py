from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import asyncpg

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool

# Column list for log_automation_state_batch copy_records_to_table.
# ⚠️ MUST match the automation_state table schema exactly.
# Source: Infrastructure/database/cea_schema.sql (automation_state table).
# If a migration adds/removes columns, update this list.
AUTOMATION_STATE_COLUMNS = [
    "timestamp",
    "location",
    "cluster",
    "device_name",
    "device_state",
    "device_mode",
    "pid_output",
    "duty_cycle_percent",
    "active_rule_ids",
    "active_schedule_ids",
    "control_reason",
    "schedule_ramp_up_duration",
    "schedule_ramp_down_duration",
    "schedule_photoperiod_hours",
    "pid_kp",
    "pid_ki",
    "pid_kd",
    "updated_at",
]


class ControlActionRepository(BaseRepository):
    """Repository for control action logging."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def log_control_action(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        old_state: int | None,
        new_state: int | None,
        mode: str = "auto",
        reason: str | None = None,
        sensor_value: float | None = None,
        setpoint: float | None = None,
        load_percent: float | None = None,
        manual_expires_at: datetime | None = None,
    ) -> bool:
        """Log a control action to control_history table."""
        if old_state is not None and new_state is not None and old_state == new_state:
            return True
        if reason is not None and len(reason) > 256:
            reason = reason[:256]
        if load_percent is not None:
            load_percent = max(0.0, min(100.0, float(load_percent)))
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO control_history
                    (timestamp, location, cluster, device_name, channel, old_state, new_state, mode, reason, sensor_value, setpoint, load_percent, manual_expires_at)
                    VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                    location,
                    cluster,
                    device_name,
                    channel,
                    old_state,
                    new_state,
                    mode,
                    reason,
                    sensor_value,
                    setpoint,
                    load_percent,
                    manual_expires_at,
                )
                return True
        except Exception as e:
            logger.error(f"Failed to log control action: {e}")
            return False

    async def get_recent_control_history(
        self, location: str, cluster: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return recent control_history rows for a location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT timestamp, location, cluster, device_name, old_state, new_state, mode, reason, load_percent
                    FROM control_history
                    WHERE location = $1 AND cluster = $2
                    ORDER BY timestamp DESC
                    LIMIT $3
                    """,
                    location,
                    cluster,
                    limit,
                )
                out: list[dict[str, Any]] = []
                for row in rows:
                    ts = row["timestamp"]
                    out.append(
                        {
                            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                            "location": row["location"],
                            "cluster": row["cluster"],
                            "device_name": row["device_name"],
                            "old_state": row["old_state"],
                            "new_state": row["new_state"],
                            "mode": row["mode"],
                            "reason": row["reason"],
                            "load_percent": row["load_percent"],
                        }
                    )
                return out
        except Exception as e:
            logger.error(f"Failed to get control history: {e}")
            return []

    async def get_control_history_filtered(
        self,
        location: str,
        cluster: str,
        limit: int = 100,
        channel: int | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return control_history rows with optional channel and time filters."""
        try:
            async with self.pool.acquire() as conn:
                query = """
                    SELECT timestamp, location, cluster, device_name, old_state, new_state, mode, reason, load_percent, channel
                    FROM control_history
                    WHERE location = $1 AND cluster = $2
                """
                params: list[Any] = [location, cluster]
                param_idx = 3

                if channel is not None:
                    query += f" AND channel = ${param_idx}"
                    params.append(channel)
                    param_idx += 1
                if since is not None:
                    query += f" AND timestamp >= ${param_idx}"
                    params.append(since)
                    param_idx += 1
                if until is not None:
                    query += f" AND timestamp <= ${param_idx}"
                    params.append(until)
                    param_idx += 1

                query += f" ORDER BY timestamp DESC LIMIT ${param_idx}"
                params.append(limit)

                rows = await conn.fetch(query, *params)
                out: list[dict[str, Any]] = []
                for row in rows:
                    ts = row["timestamp"]
                    out.append(
                        {
                            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                            "location": row["location"],
                            "cluster": row["cluster"],
                            "device_name": row["device_name"],
                            "old_state": row["old_state"],
                            "new_state": row["new_state"],
                            "mode": row["mode"],
                            "reason": row["reason"],
                            "load_percent": row["load_percent"],
                            "channel": row["channel"],
                        }
                    )
                return out
        except Exception as e:
            logger.error(f"Failed to get filtered control history: {e}")
            return []

    async def get_last_changed_per_channel(self) -> list[dict[str, Any]]:
        """Return the most recent timestamp per relay channel (0-15).

        Returns a list of 16 dicts: {"channel": int, "last_changed": str | None}
        where last_changed is ISO8601 or null if never changed.

        Bounded to last 30 days to prevent full hypertable scan.
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT channel, MAX(timestamp) AS last_changed
                    FROM control_history
                    WHERE channel BETWEEN 0 AND 15
                      AND timestamp > NOW() - INTERVAL '30 days'
                    GROUP BY channel
                    ORDER BY channel
                """)
            channel_map: dict[int, str | None] = {}
            for row in rows:
                ts = row["last_changed"]
                channel_map[row["channel"]] = ts.isoformat() if ts else None
            return [{"channel": i, "last_changed": channel_map.get(i)} for i in range(16)]
        except Exception as e:
            logger.error(f"Failed to get last changed per channel: {e}")
            return []

    async def get_expired_manual_overrides(self) -> list[dict[str, Any]]:
        """Return control_history rows where manual_expires_at has passed.

        Uses the partial index idx_control_history_manual_expires for
        efficient lookup of expired manual overrides.
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT location, cluster, device_name, channel
                    FROM control_history
                    WHERE manual_expires_at IS NOT NULL
                      AND manual_expires_at <= NOW()
                      AND mode = 'manual'
                    """
                )
                return [
                    {
                        "location": row["location"],
                        "cluster": row["cluster"],
                        "device_name": row["device_name"],
                        "channel": row["channel"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Failed to get expired manual overrides: {e}")
            return []

    async def clear_manual_expiry(self, location: str, cluster: str, device_name: str) -> bool:
        """Clear manual_expires_at for a device in control_history.

        Updates all rows for the device where manual_expires_at is set
        and mode is manual, preventing re-processing on subsequent ticks.
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE control_history
                    SET manual_expires_at = NULL
                    WHERE location = $1
                      AND cluster = $2
                      AND device_name = $3
                      AND manual_expires_at IS NOT NULL
                      AND mode = 'manual'
                    """,
                    location,
                    cluster,
                    device_name,
                )
                return True
        except Exception as e:
            logger.error(
                f"Failed to clear manual expiry for {location}/{cluster}/{device_name}: {e}"
            )
            return False

    async def log_automation_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: int,
        device_mode: str,
        pid_output: float | None,
        duty_cycle_percent: float | None,
        active_rule_ids: list[int],
        active_schedule_ids: list[int],
        control_reason: str,
        schedule_ramp_up_duration: int | None = None,
        schedule_ramp_down_duration: int | None = None,
        schedule_photoperiod_hours: float | None = None,
        pid_kp: float | None = None,
        pid_ki: float | None = None,
        pid_kd: float | None = None,
    ) -> bool:
        """Log automation state to automation_state table."""
        try:
            async with self.pool.acquire() as conn:
                try:
                    await conn.execute(
                        """
                        INSERT INTO automation_state
                        (timestamp, location, cluster, device_name, device_state, device_mode,
                         pid_output, duty_cycle_percent, active_rule_ids, active_schedule_ids,
                         control_reason, schedule_ramp_up_duration, schedule_ramp_down_duration,
                         schedule_photoperiod_hours, pid_kp, pid_ki, pid_kd, updated_at)
                        VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, NOW())
                    """,
                        location,
                        cluster,
                        device_name,
                        device_state,
                        device_mode,
                        pid_output,
                        duty_cycle_percent,
                        active_rule_ids,
                        active_schedule_ids,
                        control_reason,
                        schedule_ramp_up_duration,
                        schedule_ramp_down_duration,
                        schedule_photoperiod_hours,
                        pid_kp,
                        pid_ki,
                        pid_kd,
                    )
                except asyncpg.PostgresError:
                    # Fallback for older schemas
                    await conn.execute(
                        """
                        INSERT INTO automation_state
                        (timestamp, location, cluster, device_name, device_state, device_mode,
                         pid_output, duty_cycle_percent, active_rule_ids, active_schedule_ids,
                         control_reason, updated_at)
                        VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                    """,
                        location,
                        cluster,
                        device_name,
                        device_state,
                        device_mode,
                        pid_output,
                        duty_cycle_percent,
                        active_rule_ids,
                        active_schedule_ids,
                        control_reason,
                    )
                return True
        except Exception as e:
            logger.error(f"Failed to log automation state: {e}")
            return False

    async def log_automation_state_batch(
        self,
        records: list[dict[str, Any]],
    ) -> bool:
        """Log multiple automation state records in a single INSERT.

        This is a SAME-TICK optimization — records are collected during the tick
        and written immediately. NOT a deferred flush.

        Args:
            records: List of dicts, each containing all automation_state columns

        Returns:
            True if successful, False otherwise
        """
        if not records:
            return True

        try:
            async with self.pool.acquire() as conn:
                # Build multi-row VALUES clause
                columns = AUTOMATION_STATE_COLUMNS

                # Use asyncpg's copy_records_to_table for efficient bulk insert
                rows = []
                for r in records:
                    rows.append(
                        (
                            r.get("timestamp", datetime.now()),
                            r["location"],
                            r["cluster"],
                            r["device_name"],
                            r["device_state"],
                            r["device_mode"],
                            r.get("pid_output"),
                            r.get("duty_cycle_percent"),
                            r.get("active_rule_ids", []),
                            r.get("active_schedule_ids", []),
                            r.get("control_reason", "unknown"),
                            r.get("schedule_ramp_up_duration"),
                            r.get("schedule_ramp_down_duration"),
                            r.get("schedule_photoperiod_hours"),
                            r.get("pid_kp"),
                            r.get("pid_ki"),
                            r.get("pid_kd"),
                            datetime.now(),
                        )
                    )

                await conn.copy_records_to_table(
                    "automation_state",
                    records=rows,
                    columns=columns,
                )
                return True
        except Exception as e:
            logger.error(f"Failed to batch log automation state: {e}")
            return False
