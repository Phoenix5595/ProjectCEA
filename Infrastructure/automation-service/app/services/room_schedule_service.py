"""Internal schedule derivation, validation and atomic persistence owner."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any, cast

from app.schemas.schedules import RoomScheduleCreate
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from asyncpg import Connection

    from app.config import ConfigLoader
    from app.database import DatabaseManager

logger = get_logger(__name__)

_SCHEDULE_PROTECTED_DEVICE_NAMES = frozenset({"room_schedule", "climate"})
# Shared timeline advisory lock: standalone save joins the same lock family as
# activation (ModeTransitionService._activate) and climate timeline Apply, so a
# schedule save can never interleave with an in-flight activation transaction.
_TIMELINE_ADVISORY_LOCK = 7_281_992


class ProfileNotConfiguredError(ValueError):
    """The exact activated profile has no stored parameters."""

    code = "profile_not_configured"


class ActiveModeMissingError(ValueError):
    """The room has no active mode row, so its schedules cannot be derived."""

    code = "active_mode_missing"


def _to_hhmm(value: Any, default: str = "06:00") -> str:
    if value is None:
        return default
    if hasattr(value, "hour"):
        return f"{value.hour:02d}:{value.minute:02d}"
    text = str(value).strip()
    return text[:5] if len(text) >= 5 else default


def _schedule_from_parameters(parameters: Mapping[str, Any] | None) -> RoomScheduleCreate | None:
    """Derive DAY/NIGHT bounds from exact stored profile parameters."""
    if not parameters:
        return None
    return RoomScheduleCreate(
        day_start_time=_to_hhmm(parameters.get("day_start_time"), "06:00"),
        day_end_time=_to_hhmm(parameters.get("night_start_time"), "18:00"),
        night_start_time=_to_hhmm(parameters.get("night_start_time"), "18:00"),
        night_end_time=_to_hhmm(parameters.get("day_start_time"), "06:00"),
        ramp_up_duration=parameters.get("light_ramp_up_minutes"),
        ramp_down_duration=parameters.get("light_ramp_down_minutes"),
    )


def validate_room_schedule_times(schedule: RoomScheduleCreate) -> None:
    """Raise ValueError on malformed bounds before any persistence happens."""
    for field in (
        schedule.day_start_time,
        schedule.day_end_time,
        schedule.night_start_time,
        schedule.night_end_time,
    ):
        try:
            parts = field.split(":")
            dt_time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError) as error:
            raise ValueError(f"Invalid time format. Use HH:MM format. Error: {error}") from error
    if schedule.ramp_up_duration is not None and schedule.ramp_up_duration < 0:
        raise ValueError("ramp_up_duration must be >= 0")
    if schedule.ramp_down_duration is not None and schedule.ramp_down_duration < 0:
        raise ValueError("ramp_down_duration must be >= 0")
    if schedule.day_end_time != schedule.night_start_time:
        raise ValueError(
            f"day_end_time ({schedule.day_end_time}) must equal night_start_time "
            f"({schedule.night_start_time})"
        )
    if schedule.day_start_time != schedule.night_end_time:
        raise ValueError(
            f"day_start_time ({schedule.day_start_time}) must equal night_end_time "
            f"({schedule.night_end_time})"
        )


class RoomScheduleService:
    """Own every room-schedule mutation in one transaction-aware place."""

    def __init__(self, database: DatabaseManager, config: ConfigLoader) -> None:
        self._database = database
        self._config = config

    async def get_room_devices(self, location: str, cluster: str) -> dict[str, Any]:
        """Return the current configured device hierarchy for one room.

        The config hierarchy may be immutable (recursive MappingProxy), so only
        the mapping protocol is used; no dict-only child is required.
        """
        devices = await self._config.get_devices()
        room = (devices or {}).get(location) or {}
        return dict(room.get(cluster) or {})

    async def get_all_room_devices(self) -> dict[str, Any]:
        """Return the whole configured device hierarchy for sync-all."""
        devices = await self._config.get_devices()
        return dict(devices or {})

    async def merged_scheduler_schedules(self) -> list[dict[str, Any]]:
        """Load committed non-light schedule rows merged against the owned config."""
        from app.control.schedule_merge import merge_schedules_with_config

        db_schedules = await self._database.schedule_repo.get_schedules()
        return await merge_schedules_with_config(db_schedules, self._config)

    async def replace_on_connection(
        self, connection: Connection, location: str, cluster: str, schedule: RoomScheduleCreate
    ) -> dict[str, int]:
        """Replace non-light DAY/NIGHT rows on a caller-owned open transaction.

        Reads and deletes use the supplied connection only; ``room_schedule`` and
        ``climate`` rows are preserved from deletion. An empty device hierarchy
        safely creates zero rows.
        """
        validate_room_schedule_times(schedule)
        devices = await self.get_room_devices(location, cluster)
        unscoped = await self._database.schedule_repo.get_schedules(
            location, cluster, conn=connection
        )
        deletable = [
            row["id"]
            for row in unscoped
            if row.get("id") and row.get("device_name") not in _SCHEDULE_PROTECTED_DEVICE_NAMES
        ]
        if deletable:
            deleted = await self._database.schedule_repo.delete_schedules_bulk(
                deletable, connection
            )
            if deleted != len(deletable):
                raise RuntimeError(
                    f"Failed to delete {len(deletable)} existing schedules "
                    f"for {location}/{cluster}: deleted {deleted}"
                )
        created = 0
        for device_name, info in devices.items():
            device_type = info.get("device_type", "")
            if device_type == "light":
                continue
            display_name = info.get("display_name", device_name)
            for mode, start, end in (
                ("DAY", schedule.day_start_time, schedule.day_end_time),
                ("NIGHT", schedule.night_start_time, schedule.night_end_time),
            ):
                schedule_id = await self._database.schedule_repo.create_schedule(
                    name=f"{display_name} - {mode.title()}",
                    location=location,
                    cluster=cluster,
                    device_name=device_name,
                    start_time=start,
                    end_time=end,
                    day_of_week=None,
                    enabled=True,
                    mode=mode,
                    target_intensity=None,
                    ramp_up_duration=None,
                    ramp_down_duration=None,
                    conn=cast("Any", connection),
                )
                if not schedule_id:
                    raise RuntimeError(f"Failed to create {mode} schedule for {device_name}")
                created += 1
        return {"schedules_created": created, "devices_configured": len(devices)}

    async def read_profile_parameters_on_connection(
        self,
        connection: Connection,
        location: str,
        cluster: str,
        mode_id: int,
        submode_id: int | None,
    ) -> dict[str, Any]:
        """Read one exact activated profile's parameters on the caller's connection.

        Identity validation is delegated to the shared repository helper; the
        parameter row is then selected exactly by numeric profile IDs with a
        NULL-safe submode predicate. Missing parameters raise
        :class:`ProfileNotConfiguredError` before any caller commits.
        """
        identity = await self._database.room_mode_repo.get_profile_identity_on_connection(
            connection, location, mode_id, submode_id
        )
        row = await connection.fetchrow(
            "SELECT * FROM mode_parameters WHERE location = $1 AND cluster = $2 "
            "AND mode_id = $3 AND submode_id IS NOT DISTINCT FROM $4",
            location,
            cluster,
            int(mode_id),
            submode_id,
        )
        if row is None:
            mode_name = str(identity["mode_name"])
            submode_name = identity.get("submode_name")
            suffix = f"/{submode_name}" if submode_name else ""
            raise ProfileNotConfiguredError(
                f"No mode parameters for {location}/{cluster} mode={mode_name}{suffix}"
            )
        return dict(row)

    async def sync_on_connection(
        self,
        connection: Connection,
        location: str,
        cluster: str,
        mode_id: int,
        submode_id: int | None,
        *,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, int]:
        """Synchronize the exact activated profile's schedules on the activation transaction.

        ``parameters`` optionally reuses the exact profile parameters the
        activation owner already read on this same connection, avoiding a second
        read; by default the exact read happens here. Missing parameters raise
        before any schedule mutation. It does not save parameters again and
        publishes no events.
        """
        if parameters is None:
            parameters = await self.read_profile_parameters_on_connection(
                connection, location, cluster, mode_id, submode_id
            )
        schedule = _schedule_from_parameters(parameters)
        if schedule is None:
            raise ProfileNotConfiguredError(
                f"profile {mode_id}/{submode_id} has incomplete photoperiod bounds"
            )
        return await self.replace_on_connection(connection, location, cluster, schedule)

    async def save(
        self, location: str, cluster: str, schedule: RoomScheduleCreate
    ) -> dict[str, Any]:
        """Own the ordinary room-schedule replacement transaction and post-commit notices.

        The transaction takes the shared timeline advisory lock and the active
        row lock before it reads or merges active parameters, writes one
        ``room_schedule`` config revision, and returns a truthful post-commit
        warning instead of ever implying a rollback.
        """
        validate_room_schedule_times(schedule)
        return await self._persist(location, cluster, schedule)

    async def sync_one(self, location: str, cluster: str) -> dict[str, Any]:
        """Derive the active profile's bounds inside the save transaction and persist them."""
        return await self._persist(location, cluster, None)

    async def sync_all(self) -> list[dict[str, Any]]:
        """Synchronize every configured room; one raw outcome per room cluster."""
        devices = await self.get_all_room_devices()
        results: list[dict[str, Any]] = []
        for location, clusters in (devices or {}).items():
            if not isinstance(clusters, Mapping):
                continue
            for cluster in clusters:
                try:
                    outcome = await self.sync_one(location, cluster)
                except Exception as error:  # noqa: BLE001 - per-room isolation
                    logger.warning(f"Room schedule sync failed for {location}/{cluster}: {error}")
                    results.append(
                        {
                            "location": location,
                            "cluster": cluster,
                            "success": False,
                            "error": str(error),
                        }
                    )
                else:
                    results.append(
                        {
                            "location": location,
                            "cluster": cluster,
                            "success": True,
                            "schedules_created": outcome.get("schedules_created", 0),
                            "devices_configured": outcome.get("devices_configured", 0),
                            "schedule": outcome.get("schedule"),
                            "prior_parameters": outcome.get("prior_parameters"),
                            "warning": outcome.get("warning"),
                        }
                    )
        return results

    async def _persist(
        self, location: str, cluster: str, schedule: RoomScheduleCreate | None
    ) -> dict[str, Any]:
        """Commit one schedule aggregate: schedules, active parameters and one revision.

        ``schedule=None`` derives the bounds from the active profile's exact
        stored parameters on this transaction (internal sync-one/all entry).
        """
        pool = await self._database._get_pool()
        prior_parameters: dict[str, Any] | None = None
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock($1)", _TIMELINE_ADVISORY_LOCK)
            await connection.fetchrow(
                "SELECT mode_id, submode_id FROM room_active_mode "
                "WHERE location = $1 AND cluster = $2 FOR UPDATE",
                location,
                cluster,
            )
            active = await self._database.room_mode_repo.get_active_mode(
                location, cluster, conn=connection
            )
            mode_name = str(active.get("mode_name", "veg")) if active else "veg"
            submode_name = active.get("submode_name") if active else None
            prior_parameters = await self._database.room_mode_repo.get_mode_parameters(
                location, cluster, mode_name, submode_name, conn=connection
            )
            if schedule is None:
                if not active:
                    raise ActiveModeMissingError(
                        f"No active mode for {location}/{cluster}. Set mode first."
                    )
                if not prior_parameters:
                    raise ProfileNotConfiguredError(
                        f"No mode parameters for {location}/{cluster} mode={mode_name}"
                    )
                schedule = _schedule_from_parameters(prior_parameters)
                if schedule is None:  # unreachable: parameters are nonempty here
                    raise ProfileNotConfiguredError(
                        f"profile {mode_name} has incomplete photoperiod bounds"
                    )
            counts = await self.replace_on_connection(connection, location, cluster, schedule)
            merged_params: dict[str, Any] = {
                **(prior_parameters or {}),
                "day_start_time": schedule.day_start_time,
                "night_start_time": schedule.night_start_time,
                "light_ramp_up_minutes": schedule.ramp_up_duration or 30,
                "light_ramp_down_minutes": schedule.ramp_down_duration or 15,
            }
            saved = await self._database.room_mode_repo.save_mode_parameters(
                location, cluster, mode_name, submode_name, merged_params, conn=connection
            )
            if not saved:
                raise RuntimeError("Failed to save mode parameters")
            version_id = await self._database.config_repo.log_config_version(
                config_type="room_schedule",
                author="system",
                comment=f"Room schedule updated for {location}/{cluster}",
                location=location,
                cluster=cluster,
                changes={
                    "day_start_time": schedule.day_start_time,
                    "day_end_time": schedule.day_end_time,
                    "night_start_time": schedule.night_start_time,
                    "night_end_time": schedule.night_end_time,
                    "ramp_up_duration": schedule.ramp_up_duration,
                    "ramp_down_duration": schedule.ramp_down_duration,
                    "schedules_created": counts["schedules_created"],
                    "devices_configured": counts["devices_configured"],
                },
                conn=connection,
            )
            if version_id is None:
                raise RuntimeError("Failed to log room schedule config version")
        warning = await self._publish_after_commit(location, cluster, schedule, counts)
        return {
            **counts,
            "config_version_id": version_id,
            "prior_parameters": prior_parameters,
            "schedule": schedule,
            "warning": warning,
        }

    async def _publish_after_commit(
        self, location: str, cluster: str, schedule: RoomScheduleCreate, counts: dict[str, int]
    ) -> str | None:
        """Clear schedule caches before emitting the committed notification.

        Post-commit failures never imply a rollback: the persisted schedule and
        revision stay committed, each failure is logged once and returned as a
        truthful warning. There is no automatic write retry.
        """
        from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus
        from app.state import get_state_manager

        failures: list[str] = []
        state = get_state_manager()
        for key in (
            f"schedules:loc:{location}:cluster:{cluster}",
            f"schedules:loc:{location}:cluster:{cluster}:climate",
            "schedules:all",
        ):
            try:
                _ = await state.delete(key)
            except Exception as error:  # noqa: BLE001 - post-commit diagnostics
                logger.warning(f"Failed to clear schedule cache {key}: {error}")
                failures.append("schedule_cache_clear_failed")
        try:
            await get_event_bus().publish(
                ConfigChangeEvent(
                    event_type=ConfigEventType.MODE_CHANGED,
                    location=location,
                    cluster=cluster,
                    config_type="mode_parameters",
                    data={
                        "action": "room_schedule_saved",
                        "schedules_created": counts.get("schedules_created", 0),
                        "day_start_time": schedule.day_start_time,
                        "night_start_time": schedule.night_start_time,
                        "ramp_up_duration": schedule.ramp_up_duration,
                        "ramp_down_duration": schedule.ramp_down_duration,
                    },
                )
            )
        except Exception as error:  # noqa: BLE001 - post-commit diagnostics
            logger.warning(f"Failed to publish room schedule saved event: {error}")
            failures.append("room_schedule_notification_failed")
        if failures:
            return "; ".join(dict.fromkeys(failures))
        return None
