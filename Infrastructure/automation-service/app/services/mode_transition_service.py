"""Activation owner: identity, schedules, history and revision in one transaction."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.control.runtime_device_registry import RuntimeSnapshotPublicationError
from app.repositories.room_modes import (
    InvalidProfileIdentityError,
    ProfileNotFoundError,
    RoomModeRepository,
)
from app.services.room_schedule_service import RoomScheduleService
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from asyncpg import Connection

    from app.control.runtime_device_registry import RuntimeDeviceRegistry
    from app.control.scheduler import Scheduler
    from app.database import DatabaseManager

logger = get_logger(__name__)

ACTIVATION_REVISION_CONFLICT = "activation_revision_conflict"
ACTIVATION_RUNTIME_REFRESH_FAILED = "activation_runtime_refresh_failed"


class _TransitionRejected(Exception):
    """Internal typed failure that rolls back the activation transaction."""

    def __init__(self, message: str, error_code: str | None = None) -> None:
        self.message = message
        self.error_code = error_code
        super().__init__(message)


class ModeTransitionResult:
    """Result of a mode transition operation."""

    def __init__(
        self,
        success: bool,
        location: str,
        cluster: str,
        old_mode: dict[str, Any] | None,
        new_mode: dict[str, Any] | None,
        schedule_sync_result: dict[str, Any] | None,
        message: str = "",
        *,
        runtime_ready: bool = True,
        config_revision: str | None = None,
        warning: str | None = None,
        error_code: str | None = None,
    ):
        self.success = success
        self.location = location
        self.cluster = cluster
        self.old_mode = old_mode
        self.new_mode = new_mode
        self.schedule_sync_result = schedule_sync_result
        self.message = message
        self.runtime_ready = runtime_ready
        self.config_revision = config_revision
        self.warning = warning
        self.error_code = error_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "location": self.location,
            "cluster": self.cluster,
            "old_mode": self.old_mode,
            "new_mode": self.new_mode,
            "schedule_sync_result": self.schedule_sync_result,
            "message": self.message,
            "runtime_ready": self.runtime_ready,
            "config_revision": self.config_revision,
            "warning": self.warning,
            "error_code": self.error_code,
        }


class ModeTransitionService:
    """Commit activation, required schedules, history and revision atomically.

    The registry process lock spans transaction begin, the registry advisory
    lock, pending-snapshot construction, commit and snapshot publication, so a
    later activation cannot publish before an earlier one. Never hold the
    light-target active row lock while waiting for registry publication.
    """

    def __init__(
        self,
        db: DatabaseManager,
        schedule_service: RoomScheduleService,
        runtime_device_registry: RuntimeDeviceRegistry,
        scheduler: Scheduler | None,
    ):
        self._db = db
        self._schedule_service = schedule_service
        self._runtime_device_registry = runtime_device_registry
        self._scheduler = scheduler
        self._room_mode_repo = RoomModeRepository(db.pool)
        self._config_repo = db.config_repo

    async def execute_mode_transition(
        self,
        location: str,
        cluster: str,
        new_mode_id: int,
        new_submode_id: int | None,
        triggered_by: str,
        *,
        expected_config_revision: str | None = None,
    ) -> dict[str, Any]:
        """Activate one exact profile and synchronize its schedules atomically."""
        logger.info(
            "Starting mode transition for %s/%s to mode_id=%s, submode_id=%s (triggered by %s)",
            location,
            cluster,
            new_mode_id,
            new_submode_id,
            triggered_by,
        )
        if self._runtime_device_registry is None:
            return self._failure(
                location,
                cluster,
                None,
                "Runtime device registry is not configured",
            )
        try:
            committed = await self._runtime_device_registry.mutate(
                lambda connection: self._activate(
                    connection,
                    location,
                    cluster,
                    new_mode_id,
                    new_submode_id,
                    triggered_by,
                    expected_config_revision,
                )
            )
        except RuntimeSnapshotPublicationError as error:
            # The activation committed; only the synchronous runtime refresh failed.
            committed = dict(error.committed_result)
            committed["warning"] = ACTIVATION_RUNTIME_REFRESH_FAILED
            committed["runtime_ready"] = False
            logger.error(
                "Activation committed for %s/%s but runtime snapshot refresh failed",
                location,
                cluster,
            )
        except _TransitionRejected as rejected:
            return self._failure(
                location,
                cluster,
                await self._read_current_identity(location, cluster),
                rejected.message,
                error_code=rejected.error_code,
            )
        except InvalidProfileIdentityError as error:
            return self._failure(
                location,
                cluster,
                await self._read_current_identity(location, cluster),
                str(error),
                error_code="invalid_profile_identity",
            )
        except ProfileNotFoundError as error:
            return self._failure(
                location,
                cluster,
                await self._read_current_identity(location, cluster),
                f"Requested profile does not exist: {error}",
                error_code="profile_not_found",
            )
        except Exception as error:  # noqa: BLE001 - existing failure semantics
            logger.error(f"Mode transition failed for {location}/{cluster}: {error}")
            return self._failure(
                location,
                cluster,
                await self._read_current_identity(location, cluster),
                f"Mode transition failed: {str(error)}",
            )

        # Pending snapshot built pre-commit carries the next version; the
        # installed snapshot after publication is the one to report.
        runtime_ready = committed.get("runtime_ready", True)
        installed_version = (
            self._runtime_device_registry.snapshot.version if runtime_ready else None
        )
        mode_changed = bool(committed.get("mode_changed"))
        try:
            if self._scheduler is not None:
                if mode_changed:
                    self._scheduler.clear_room_light_ramps(location, cluster)
                merged = await self._schedule_service.merged_scheduler_schedules()
                self._scheduler.update_schedules(merged)
        except Exception as error:
            runtime_ready = False
            committed["warning"] = ACTIVATION_RUNTIME_REFRESH_FAILED
            logger.error("Activation committed but scheduler refresh failed: %s", error)

        await self._log_cluster_desync(location, cluster, new_mode_id)

        await self._publish_mode_changed_event(
            location,
            cluster,
            committed,
            installed_version,
            runtime_ready,
        )

        logger.info(
            "Successfully transitioned %s/%s from %s/%s to %s/%s",
            location,
            cluster,
            (committed.get("old_mode") or {}).get("mode_name")
            if committed.get("old_mode")
            else None,
            (committed.get("old_mode") or {}).get("submode_name")
            if committed.get("old_mode")
            else None,
            committed.get("identity", {}).get("mode_name"),
            committed.get("identity", {}).get("submode_name") or "None",
        )
        return {
            **committed,
            "runtime_ready": runtime_ready,
            "warning": committed.get("warning"),
        }

    async def _activate(
        self,
        connection: Connection,
        location: str,
        cluster: str,
        new_mode_id: int,
        new_submode_id: int | None,
        triggered_by: str,
        expected_config_revision: str | None,
    ) -> dict[str, Any]:
        """Run every pre-commit activation step on one connection."""
        await connection.execute("SELECT pg_advisory_xact_lock($1)", 7_281_992)
        await connection.fetchrow(
            "SELECT mode_id, submode_id FROM room_active_mode "
            "WHERE location = $1 AND cluster = $2 FOR UPDATE",
            location,
            cluster,
        )
        prior = await self._room_mode_repo.get_active_mode(location, cluster, conn=connection)
        old_mode_id = prior["mode_id"] if prior is not None else None

        if expected_config_revision is not None:
            version_id = await connection.fetchval(
                "SELECT COALESCE(MAX(version_id), 0) FROM config_versions"
            )
            current_revision = f"{int(version_id or 0):07x}"
            if current_revision != expected_config_revision:
                raise _TransitionRejected(
                    "configuration revision changed: expected "
                    f"{expected_config_revision}, current {current_revision}",
                    ACTIVATION_REVISION_CONFLICT,
                )

        written = await self._room_mode_repo.set_mode_on_connection(
            connection, location, cluster, new_mode_id, new_submode_id
        )
        identity = written
        parameters_row = await connection.fetchrow(
            "SELECT * FROM mode_parameters WHERE location = $1 AND cluster = $2 "
            "AND mode_id = $3 AND submode_id IS NOT DISTINCT FROM $4",
            location,
            cluster,
            new_mode_id,
            new_submode_id,
        )
        if parameters_row is None:
            raise _TransitionRejected(
                "Selected profile has no saved parameters", "profile_not_configured"
            )
        parameters = dict(parameters_row)
        mode_changed = old_mode_id != new_mode_id
        if mode_changed:
            counts = await self._schedule_service.sync_on_connection(
                connection,
                location,
                cluster,
                new_mode_id,
                new_submode_id,
                parameters=parameters,
            )
            schedule_sync_result: dict[str, Any] = dict(counts)
        else:
            schedule_sync_result = {"skipped": True, "reason": "submode_only_transition"}
            logger.info(
                "Skipping schedule synchronization for %s/%s: mode_id unchanged (%s), "
                "submode-only transition",
                location,
                cluster,
                new_mode_id,
            )

        # mode_transition_history id columns are INTEGER in production (asyncpg rejects str).
        # Human-readable names live in parameters_synced.
        params_sync: dict[str, Any] = {
            "old_mode_name": (prior or {}).get("mode_name"),
            "old_submode_name": (prior or {}).get("submode_name"),
            "new_mode_name": identity["mode_name"],
            "new_submode_name": identity["submode_name"],
            "schedule_sync": schedule_sync_result,
        }
        await connection.execute(
            """
            INSERT INTO mode_transition_history (
                location, cluster,
                old_mode_id, old_submode_id,
                new_mode_id, new_submode_id,
                triggered_by,
                parameters_synced,
                success
            ) VALUES (
                $1, $2,
                $3, $4,
                $5, $6,
                $7,
                $8::jsonb,
                true
            )
            """,
            location,
            cluster,
            old_mode_id,
            int(prior["submode_id"]) if prior and prior.get("submode_id") is not None else None,
            int(new_mode_id),
            int(new_submode_id) if new_submode_id is not None else None,
            triggered_by,
            json.dumps(params_sync),
        )

        version_id = await self._config_repo.log_config_version(
            config_type="room_mode",
            author="system",
            comment=f"Activated {identity['mode_name']}/{identity['submode_name'] or 'base'} "
            f"for {location}/{cluster}",
            location=location,
            cluster=cluster,
            changes={
                "old_mode_id": old_mode_id,
                "old_submode_id": int(prior["submode_id"])
                if prior and prior.get("submode_id") is not None
                else None,
                "new_mode_id": int(new_mode_id),
                "new_submode_id": int(new_submode_id) if new_submode_id is not None else None,
            },
            conn=connection,
        )
        if version_id is None:
            raise _TransitionRejected("Failed to log activation config revision")

        return {
            "success": True,
            "location": location,
            "cluster": cluster,
            "old_mode": dict(prior) if prior else None,
            "new_mode": dict(written),
            "schedule_sync_result": schedule_sync_result,
            "message": (
                f"Successfully transitioned to {identity['mode_name']}/"
                f"{identity['submode_name'] or 'None'}"
            ),
            "config_revision": f"{int(version_id):07x}",
            "identity": dict(identity),
            "parameters": parameters,
            "mode_changed": mode_changed,
        }

    async def _read_current_identity(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Best-effort active identity for failure responses."""
        if self._db is None:
            return None
        try:
            return await self._room_mode_repo.get_active_mode(location, cluster)
        except Exception:  # noqa: BLE001 - failure diagnostics only
            return None

    def _failure(
        self,
        location: str,
        cluster: str,
        old_mode: dict[str, Any] | None,
        message: str,
        *,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        result = ModeTransitionResult(
            success=False,
            location=location,
            cluster=cluster,
            old_mode=old_mode,
            new_mode=None,
            schedule_sync_result=None,
            message=message,
            runtime_ready=False,
            error_code=error_code,
        )
        logger.error(f"Mode transition failed for {location}/{cluster}: {message}")
        return result.to_dict()

    async def _log_cluster_desync(self, location: str, cluster: str, new_mode_id: int) -> None:
        """Warn when sibling clusters of one location disagree with the new mode."""
        try:
            pool = await self._db._get_pool()
            async with pool.acquire() as conn:
                query = """
                    SELECT cluster, mode_id FROM room_active_mode
                    WHERE location = $1 AND cluster != $2
                """
                other_clusters = await conn.fetch(query, location, cluster)
                for row in other_clusters:
                    if row["mode_id"] != new_mode_id:
                        logger.warning(
                            f"Mode desync: {location}/{cluster} -> {new_mode_id}, "
                            f"but {row['cluster']} is in {row['mode_id']}"
                        )
        except Exception as error:  # noqa: BLE001 - diagnostics only
            logger.info(f"Could not check cluster sync: {error}")

    async def _publish_mode_changed_event(
        self,
        location: str,
        cluster: str,
        committed: dict[str, Any],
        installed_version: int | None,
        runtime_ready: bool,
    ) -> None:
        """Publish the MODE_CHANGED config notification with runtime refresh proof."""
        from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus

        identity: dict[str, Any] = committed.get("identity") or {}
        try:
            await get_event_bus().publish(
                ConfigChangeEvent(
                    event_type=ConfigEventType.MODE_CHANGED,
                    location=location,
                    cluster=cluster,
                    config_type="room_mode",
                    data={
                        "old_mode_id": (committed.get("old_mode") or {}).get("mode_id"),
                        "new_mode_id": identity.get("mode_id"),
                        "old_submode_id": (committed.get("old_mode") or {}).get("submode_id"),
                        "new_submode_id": identity.get("submode_id"),
                        "config_revision": committed.get("config_revision"),
                        "runtime_snapshot_version": installed_version,
                        "runtime_refreshed": runtime_ready,
                    },
                )
            )
        except Exception as error:  # noqa: BLE001 - notification is best-effort
            logger.warning(
                f"Failed to publish mode changed event for {location}/{cluster}: {error}"
            )
