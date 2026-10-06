"""Behavioral proof of the exact-profile climate period cache contract.

Every assertion is driven through ``ClimatePeriodResolver.resolve_period``
with captured tick identities, in-memory fake state and a deterministic
period table — no SQL source or wall-clock timing claims.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.control.climate_resolver import ClimatePeriodResolver
from app.control.scheduler import LOCAL_TZ
from shared.redis_keys import climate_period_cache_key

_LOCATION = "Flower Room"
_CLUSTER = "main"

_MORNING = datetime(2026, 7, 2, 6, 0, tzinfo=LOCAL_TZ)
_MORNING_NEXT_MINUTE = datetime(2026, 7, 2, 6, 1, tzinfo=LOCAL_TZ)


def _period_row(
    mode_id: int,
    submode_id: int | None,
    name: str,
    heating: float,
    *,
    start_time: Any = "06:00",
    end_time: Any = "18:00",
    ramp_minutes: int = 30,
) -> dict[str, Any]:
    """One stored climate_periods row; ``start_time`` may be a time object."""
    return {
        "id": 100 + mode_id,
        "location": _LOCATION,
        "cluster": _CLUSTER,
        "mode_id": mode_id,
        "submode_id": submode_id,
        "period_name": name,
        "start_time": start_time,
        "end_time": end_time,
        "ramp_minutes": ramp_minutes,
        "heating_setpoint": heating,
        "cooling_setpoint": heating + 4.0,
        "vpd_setpoint": None,
        "co2_setpoint": None,
        "details": None,
    }


_PERIODS: dict[tuple[int, int | None], dict[str, Any]] = {
    (1, None): _period_row(1, None, "Day", 22.0),
    (2, None): _period_row(2, None, "Day", 26.0),
    (1, 4): _period_row(1, 4, "Stretch", 22.0),
    (1, 5): _period_row(1, 5, "Bulk", 24.0),
    (2, 5): _period_row(2, 5, "Bulk", 26.0),
}


def _covering_minutes(row: dict[str, Any], reference_time: str) -> bool:
    start_parts = str(row["start_time"]).split(":")
    end_parts = str(row["end_time"]).split(":")
    start = int(start_parts[0]) * 60 + int(start_parts[1])
    end = int(end_parts[0]) * 60 + int(end_parts[1])
    ref_parts = reference_time.split(":")
    ref = int(ref_parts[0]) * 60 + int(ref_parts[1])
    if start == end:
        return True
    if start < end:
        return start <= ref < end
    return ref >= start or ref < end


class _FakeClimatePeriodsRepository:
    """Deterministic exact-profile period table with query recording."""

    def __init__(self, periods: dict[tuple[int, int | None], dict[str, Any]]) -> None:
        self.periods = periods
        self.calls: list[tuple[str, str, str, int | None, int | None]] = []
        self.fail = False

    async def get_active_period(
        self,
        location: str,
        cluster: str,
        reference_time: str,
        *,
        mode_id: int | None = None,
        submode_id: int | None = None,
    ) -> dict[str, Any] | None:
        self.calls.append((location, cluster, reference_time, mode_id, submode_id))
        if self.fail:
            raise RuntimeError("database unavailable")
        row = self.periods.get((mode_id, submode_id))
        if row is None or not _covering_minutes(row, reference_time):
            return None
        return dict(row)


class _FakeScheduleRepository:
    async def get_room_light_schedule(
        self, _location: str, _cluster: str
    ) -> list[dict[str, Any]]:
        return [{"device_name": "light_f_1", "start": "06:00", "end": "18:00"}]


class _FakeDatabase:
    def __init__(
        self,
        climate_repo: _FakeClimatePeriodsRepository,
        schedule_repo: _FakeScheduleRepository,
    ) -> None:
        self.climate_periods_repo = climate_repo
        self.schedule_repo = schedule_repo


class _FakeState:
    def __init__(self) -> None:
        self.entries: dict[str, Any] = {}
        self.sets: list[tuple[str, Any, float | None]] = []
        self.deletes: list[str] = []

    async def get(self, key: str) -> Any | None:
        return self.entries.get(key)

    async def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        self.entries[key] = value
        self.sets.append((key, value, ttl))

    async def delete(self, key: str) -> bool:
        existed = key in self.entries
        self.entries.pop(key, None)
        self.deletes.append(key)
        return existed


class _StubScheduler:
    def is_in_photoperiod(self, *args: Any) -> bool:
        return True


def _resolver(
    database: _FakeDatabase, state: _FakeState | None
) -> ClimatePeriodResolver:
    return ClimatePeriodResolver(_StubScheduler(), object(), state)


def _climate_sets(state: _FakeState) -> list[tuple[str, Any, float | None]]:
    return [entry for entry in state.sets if entry[0].startswith("cache:climate_period")]


def _profile(mode_id: int, submode_id: int | None) -> dict[str, Any]:
    return {
        "location": _LOCATION,
        "cluster": _CLUSTER,
        "mode_id": mode_id,
        "submode_id": submode_id,
        "mode_name": "flower",
        "submode_name": "bulk" if submode_id == 5 else None,
    }


@pytest.mark.asyncio
async def test_resolve_period_requires_keyword_only_active_profile() -> None:
    database = _FakeDatabase(_FakeClimatePeriodsRepository(_PERIODS), _FakeScheduleRepository())
    resolver = _resolver(database, _FakeState())

    with pytest.raises(TypeError):
        await resolver.resolve_period(  # type: ignore[call-arg]
            _LOCATION, _CLUSTER, _MORNING, database
        )


@pytest.mark.asyncio
async def test_unknown_active_identity_skips_climate_lookup_and_cache() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    state = _FakeState()
    resolver = _resolver(database, state)

    result = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=None
    )

    assert climate_repo.calls == []
    assert result["active_period"] is None
    assert result["current_period_name"] == "NO_PERIOD"
    assert result["setpoint_data"] is None
    assert not _climate_sets(state)


@pytest.mark.asyncio
async def test_invalid_identity_shape_is_unknown_not_null_base_coercion() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    state = _FakeState()
    resolver = _resolver(database, state)

    named_only = await resolver.resolve_period(
        _LOCATION,
        _CLUSTER,
        _MORNING,
        database,
        active_profile={"location": _LOCATION, "mode_name": "flower"},
    )
    text_submode = await resolver.resolve_period(
        _LOCATION,
        _CLUSTER,
        _MORNING,
        database,
        active_profile={"mode_id": 2, "submode_id": "5"},
    )

    assert climate_repo.calls == []
    assert named_only["active_period"] is None
    assert text_submode["active_period"] is None
    assert not _climate_sets(state)


@pytest.mark.asyncio
async def test_known_identity_queries_exact_null_safe_profile() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    resolver = _resolver(database, _FakeState())

    result = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )

    assert climate_repo.calls == [(_LOCATION, _CLUSTER, "06:00", 2, None)]
    assert result["setpoint_data"]["heating_setpoint"] == 26.0


@pytest.mark.asyncio
async def test_mode_switch_same_minute_returns_new_profile_not_cached_old() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    state = _FakeState()
    resolver = _resolver(database, state)

    mode_one = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(1, None)
    )
    mode_two = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )
    mode_one_again = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(1, None)
    )

    assert mode_one["setpoint_data"]["heating_setpoint"] == 22.0
    assert mode_two["setpoint_data"]["heating_setpoint"] == 26.0
    assert mode_one_again["setpoint_data"]["heating_setpoint"] == 22.0
    # One DB read per profile: the mode-2 lookup never reused the mode-1
    # entry, and the mode-1 re-read reused its own cached entry.
    assert [call[3] for call in climate_repo.calls] == [1, 2]
    assert (
        climate_period_cache_key(_LOCATION, _CLUSTER, 1, None)
        != climate_period_cache_key(_LOCATION, _CLUSTER, 2, None)
    )


@pytest.mark.asyncio
async def test_submode_identities_same_minute_stay_distinct() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    resolver = _resolver(database, _FakeState())

    stretch = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(1, 4)
    )
    bulk = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(1, 5)
    )
    stretch_again = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(1, 4)
    )

    assert stretch["setpoint_data"]["climate_identity"] == (1, 4, "Stretch", "06:00", "18:00")
    assert bulk["setpoint_data"]["climate_identity"] == (1, 5, "Bulk", "06:00", "18:00")
    assert stretch_again["setpoint_data"]["climate_identity"] == (1, 4, "Stretch", "06:00", "18:00")
    assert [call[4] for call in climate_repo.calls] == [4, 5]


@pytest.mark.asyncio
async def test_cache_entry_is_exact_time_str_and_period_with_ttl_30() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    state = _FakeState()
    resolver = _resolver(database, state)

    await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )

    key = climate_period_cache_key(_LOCATION, _CLUSTER, 2, None)
    stored = state.entries[key]
    assert set(stored.keys()) == {"time_str", "period"}
    assert stored["time_str"] == "06:00"
    assert stored["period"]["heating_setpoint"] == 26.0
    assert _climate_sets(state) == [(key, stored, 30.0)]


@pytest.mark.asyncio
async def test_cached_value_reused_only_for_matching_minute() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    state = _FakeState()
    resolver = _resolver(database, state)

    await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )
    expired = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING_NEXT_MINUTE, database, active_profile=_profile(2, None)
    )
    refilled = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING_NEXT_MINUTE, database, active_profile=_profile(2, None)
    )

    assert len(climate_repo.calls) == 2  # 06:00 fill, 06:01 expiry refill
    key = climate_period_cache_key(_LOCATION, _CLUSTER, 2, None)
    assert state.entries[key]["time_str"] == "06:01"
    expired_heating = expired["setpoint_data"]["heating_setpoint"]
    refilled_heating = refilled["setpoint_data"]["heating_setpoint"]
    assert expired_heating == refilled_heating == 26.0


@pytest.mark.asyncio
async def test_save_invalidation_is_immediate_and_scoped_to_the_profile_key() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    state = _FakeState()
    resolver = _resolver(database, state)

    saved_key = climate_period_cache_key(_LOCATION, _CLUSTER, 2, None)
    other_key = climate_period_cache_key(_LOCATION, _CLUSTER, 1, None)
    # Same-profile Save invalidates exactly this key; the other profile's
    # cached entry must survive untouched.
    state.entries[other_key] = {
        "time_str": "06:00",
        "period": _PERIODS[(1, None)],
    }

    await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )
    await state.delete(saved_key)
    invalidated = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )

    assert state.deletes == [saved_key]
    assert invalidated["setpoint_data"]["heating_setpoint"] == 26.0
    assert climate_repo.calls == [
        (_LOCATION, _CLUSTER, "06:00", 2, None),
        (_LOCATION, _CLUSTER, "06:00", 2, None),
    ]
    assert other_key in state.entries


@pytest.mark.asyncio
async def test_database_error_returns_none_without_caching_empty_state() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    climate_repo.fail = True
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    state = _FakeState()
    resolver = _resolver(database, state)

    failed = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )

    assert failed["active_period"] is None
    assert failed["setpoint_data"] is None
    # A failed read never installs an empty or failed cache entry.
    assert not _climate_sets(state)

    climate_repo.fail = False
    recovered = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )

    assert len(climate_repo.calls) == 2  # failed read, then one recovery read
    assert recovered["setpoint_data"]["heating_setpoint"] == 26.0
    assert state.entries[climate_period_cache_key(_LOCATION, _CLUSTER, 2, None)]["period"][
        "heating_setpoint"
    ] == 26.0


@pytest.mark.asyncio
async def test_climate_identity_uses_captured_ids_not_replacement_row_ids() -> None:
    polluted_row = _period_row(7, None, "Day", 26.0)
    polluted_row["mode_id"] = 7
    polluted_row["submode_id"] = None
    periods: dict[tuple[int, int | None], dict[str, Any]] = {(2, None): polluted_row}
    climate_repo = _FakeClimatePeriodsRepository(periods)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    state = _FakeState()
    resolver = _resolver(database, state)

    fresh = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )
    cached = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )

    expected_identity = (2, None, "Day", "06:00", "18:00")
    assert fresh["setpoint_data"]["climate_identity"] == expected_identity
    assert cached["setpoint_data"]["climate_identity"] == expected_identity
    assert climate_repo.calls[-1][3] == 2 and climate_repo.calls[-1][4] is None


@pytest.mark.asyncio
async def test_climate_identity_normalizes_time_object_bounds() -> None:
    from datetime import time as dt_time

    periods: dict[tuple[int, int | None], dict[str, Any]] = {
        (1, None): _period_row(
            1,
            None,
            "Day",
            22.0,
            start_time=dt_time(6, 0),
            end_time=dt_time(18, 0),
        )
    }
    climate_repo = _FakeClimatePeriodsRepository(periods)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    resolver = _resolver(database, _FakeState())

    result = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(1, None)
    )

    assert result["setpoint_data"]["climate_identity"] == (1, None, "Day", "06:00", "18:00")


@pytest.mark.asyncio
async def test_null_base_profile_key_spells_submode_none() -> None:
    climate_repo = _FakeClimatePeriodsRepository(_PERIODS)
    database = _FakeDatabase(climate_repo, _FakeScheduleRepository())
    resolver = _resolver(database, _FakeState())

    base = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, None)
    )
    bulk = await resolver.resolve_period(
        _LOCATION, _CLUSTER, _MORNING, database, active_profile=_profile(2, 5)
    )

    assert climate_period_cache_key(_LOCATION, _CLUSTER, 2, None).endswith("submode:none")
    assert climate_period_cache_key(_LOCATION, _CLUSTER, 2, 5).endswith("submode:5")
    assert base["setpoint_data"]["climate_identity"][1] is None
    assert bulk["setpoint_data"]["climate_identity"][1] == 5
    assert len(climate_repo.calls) == 2
