"""Atomic runtime device-registry loader and serialized mutation boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from shared.infra_logging import get_logger

from .runtime_device_snapshot import RuntimeDeviceSnapshot

if TYPE_CHECKING:
    from ..database import DatabaseManager

logger = get_logger(__name__)

SnapshotConsumer = Callable[[RuntimeDeviceSnapshot], None]
MutationResult = TypeVar("MutationResult")
Mutation = Callable[[Any], Awaitable[MutationResult]]
_REGISTRY_MUTATION_ADVISORY_LOCK = 7_281_991


class RuntimeDeviceRegistry:
    """Publishes one immutable snapshot and serializes assignment mutations.

    The process lock deliberately spans transaction begin, the PostgreSQL
    transaction advisory lock, pending-snapshot construction, commit, and the
    reference swap. Therefore a later committed write cannot publish before an
    earlier write in this process.
    """

    def __init__(self, database: DatabaseManager) -> None:
        self._database = database
        self._snapshot: RuntimeDeviceSnapshot | None = None
        self._consumers: list[SnapshotConsumer] = []
        self._next_version = 1
        self._mutation_lock = asyncio.Lock()

    @property
    def snapshot(self) -> RuntimeDeviceSnapshot:
        """Return the fully installed snapshot or fail closed before startup load."""
        if self._snapshot is None:
            raise RuntimeError("Runtime device snapshot is not installed")
        return self._snapshot

    def subscribe(self, consumer: SnapshotConsumer) -> None:
        """Register a synchronous projection consumer and install the current snapshot."""
        self._consumers.append(consumer)
        if self._snapshot is not None:
            consumer(self._snapshot)

    async def load_startup(self) -> RuntimeDeviceSnapshot:
        """Load the initial complete projection."""
        async with self._mutation_lock:
            snapshot = await self._build_snapshot()
            return self._install(snapshot, "startup")

    async def reload_after_commit(self) -> RuntimeDeviceSnapshot:
        """Reload externally committed registry-related projections safely."""
        async with self._mutation_lock:
            snapshot = await self._build_snapshot()
            return self._install(snapshot, "external committed mutation")

    async def mutate(self, mutation: Mutation[MutationResult]) -> MutationResult:
        """Run one registry mutation and publish its pending snapshot only after commit."""
        async with self._mutation_lock:
            pool = await self._database._get_pool()
            async with pool.acquire() as connection:
                async with connection.transaction():
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock($1)", _REGISTRY_MUTATION_ADVISORY_LOCK
                    )
                    result = await mutation(connection)
                    pending_snapshot = await self._build_snapshot(connection)

            await self._after_commit_before_install(pending_snapshot)
            self._install(pending_snapshot, "committed registry mutation")
            return result

    async def _after_commit_before_install(self, snapshot: RuntimeDeviceSnapshot) -> None:
        """Test seam for proving the process lock covers commit-to-publication ordering."""
        del snapshot

    def _install(self, snapshot: RuntimeDeviceSnapshot, reason: str) -> RuntimeDeviceSnapshot:
        """Synchronously replace the installed reference after a complete projection exists."""
        for consumer in self._consumers:
            consumer(snapshot)
        self._snapshot = snapshot
        self._next_version += 1
        logger.info("Installed runtime device snapshot version=%s (%s)", snapshot.version, reason)
        return snapshot

    async def _build_snapshot(self, connection: Any | None = None) -> RuntimeDeviceSnapshot:
        """Build every projection, optionally from the still-uncommitted connection."""
        if connection is None:
            hierarchy = await self._database.device_repo.get_all_as_hierarchy()
            mode_parameters = await self._load_active_mode_parameters(hierarchy)
            light_intensities = (
                await self._database.light_target_intensity_repo.get_all_intensities()
            )
            light_programs = await self._database.light_programs_repo.get_all_programs()
        else:
            hierarchy = await self._load_hierarchy_on_connection(connection)
            mode_parameters = await self._load_active_mode_parameters_on_connection(
                connection, hierarchy
            )
            light_intensities = await self._load_light_intensities_on_connection(connection)
            light_programs = await self._load_light_programs_on_connection(connection)
        return RuntimeDeviceSnapshot.create(
            version=self._next_version,
            hierarchy=hierarchy,
            mode_parameters=mode_parameters,
            light_intensities=light_intensities,
            light_programs=light_programs,
        )

    async def _load_hierarchy_on_connection(
        self, connection: Any
    ) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        """Load the registry hierarchy through the transaction connection."""
        rows = await connection.fetch(
            """SELECT device_id, location, cluster, device_name, display_name, device_type,
                      channel, dimming_enabled, dimming_type, dimming_board_id,
                      dimming_channel, safety_level, pid_enabled, interlock_with,
                      pid_setpoints, per_room_index, created_at, updated_at
               FROM device_registry ORDER BY location, cluster, device_name"""
        )
        hierarchy: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        for row in rows:
            device = dict(row)
            hierarchy.setdefault(device["location"], {}).setdefault(device["cluster"], {})[
                device["device_name"]
            ] = device
        return hierarchy

    async def _load_active_mode_parameters_on_connection(
        self, connection: Any, hierarchy: dict[str, dict[str, dict[str, dict[str, Any]]]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Build active photoperiod projections through the mutation transaction."""
        projection: dict[tuple[str, str], dict[str, Any]] = {}
        for location, clusters in hierarchy.items():
            for cluster in clusters:
                active_mode = await connection.fetchrow(
                    """SELECT arm.mode_id, arm.submode_id FROM room_active_mode arm
                       WHERE arm.location = $1 AND arm.cluster = $2""",
                    location,
                    cluster,
                )
                if active_mode is None:
                    continue
                parameters = await connection.fetchrow(
                    """SELECT mode_id, day_start_time, night_start_time,
                              light_ramp_up_minutes, light_ramp_down_minutes
                       FROM mode_parameters
                       WHERE location = $1 AND cluster = $2 AND mode_id = $3
                         AND COALESCE(submode_id, -1) = COALESCE($4, -1)""",
                    location,
                    cluster,
                    active_mode["mode_id"],
                    active_mode["submode_id"],
                )
                if parameters is None:
                    continue
                projection[(location, cluster)] = {
                    "mode_id": parameters["mode_id"],
                    "day_start": str(parameters["day_start_time"])[:5],
                    "night_start": str(parameters["night_start_time"])[:5],
                    "ramp_up": parameters["light_ramp_up_minutes"],
                    "ramp_down": parameters["light_ramp_down_minutes"],
                }
        return projection

    async def _load_light_intensities_on_connection(
        self, connection: Any
    ) -> dict[tuple[int, int], float]:
        """Load intensity anchors through the mutation transaction."""
        rows = await connection.fetch(
            "SELECT device_id, mode_id, target_intensity FROM light_target_intensity"
        )
        return {(row["device_id"], row["mode_id"]): float(row["target_intensity"]) for row in rows}

    async def _load_light_programs_on_connection(self, connection: Any) -> list[dict[str, Any]]:
        """Load programs through the mutation transaction in repository ordering."""
        rows = await connection.fetch(
            "SELECT * FROM light_programs WHERE enabled = TRUE ORDER BY priority DESC, created_at ASC"
        )
        return [dict(row) for row in rows]

    async def _load_active_mode_parameters(
        self, hierarchy: dict[str, dict[str, dict[str, dict[str, Any]]]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Load active photoperiod data for every room represented in the registry."""
        projection: dict[tuple[str, str], dict[str, Any]] = {}
        for location, clusters in hierarchy.items():
            for cluster in clusters:
                active_mode = await self._database.room_mode_repo.get_active_mode(location, cluster)
                if active_mode is None:
                    continue
                mode_name = active_mode.get("mode_name")
                submode_name = active_mode.get("submode_name")
                if not isinstance(mode_name, str):
                    continue
                parameters = await self._database.room_mode_repo.get_mode_parameters(
                    location, cluster, mode_name, submode_name
                )
                if parameters is None:
                    continue
                projection[(location, cluster)] = {
                    "mode_id": parameters.get("mode_id"),
                    "day_start": parameters.get("day_start_time", "06:00"),
                    "night_start": parameters.get("night_start_time", "18:00"),
                    "ramp_up": parameters.get("light_ramp_up_minutes", 0),
                    "ramp_down": parameters.get("light_ramp_down_minutes", 0),
                }
        return projection
