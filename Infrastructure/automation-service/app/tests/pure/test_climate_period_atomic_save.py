"""Legacy climate-period save: exact scope, atomic staging, post-commit truth.

The route's previous read, scoped delete, replacement inserts and the one
configuration revision must share one transaction under the timeline advisory
lock. These tests stage concrete in-memory state so a mid-replacement failure
observabley leaves every prior row intact, and so the post-commit invalidator
captures identities whose rows no longer remain.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from app.events import ConfigChangeEvent, ConfigEventType
from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.repositories.climate_periods import ClimatePeriodRepository
from app.routes.climate_periods import save_climate_periods
from app.schemas.climate_periods import PeriodInput, PeriodsSaveRequest
from app.services import climate_timeline_apply as climate_apply_module
from shared.redis_keys import climate_period_cache_key

_LOCATION = "Flower Room"
_CLUSTER = "main"
_TIMELINE_LOCK = 7_281_992


def _period(
    period_name: str,
    *,
    start: str = "00:00",
    end: str = "00:00",
    heating: float | None = 20.0,
    ramp: int = 0,
) -> dict[str, object]:
    return {
        "period_name": period_name,
        "start_time": start,
        "end_time": end,
        "ramp_minutes": ramp,
        "heating_setpoint": heating,
        "cooling_setpoint": None,
        "vpd_setpoint": None,
        "co2_setpoint": None,
        "details": None,
    }


def _row(
    period_name: str,
    mode_id: int | None,
    submode_id: int | None,
    *,
    row_id: int,
    start: object = "00:00",
    end: object = "00:00",
    heating: float | None = 20.0,
) -> dict[str, object]:
    return {
        "id": row_id,
        "location": _LOCATION,
        "cluster": _CLUSTER,
        "mode_id": mode_id,
        "submode_id": submode_id,
        "period_name": period_name,
        "start_time": start,
        "end_time": end,
        "ramp_minutes": 0,
        "heating_setpoint": heating,
        "cooling_setpoint": None,
        "vpd_setpoint": None,
        "co2_setpoint": None,
        "details": None,
    }


def _seed_rows() -> list[dict[str, object]]:
    """Same mode with NULL and non-NULL submode sets; prior clocks are TIME values."""
    return [
        _row("Day", 1, None, row_id=1, start=time(6, 0), end=time(18, 0), heating=22.0),
        _row("Night", 1, None, row_id=2, start=time(18, 0), end=time(6, 0), heating=18.0),
        _row("Bulk Day", 1, 4, row_id=3, start=time(6, 0), end=time(18, 0), heating=21.0),
        _row("Bulk Night", 1, 4, row_id=4, start=time(18, 0), end=time(6, 0), heating=17.0),
        _row("Veg All Day", 2, None, row_id=5, heating=19.0),
    ]


def _identity(row: dict[str, object]) -> tuple[int | None, int | None]:
    return (row.get("mode_id"), row.get("submode_id"))  # type: ignore[return-value]


@dataclass
class _State:
    revision: int = 5
    periods: list[dict[str, object]] = field(default_factory=_seed_rows)
    active_row: dict[str, object] | None = field(
        default_factory=lambda: {"mode_id": 1, "submode_id": None}
    )
    fail_delete: bool = False
    delete_false: bool = False
    fail_insert_at: int | None = None
    fail_version: bool = False
    trace: list[str] = field(default_factory=list)


class _Connection:
    def __init__(self, state: _State) -> None:
        self._state = state
        self._staged: _State | None = None

    @asynccontextmanager
    async def transaction(self):
        self._state.trace.append("transaction_enter")
        self._staged = deepcopy(self._state)
        try:
            yield
        except Exception:
            self._state.trace.append("rollback")
            raise
        else:
            self._state.revision = self._staged.revision
            self._state.periods = self._staged.periods
            self._state.trace.append("commit")

    async def execute(self, query: str, *args: object) -> str:
        assert "pg_advisory_xact_lock" in query, query
        assert args == (_TIMELINE_LOCK,)
        self._state.trace.append("lock")
        return "SELECT 1"

    def staged_periods(self) -> list[dict[str, object]]:
        assert self._staged is not None
        return self._staged.periods

    def stage_periods(self, rows: list[dict[str, object]]) -> None:
        assert self._staged is not None
        self._staged.periods = rows

    def stage_revision(self) -> int:
        assert self._staged is not None
        self._staged.revision += 1
        return self._staged.revision


class _Pool:
    def __init__(self, state: _State) -> None:
        self._connection = _Connection(state)

    @asynccontextmanager
    async def acquire(self):
        yield self._connection


class _PeriodsRepository:
    """Exact NULL-safe slice semantics staged on the caller-owned connection."""

    def __init__(self, state: _State) -> None:
        self._state = state
        self._insert_calls = 0
        self._inserted_rows = 0

    def validate_24h_coverage(
        self, periods: list[dict[str, object]]
    ) -> tuple[bool, list[str]]:
        """The route's 400 gate is the real repository validation."""
        return ClimatePeriodRepository().validate_24h_coverage(periods)

    def _matches(
        self,
        row: dict[str, object],
        location: str,
        cluster: str,
        mode_id: int | None,
        submode_id: int | None,
    ) -> bool:
        if row.get("location") != location or row.get("cluster") != cluster:
            return False
        if mode_id is None:
            return True
        return row.get("mode_id") == mode_id and row.get("submode_id") == submode_id

    def _slice(
        self,
        rows: list[dict[str, object]],
        location: str,
        cluster: str,
        mode_id: int | None,
        submode_id: int | None,
    ) -> list[dict[str, object]]:
        return [
            row
            for row in rows
            if self._matches(row, location, cluster, mode_id, submode_id)
        ]

    def _rows(self, conn: object) -> list[dict[str, object]]:
        if conn is not None:
            return conn.staged_periods()  # type: ignore[union-attr]
        return self._state.periods

    async def get_periods(
        self,
        location: str,
        cluster: str,
        mode_id: int | None = None,
        submode_id: int | None = None,
        conn: object = None,
    ) -> list[dict[str, object]]:
        if conn is not None:
            self._state.trace.append("read_previous")
        return deepcopy(
            self._slice(self._rows(conn), location, cluster, mode_id, submode_id)
        )

    async def get_periods_for_room_mode(
        self,
        location: str,
        cluster: str,
        mode_id: int,
        submode_id: int | None,
        conn: object = None,
    ) -> list[dict[str, object]]:
        return await self.get_periods(location, cluster, mode_id, submode_id, conn=conn)

    async def delete_periods(
        self,
        location: str,
        cluster: str,
        mode_id: int | None = None,
        submode_id: int | None = None,
        conn: object = None,
    ) -> bool:
        if self._state.fail_delete:
            raise RuntimeError("injected delete failure")
        if self._state.delete_false:
            return False
        rows = self._rows(conn)
        keep = [
            row
            for row in rows
            if not self._matches(row, location, cluster, mode_id, submode_id)
        ]
        if conn is not None:
            self._state.trace.append("delete_periods")
            conn.stage_periods(keep)  # type: ignore[union-attr]
        else:
            self._state.periods = keep
        return True

    async def save_period(
        self,
        *,
        location: str,
        cluster: str,
        period_name: str,
        start_time: str,
        end_time: str,
        ramp_minutes: int,
        heating_setpoint: float | None,
        cooling_setpoint: float | None,
        vpd_setpoint: int | None,
        co2_setpoint: int | None,
        details: str | None,
        mode_id: int | None,
        submode_id: int | None,
        conn: object = None,
    ) -> dict[str, object]:
        self._state.trace.append("save_period")
        self._insert_calls += 1
        if self._state.fail_insert_at == self._insert_calls:
            raise RuntimeError("injected period insert failure")
        assert conn is not None
        rows = conn.staged_periods()  # type: ignore[union-attr]
        for row in rows:
            same_identity = (
                row.get("location") == location
                and row.get("cluster") == cluster
                and row.get("mode_id") == mode_id
                and row.get("submode_id") == submode_id
                and row.get("period_name") == period_name
            )
            if same_identity:
                row.update(
                    {
                        "start_time": start_time,
                        "end_time": end_time,
                        "ramp_minutes": ramp_minutes,
                        "heating_setpoint": heating_setpoint,
                        "cooling_setpoint": cooling_setpoint,
                        "vpd_setpoint": vpd_setpoint,
                        "co2_setpoint": co2_setpoint,
                        "details": details,
                    }
                )
                break
        else:
            self._inserted_rows += 1
            rows.append(
                {
                    "id": 100 + self._inserted_rows,
                    "location": location,
                    "cluster": cluster,
                    "mode_id": mode_id,
                    "submode_id": submode_id,
                    "period_name": period_name,
                    "start_time": start_time,
                    "end_time": end_time,
                    "ramp_minutes": ramp_minutes,
                    "heating_setpoint": heating_setpoint,
                    "cooling_setpoint": cooling_setpoint,
                    "vpd_setpoint": vpd_setpoint,
                    "co2_setpoint": co2_setpoint,
                    "details": details,
                }
            )
        return {
            "id": 11,
            "location": location,
            "cluster": cluster,
            "period_name": period_name,
            "start_time": start_time,
            "end_time": end_time,
        }


class _ConfigRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    async def log_config_version(self, **kwargs: object) -> int | None:
        conn = kwargs.get("conn")
        assert conn is not None
        self._state.trace.append("log_revision")
        if self._state.fail_version:
            return None
        return conn.stage_revision()


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _RecordingInvalidationState:
    def __init__(self) -> None:
        self.deletes: list[str] = []

    async def delete(self, key: str, skip_redis: bool = False) -> bool:
        del skip_redis
        self.deletes.append(key)
        return True


class _RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[ConfigChangeEvent] = []

    async def publish(self, event: ConfigChangeEvent) -> bool:
        self.events.append(event)
        return True


def _notification_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_RecordingInvalidationState, _RecordingEventBus]:
    state_manager = _RecordingInvalidationState()
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(climate_apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(climate_apply_module, "get_event_bus", lambda: event_bus)
    return state_manager, event_bus


def _database(state: _State) -> SimpleNamespace:
    return SimpleNamespace(
        _get_pool=AsyncMock(return_value=_Pool(state)),
        climate_periods_repo=_PeriodsRepository(state),
        config_repo=_ConfigRepository(state),
        room_mode_repo=SimpleNamespace(
            get_active_mode=AsyncMock(return_value=dict(state.active_row or {}))
        ),
    )


def _save_request(
    periods: list[PeriodInput], mode_id: int | None, submode_id: int | None
) -> PeriodsSaveRequest:
    return PeriodsSaveRequest(periods=periods, mode_id=mode_id, submode_id=submode_id)


def _profile_key(mode_id: int | None, submode_id: int | None) -> str:
    return climate_period_cache_key(_LOCATION, _CLUSTER, mode_id, submode_id)


# --- Pure coverage validation on the real repository -------------------------


def test_validate_rejects_a_gap_between_consecutive_periods() -> None:
    # Given: three periods that touch except for a two-hour hole.
    periods = [
        _period("Night", start="00:00", end="06:00"),
        _period("Midday", start="08:00", end="12:00"),
        _period("Afternoon", start="12:00", end="00:00"),
    ]

    valid, errors = ClimatePeriodRepository().validate_24h_coverage(periods)

    assert valid is False
    assert errors == ["Coverage gap: 06:00-08:00 is not covered by any period"]


def test_validate_rejects_the_wrap_gap_at_day_end() -> None:
    # Given: coverage that stops at 22:00 with nothing until midnight.
    periods = [
        _period("Day", start="06:00", end="22:00"),
        _period("Morning", start="00:00", end="06:00"),
    ]

    valid, errors = ClimatePeriodRepository().validate_24h_coverage(periods)

    assert valid is False
    assert errors == ["Coverage gap: 22:00-24:00 is not covered by any period"]


def test_validate_accepts_touching_periods_that_cover_the_whole_day() -> None:
    periods = [
        _period("Night", start="00:00", end="06:00"),
        _period("Day", start="06:00", end="18:00"),
        _period("Evening", start="18:00", end="00:00"),
    ]

    valid, errors = ClimatePeriodRepository().validate_24h_coverage(periods)

    assert (valid, errors) == (True, [])


def test_validate_handles_time_object_rows_including_midnight() -> None:
    # Given: rows read back from TIME columns; midnight is a falsy time object.
    periods = [
        {"period_name": "All Day", "start_time": time(0, 0), "end_time": time(0, 0), "ramp_minutes": 0},
        {
            "period_name": "Wrap",
            "start_time": time(22, 0),
            "end_time": time(2, 0),
            "ramp_minutes": 0,
        },
    ]

    valid, errors = ClimatePeriodRepository().validate_24h_coverage(periods)

    assert valid is False  # the wrap period overlaps the all-day period
    assert errors and errors[0].startswith("Overlap:")


def test_validate_accepts_a_single_all_day_period() -> None:
    periods = [_period("All Day")]

    valid, errors = ClimatePeriodRepository().validate_24h_coverage(periods)

    assert (valid, errors) == (True, [])


def test_validate_keeps_the_maximum_seven_periods_rule() -> None:
    eight: list[dict[str, object]] = []
    for index in range(8):
        eight.append(_period(f"P{index}", start=f"{index:02d}:00", end=f"{index + 1:02d}:00"))
    seven = [
        _period("P0", start="00:00", end="04:00"),
        _period("P1", start="04:00", end="08:00"),
        _period("P2", start="08:00", end="12:00"),
        _period("P3", start="12:00", end="16:00"),
        _period("P4", start="16:00", end="20:00"),
        _period("P5", start="20:00", end="22:00"),
        _period("P6", start="22:00", end="00:00"),
    ]

    eight_valid, eight_errors = ClimatePeriodRepository().validate_24h_coverage(eight)
    seven_valid, seven_errors = ClimatePeriodRepository().validate_24h_coverage(seven)

    assert eight_valid is False
    assert eight_errors == ["Maximum 7 periods allowed, got 8"]
    assert (seven_valid, seven_errors) == (True, [])


def test_validate_reports_overlap_without_also_claiming_gaps() -> None:
    # Given: two overlapping periods whose union would still leave no hole.
    periods = [
        _period("Day", start="00:00", end="13:00"),
        _period("Night", start="12:00", end="00:00"),
    ]

    valid, errors = ClimatePeriodRepository().validate_24h_coverage(periods)

    assert valid is False
    assert [error.split(":")[0] for error in errors] == ["Overlap"]


def test_validate_still_reports_invalid_time_text() -> None:
    periods = [{"period_name": "Bad", "start_time": "6am", "end_time": "00:00", "ramp_minutes": 0}]

    valid, errors = ClimatePeriodRepository().validate_24h_coverage(periods)

    assert valid is False
    assert errors == ["Invalid time format in period 'Bad': 6am - 00:00"]


# --- Repository NULL-safety contract -----------------------------------------


class _RecordingSqlConnection:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.queries.append((query, args))
        return []

    async def execute(self, query: str, *args: object) -> str:
        self.queries.append((query, args))
        return "DELETE 0"


@pytest.mark.asyncio
async def test_repository_scopes_a_supplied_mode_to_the_exact_null_submode_slice() -> None:
    # Given: the real repository against a recording connection.
    repository = ClimatePeriodRepository()
    conn = _RecordingSqlConnection()

    # When: the mode is supplied with a NULL submode.
    await repository.get_periods(_LOCATION, _CLUSTER, 1, None, conn=conn)
    await repository.delete_periods(_LOCATION, _CLUSTER, 1, None, conn=conn)
    # ...and when the mode is omitted the read/delete stay broad.
    await repository.get_periods(_LOCATION, _CLUSTER, conn=conn)
    await repository.delete_periods(_LOCATION, _CLUSTER, conn=conn)

    scoped = [query for query, _args in conn.queries if "IS NOT DISTINCT FROM" in query]
    broad = [query for query, _args in conn.queries if "IS NOT DISTINCT FROM" not in query]

    # Then: only the supplied-mode statements carry the NULL-safe predicate.
    assert len(scoped) == 2
    assert len(broad) == 2
    assert all("mode_id = $3" in query for query in scoped)


# --- Route-level atomicity, scope and post-commit truth ----------------------


@pytest.mark.asyncio
async def test_scoped_null_submode_save_replaces_only_the_null_base_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: NULL-base, sibling-submode and other-mode rows plus an active NULL-base identity.
    state = _State()
    state_manager, event_bus = _notification_boundaries(monkeypatch)
    sink = _RecordingSink()

    # When: the NULL-base slice is replaced with changed setpoints.
    response = await save_climate_periods(
        _LOCATION,
        _CLUSTER,
        _save_request([_period("Day", start="00:00", end="12:00", heating=21.0),
                       _period("Night", start="12:00", end="00:00", heating=19.0)], 1, None),
        _database(state),
        MutationRequestContext.create(),
        sink,
    )

    # Then: only the requested slice changes; sibling identities survive with
    # their original values, one revision is appended, and the exact active
    # profile key invalidates after the commit.
    assert response["notification_warning"] is None
    assert len(response["periods"]) == 2
    assert state.revision == 6
    identities = {_identity(row) for row in state.periods}
    assert identities == {(1, None), (1, 4), (2, None)}
    null_rows = [row for row in state.periods if _identity(row) == (1, None)]
    assert [row["heating_setpoint"] for row in null_rows] == [21.0, 19.0]
    bulk_rows = [row for row in state.periods if _identity(row) == (1, 4)]
    assert [row["heating_setpoint"] for row in bulk_rows] == [21.0, 17.0]
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_previous",
        "delete_periods",
        "save_period",
        "save_period",
        "log_revision",
        "commit",
    ]
    assert state_manager.deletes == [
        _profile_key(1, None),
        "schedules:loc:Flower Room:cluster:main",
        "schedules:loc:Flower Room:cluster:main:climate",
        "schedule:Flower Room:main",
        "schedules:all",
    ]
    assert [(event.event_type, event.data) for event in event_bus.events] == [
        (
            ConfigEventType.SCHEDULE_CHANGED,
            {"config_revision": "0000006", "mode_id": 1, "submode_id": None},
        )
    ]
    assert [change.key for change in sink.events[0].payload.changes] == ["periods_digest"]


@pytest.mark.asyncio
async def test_scoped_inactive_save_touches_no_schedule_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a bulk-submode active identity and a saved inactive veg profile.
    state = _State(active_row={"mode_id": 1, "submode_id": 4})
    state_manager, event_bus = _notification_boundaries(monkeypatch)
    sink = _RecordingSink()

    # When: the inactive profile slice is replaced.
    response = await save_climate_periods(
        _LOCATION,
        _CLUSTER,
        _save_request([_period("Veg All Day", heating=19.0)], 2, None),
        _database(state),
        MutationRequestContext.create(),
        sink,
    )

    # Then: preparation stands persisted but installs no schedule cache clear
    # and publishes no event; only the saved identity key clears.
    assert response["notification_warning"] is None
    assert [row["heating_setpoint"] for row in state.periods if _identity(row) == (2, None)] == [
        19.0
    ]
    assert [row for row in state.periods if _identity(row) == (1, None)]
    assert state_manager.deletes == [_profile_key(2, None)]
    assert event_bus.events == []
    assert state.revision == 6


@pytest.mark.asyncio
async def test_broad_save_replaces_the_whole_room_and_captures_prior_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: rows for two identities and a broad legacy replacement that leaves
    # no row for either of them.
    state = _State(active_row={"mode_id": 1, "submode_id": 4})
    state_manager, event_bus = _notification_boundaries(monkeypatch)
    sink = _RecordingSink()

    # When: the room-wide save commits NULL-mode replacement rows.
    response = await save_climate_periods(
        _LOCATION,
        _CLUSTER,
        _save_request([_period("Legacy Day", heating=20.0)], None, None),
        _database(state),
        MutationRequestContext.create(),
        sink,
    )

    # Then: every prior row is gone, both prior identity keys are invalidated
    # even though no period remains, and NULL-mode rows claim no cache key.
    assert response["notification_warning"] is None
    assert len(state.periods) == 1
    assert state.periods[0]["mode_id"] is None
    assert state.periods[0]["period_name"] == "Legacy Day"
    assert _profile_key(1, None) in state_manager.deletes
    assert _profile_key(1, 4) in state_manager.deletes
    assert _profile_key(None, None) not in state_manager.deletes
    assert any(key.startswith("schedules:") for key in state_manager.deletes)
    assert [(event.event_type, event.data) for event in event_bus.events] == [
        (
            ConfigEventType.SCHEDULE_CHANGED,
            {"config_revision": "0000006", "mode_id": None, "submode_id": None},
        )
    ]
    assert state.revision == 6


@pytest.mark.asyncio
async def test_scoped_save_rolls_back_when_a_period_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a two-period replacement whose second insert fails.
    state = _State(fail_insert_at=2)
    state_manager, event_bus = _notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    before = deepcopy(state.periods)

    # When: the replacement reaches the route.
    with pytest.raises(HTTPException) as error:
        await save_climate_periods(
            _LOCATION,
            _CLUSTER,
            _save_request([_period("Day", start="00:00", end="12:00", heating=21.0),
                           _period("Night", start="12:00", end="00:00", heating=19.0)], 1, None),
            _database(state),
            MutationRequestContext.create(),
            sink,
        )

    # Then: every prior row of every identity is restored, the revision is
    # untouched, and no post-commit boundary observed the abandoned mutation.
    assert error.value.status_code == 500
    assert state.periods == before
    assert state.revision == 5
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_previous",
        "delete_periods",
        "save_period",
        "save_period",
        "rollback",
    ]
    assert state_manager.deletes == []
    assert event_bus.events == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_scoped_save_rolls_back_when_the_version_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a configuration repository that cannot append the one revision.
    state = _State(fail_version=True)
    state_manager, event_bus = _notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    before = deepcopy(state.periods)

    # When: the replacement stages rows but cannot record its revision.
    with pytest.raises(HTTPException) as error:
        await save_climate_periods(
            _LOCATION,
            _CLUSTER,
            _save_request([_period("Day", heating=21.0)], 1, None),
            _database(state),
            MutationRequestContext.create(),
            sink,
        )

    # Then: the whole replacement rolls back with no notification.
    assert error.value.status_code == 500
    assert state.periods == before
    assert state.revision == 5
    assert state.trace[-2:] == ["log_revision", "rollback"]
    assert state_manager.deletes == []
    assert event_bus.events == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_scoped_save_aborts_when_the_delete_reports_no_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a scoped delete that reports it did not persist.
    state = _State(delete_false=True)
    state_manager, event_bus = _notification_boundaries(monkeypatch)
    sink = _RecordingSink()
    before = deepcopy(state.periods)

    # When: the route asks the repository to replace the slice.
    with pytest.raises(HTTPException) as error:
        await save_climate_periods(
            _LOCATION,
            _CLUSTER,
            _save_request([_period("Day", heating=21.0)], 1, None),
            _database(state),
            MutationRequestContext.create(),
            sink,
        )

    # Then: nothing is inserted or versioned after the false delete result.
    assert error.value.status_code == 500
    assert "delete reported no persisted change" in error.value.detail
    assert state.periods == before
    assert state.revision == 5
    assert state.trace == ["transaction_enter", "lock", "read_previous", "rollback"]
    assert state_manager.deletes == []
    assert event_bus.events == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_gappy_replacement_is_rejected_before_the_transaction_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a replacement whose periods do not cover the whole day.
    state = _State()
    _notification_boundaries(monkeypatch)
    sink = _RecordingSink()

    # When: the save reaches the route.
    with pytest.raises(HTTPException) as error:
        await save_climate_periods(
            _LOCATION,
            _CLUSTER,
            _save_request(
                [
                    _period("Morning", start="00:00", end="06:00"),
                    _period("Evening", start="08:00", end="00:00"),
                ],
                1,
                None,
            ),
            _database(state),
            MutationRequestContext.create(),
            sink,
        )

    # Then: coverage gaps are rejected with 400 before any lock or mutation.
    assert error.value.status_code == 400
    assert any("Coverage gap: 06:00-08:00" in detail for detail in error.value.detail["errors"])
    assert state.trace == []
    assert state.periods == _seed_rows()
    assert state.revision == 5
    assert sink.events == []
