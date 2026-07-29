from __future__ import annotations

from typing import Any

import pytest

from app.repositories.light_programs import validate_program_target_intensity
from app.repositories.light_target_intensity import validate_normal_target_intensity
from app.services import schedule_auto_create


class _TargetRepository:
    def __init__(self) -> None:
        self.rows: list[tuple[int, int, float]] = []

    async def set_intensity(self, device_id: int, mode_id: int, target_intensity: float) -> bool:
        self.rows.append((device_id, mode_id, target_intensity))
        return True


class _ModeRepository:
    def __init__(self, global_mode_ids: list[int], room_mode_ids: list[int]) -> None:
        self._global_mode_ids = global_mode_ids
        self._room_mode_ids = room_mode_ids

    async def get_room_modes(self) -> list[dict[str, int]]:
        return [{"id": mode_id} for mode_id in self._global_mode_ids]

    async def get_mode_ids_for_room_cluster(self, _location: str, _cluster: str) -> list[int]:
        return self._room_mode_ids


class _Database:
    def __init__(self, global_mode_ids: list[int], room_mode_ids: list[int]) -> None:
        self.room_mode_repo = _ModeRepository(global_mode_ids, room_mode_ids)
        self.light_target_intensity_repo = _TargetRepository()


class _EventBus:
    async def publish(self, _event: object) -> None:
        return None


@pytest.mark.asyncio
async def test_default_targets_use_ten_percent_for_each_available_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a light with two available modes
    database: Any = _Database([1, 2], [1, 2])
    monkeypatch.setattr(schedule_auto_create, "get_event_bus", _EventBus)

    # When: default targets are created
    await schedule_auto_create.create_default_intensity_for_light(database, 42, "Veg Room", "main")

    # Then: each target uses the 10% default anchor
    assert database.light_target_intensity_repo.rows == [(42, 1, 10.0), (42, 2, 10.0)]


@pytest.mark.asyncio
async def test_default_targets_only_use_modes_assigned_to_light_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a global mode from another room and one matching room mode
    database: Any = _Database([1, 2], [1])
    monkeypatch.setattr(schedule_auto_create, "get_event_bus", _EventBus)

    # When: default targets are created for the light
    await schedule_auto_create.create_default_intensity_for_light(database, 42, "Veg Room", "main")

    # Then: only the matching mode receives a 10% target
    assert database.light_target_intensity_repo.rows == [(42, 1, 10.0)]


@pytest.mark.asyncio
async def test_default_target_creation_rejects_unknown_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a light assigned where no mode parameters exist
    database: Any = _Database([], [])
    monkeypatch.setattr(schedule_auto_create, "get_event_bus", _EventBus)

    # When/Then: creation aborts before writing targets
    with pytest.raises(RuntimeError, match="No mode parameters exist for Unknown/main"):
        await schedule_auto_create.create_default_intensity_for_light(
            database, 42, "Unknown", "main"
        )
    assert database.light_target_intensity_repo.rows == []


@pytest.mark.parametrize("target_intensity", [10.0, 100.0])
def test_normal_target_intensity_accepts_policy_boundaries(target_intensity: float) -> None:
    # Given: a normal target at either valid policy boundary
    # When: the target is validated
    # Then: it is accepted unchanged
    assert validate_normal_target_intensity(target_intensity) == target_intensity


def test_normal_target_intensity_rejects_values_below_ten_percent() -> None:
    # Given: a normal target below the safety minimum
    # When/Then: validation rejects it with a clear policy error
    with pytest.raises(ValueError, match="between 10.0 and 100.0"):
        validate_normal_target_intensity(9.9)


def test_supplemental_program_rejects_zero_target_intensity() -> None:
    # Given: a supplemental program target at zero
    # When/Then: validation rejects it because supplemental light must emit
    with pytest.raises(ValueError, match="Supplemental program target intensity"):
        validate_program_target_intensity("supplemental", 0.0)


def test_override_program_accepts_zero_target_intensity() -> None:
    # Given: an override program target at zero
    # When: the override target is validated
    # Then: it is retained for an intentional sun-period override
    assert validate_program_target_intensity("override", 0.0) == 0.0
