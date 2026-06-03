from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from app.database import DatabaseManager
from app.repositories.room_modes import RoomModeRepository
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from asyncpg import Connection


logger = get_logger(__name__)


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
    ):
        self.success = success
        self.location = location
        self.cluster = cluster
        self.old_mode = old_mode
        self.new_mode = new_mode
        self.schedule_sync_result = schedule_sync_result
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "location": self.location,
            "cluster": self.cluster,
            "old_mode": self.old_mode,
            "new_mode": self.new_mode,
            "schedule_sync_result": self.schedule_sync_result,
            "message": self.message,
        }


class ModeTransitionService:
    """Service for handling mode transitions with proper transaction management."""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.room_mode_repo = RoomModeRepository(db.pool)

    async def execute_mode_transition(
        self,
        location: str,
        cluster: str,
        new_mode_id: int,
        new_submode_id: int | None,
        triggered_by: str,
    ) -> dict[str, Any]:
        logger.info(
            f"Starting mode transition for {location}/{cluster} to mode_id={new_mode_id}, "
            f"submode_id={new_submode_id} (triggered by {triggered_by})"
        )
        current_mode = None
        try:
            current_mode = await self.room_mode_repo.get_active_mode(location, cluster)

            new_mode_name = ""
            new_submode_name = None
            schedule_sync_result = None

            old_mode_id: int | None = None
            if current_mode and current_mode.get("mode_id") is not None:
                try:
                    old_mode_id = int(current_mode["mode_id"])
                except (TypeError, ValueError):
                    old_mode_id = None
            # Flower bulk ↔ late, etc.: same room_modes.id — do not rewrite schedules from submode-specific params.
            submode_only_transition = old_mode_id is not None and int(new_mode_id) == old_mode_id

            pool = self.db.pool
            if not pool:
                raise RuntimeError("Database pool not initialized")

            async with pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                async with conn.transaction():
                    new_mode_row = await conn.fetchrow(
                        "SELECT name FROM room_modes WHERE id = $1", new_mode_id
                    )
                    if not new_mode_row:
                        return ModeTransitionResult(
                            success=False,
                            location=location,
                            cluster=cluster,
                            old_mode=current_mode,
                            new_mode=None,
                            schedule_sync_result=None,
                            message=f"Mode ID {new_mode_id} not found",
                        ).to_dict()

                    new_mode_name = new_mode_row["name"]
                    if new_submode_id:
                        submode_row = await conn.fetchrow(
                            "SELECT name FROM flower_submodes WHERE id = $1", new_submode_id
                        )
                        if submode_row:
                            new_submode_name = submode_row["name"]

                    mode_result = await self.room_mode_repo.set_mode_with_transaction(
                        location, cluster, new_mode_name, new_submode_name
                    )

                    if not mode_result:
                        return ModeTransitionResult(
                            success=False,
                            location=location,
                            cluster=cluster,
                            old_mode=current_mode,
                            new_mode=None,
                            schedule_sync_result=None,
                            message="Failed to set active mode",
                        ).to_dict()

                    from app.routes.schedules.room import sync_room_schedule_from_mode_parameters

                    if submode_only_transition:
                        schedule_sync_result = {
                            "skipped": True,
                            "reason": "submode_only_transition",
                        }
                        logger.info(
                            "Skipping sync_room_schedule_from_mode_parameters for %s/%s: "
                            "mode_id unchanged (%s), submode-only transition",
                            location,
                            cluster,
                            new_mode_id,
                        )
                    else:
                        try:
                            schedule_sync_result = await sync_room_schedule_from_mode_parameters(
                                location, cluster
                            )
                        except Exception as e:
                            schedule_sync_result = {"error": str(e)}

                    # mode_transition_history id columns are INTEGER in production (asyncpg rejects str).
                    # Human-readable names live in parameters_synced.
                    params_sync: dict[str, Any] = {
                        "old_mode_name": current_mode["mode_name"] if current_mode else None,
                        "old_submode_name": current_mode.get("submode_name")
                        if current_mode
                        else None,
                        "new_mode_name": new_mode_name,
                        "new_submode_name": new_submode_name,
                        "schedule_sync": schedule_sync_result,
                    }
                    await conn.execute(
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
                        int(current_mode["mode_id"])
                        if current_mode and current_mode.get("mode_id") is not None
                        else None,
                        int(current_mode["submode_id"])
                        if current_mode and current_mode.get("submode_id") is not None
                        else None,
                        int(new_mode_id),
                        int(new_submode_id) if new_submode_id is not None else None,
                        triggered_by,
                        json.dumps(params_sync),
                    )

                # Check for multi-cluster desync
                try:
                    query = """
                        SELECT cluster, mode_id FROM room_active_mode
                        WHERE location = $1 AND cluster != $2
                    """
                    other_clusters = await conn.fetch(query, location, cluster)
                    for row in other_clusters:
                        if row["mode_id"] != new_mode_id:
                            logger.warning(
                                f"Mode desync: {location}/{cluster} -> {new_mode_id}, but {row['cluster']} is in {row['mode_id']}"
                            )
                except Exception as e:
                    logger.info(f"Could not check cluster sync: {e}")

            try:
                await self._trigger_scheduler_refresh(location, cluster)
                if not submode_only_transition:
                    self._clear_light_ramp_state(location, cluster)
            except Exception as e:
                logger.error(f"Failed to refresh scheduler after mode transition: {e}")

            final_mode = await self.room_mode_repo.get_active_mode(location, cluster)

            logger.info(
                f"Successfully transitioned {location}/{cluster} from "
                f"{current_mode['mode_name'] if current_mode else 'None'}/{current_mode.get('submode_name') if current_mode else 'None'} "
                f"to {new_mode_name}/{new_submode_name or 'None'}"
            )

            return ModeTransitionResult(
                success=True,
                location=location,
                cluster=cluster,
                old_mode=current_mode,
                new_mode=final_mode,
                schedule_sync_result=schedule_sync_result,
                message=f"Successfully transitioned to {new_mode_name}/{new_submode_name or 'None'}",
            ).to_dict()

        except Exception as e:
            logger.error(f"Mode transition failed for {location}/{cluster}: {e}")

            return ModeTransitionResult(
                success=False,
                location=location,
                cluster=cluster,
                old_mode=current_mode,
                new_mode=None,
                schedule_sync_result=None,
                message=f"Mode transition failed: {str(e)}",
            ).to_dict()

    async def _trigger_scheduler_refresh(self, location: str, cluster: str):
        """Trigger scheduler to refresh schedules from database."""
        try:
            from app.control.schedule_merge import merge_schedules_with_config
            from app.main import container

            control_engine = container.get_control_engine()
            cfg = container.get_config()
            db_schedules = await self.db.schedule_repo.get_schedules()
            merged = merge_schedules_with_config(db_schedules, cfg)
            control_engine.scheduler.update_schedules(merged)
            logger.info(f"Synchronously refreshed scheduler for {location}/{cluster}")
        except Exception as e:
            logger.error(f"Failed to trigger scheduler refresh: {e}")

    def _clear_light_ramp_state(self, location: str, cluster: str):
        """Clear light ramp state for specific location/cluster."""
        try:
            from app.main import container

            control_engine = container.get_control_engine()
            scheduler = control_engine.scheduler
            keys_to_delete = [
                key
                for key in scheduler._light_ramp_state
                if key[0] == location and key[1] == cluster
            ]
            for key in keys_to_delete:
                del scheduler._light_ramp_state[key]
            if keys_to_delete:
                logger.info(
                    f"Cleared {len(keys_to_delete)} light ramp state entries for {location}/{cluster}"
                )
        except Exception as e:
            logger.error(f"Failed to clear light ramp state: {e}")
