from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from shared.logging import get_logger

from ..database import DatabaseManager
from ..repositories.mode_sync import sync_climate_setpoints_from_mode_parameters
from ..repositories.room_modes import RoomModeRepository

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
        climate_sync_result: dict[str, Any] | None,
        schedule_sync_result: dict[str, Any] | None,
        message: str = "",
    ):
        self.success = success
        self.location = location
        self.cluster = cluster
        self.old_mode = old_mode
        self.new_mode = new_mode
        self.climate_sync_result = climate_sync_result
        self.schedule_sync_result = schedule_sync_result
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "location": self.location,
            "cluster": self.cluster,
            "old_mode": self.old_mode,
            "new_mode": self.new_mode,
            "climate_sync_result": self.climate_sync_result,
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
            climate_sync_result = None
            schedule_sync_result = None

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
                            climate_sync_result=None,
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
                            climate_sync_result=None,
                            schedule_sync_result=None,
                            message="Failed to set active mode",
                        ).to_dict()

                    climate_sync_result = await sync_climate_setpoints_from_mode_parameters(
                        conn, location, cluster, new_mode_id, new_submode_id
                    )

                    from ..routes.schedules.room import sync_room_schedule_from_mode_parameters

                    try:
                        schedule_sync_result = await sync_room_schedule_from_mode_parameters(
                            location, cluster
                        )
                    except Exception as e:
                        schedule_sync_result = {"error": str(e)}

                    await conn.execute(
                        """
                        INSERT INTO mode_transition_history (
                            location, cluster,
                            old_mode_id, old_submode_id,
                            new_mode_id, new_submode_id,
                            old_mode_name, old_submode_name,
                            new_mode_name, new_submode_name,
                            triggered_by, transition_at
                        ) VALUES (
                            $1, $2,
                            $3, $4,
                            $5, $6,
                            $7, $8,
                            $9, $10,
                            $11, NOW()
                        )
                        """,
                        location,
                        cluster,
                        current_mode["mode_id"] if current_mode else None,
                        current_mode.get("submode_id") if current_mode else None,
                        new_mode_id,
                        new_submode_id,
                        current_mode["mode_name"] if current_mode else None,
                        current_mode.get("submode_name") if current_mode else None,
                        new_mode_name,
                        new_submode_name,
                        triggered_by,
                    )

                    self.db.setpoint_repo.invalidate_cache_for_location_cluster(location, cluster)

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
                climate_sync_result=climate_sync_result,
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
                climate_sync_result=None,
                schedule_sync_result=None,
                message=f"Mode transition failed: {str(e)}",
            ).to_dict()

    async def _trigger_scheduler_refresh(self, location: str, cluster: str):
        """Trigger scheduler to refresh schedules from database."""
        try:
            from ..main import container

            control_engine = container.get_control_engine()
            db_schedules = await self.db.schedule_repo.get_schedules()
            control_engine.scheduler.update_schedules(db_schedules)
            logger.info(f"Synchronously refreshed scheduler for {location}/{cluster}")
        except Exception as e:
            logger.error(f"Failed to trigger scheduler refresh: {e}")

    def _clear_light_ramp_state(self, location: str, cluster: str):
        """Clear light ramp state for specific location/cluster."""
        try:
            from ..main import container

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
