from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
import pytest

from app.alarm_manager import AlarmManager
from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent, serialize_operational_event
from app.routes.alarms import acknowledge_alarm
from app.routes.failsafe import clear_failsafe
from app.routes.flags import update_flag
from app.routes.notes import NotesBody, save_notes
from app.schemas.flags import FlagUpdateRequest


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _AlarmManager:
    def __init__(self, acknowledged: bool) -> None:
        self._acknowledged = acknowledged

    def acknowledge_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        del location, cluster, alarm_name
        return self._acknowledged


class _FailsafeManager:
    def __init__(self, cleared: bool) -> None:
        self._cleared = cleared

    def clear_failsafe(self, location: str, cluster: str) -> bool:
        del location, cluster
        return self._cleared


class _FlagManager:
    FLAG_DEFINITIONS = {"SAFE_MODE": "Enable safe mode"}

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def get_flag(self, flag_name: str) -> bool:
        assert flag_name in self.FLAG_DEFINITIONS
        return self._enabled

    def set_flag(self, flag_name: str, enabled: bool) -> None:
        assert flag_name in self.FLAG_DEFINITIONS
        self._enabled = enabled

    def get_flag_definition(self, flag_name: str):
        from app.feature_flags import FeatureFlag

        if flag_name not in self.FLAG_DEFINITIONS:
            return None
        return FeatureFlag(
            name=flag_name,
            enabled=self._enabled,
            description=self.FLAG_DEFINITIONS[flag_name],
        )


class _FailedFlagManager(_FlagManager):
    def get_flag_definition(self, flag_name: str):
        del flag_name
        return None


class _FailsafeRedis:
    def read_alarms(self, _location: str, _cluster: str) -> dict[str, dict[str, object]]:
        return {}

    def clear_failsafe(self, _location: str, _cluster: str) -> bool:
        return True

    def write_mode(self, _location: str, _cluster: str, _mode: str, *, source: str) -> bool:
        assert source == "system"
        return True


@pytest.mark.asyncio
async def test_acknowledge_emits_one_correlated_semantic_alarm_event_when_persisted() -> None:
    # Given: a successfully acknowledged alarm and an injected route event context.
    sink = _RecordingSink()
    context = MutationRequestContext(UUID("7552d5f1-0a9a-43e8-a63b-26a60d126c2e"))

    # When: the acknowledgement route completes.
    response = await acknowledge_alarm(
        "Veg Room", "main", "relay_mismatch", context, sink, _AlarmManager(True)
    )

    # Then: the route emits its single semantic lifecycle event with request correlation.
    assert response["success"] is True
    assert len(sink.events) == 1
    assert sink.events[0].event_type == "alarm.acknowledged"
    assert sink.events[0].correlation_id == context.correlation_id
    assert sink.events[0].payload.state == "acknowledged"


@pytest.mark.asyncio
async def test_acknowledge_emits_nothing_when_alarm_is_not_found() -> None:
    # Given: an alarm manager which declines an acknowledgement.
    sink = _RecordingSink()
    context = MutationRequestContext.create()

    # When: the acknowledgement route reports the missing alarm.
    with pytest.raises(HTTPException):
        await acknowledge_alarm("Veg Room", "main", "missing", context, sink, _AlarmManager(False))

    # Then: no success event is emitted.
    assert sink.events == []


@pytest.mark.asyncio
async def test_note_save_emits_only_safe_changed_length_metadata(
    tmp_path, monkeypatch, caplog
) -> None:
    # Given: a note directory and content that must never enter an operational event.
    from app.routes import notes

    content = "operator-private-note"
    monkeypatch.setattr(notes, "NOTES_DATA_DIR", str(tmp_path))
    sink = _RecordingSink()

    # When: the route persists changed note content.
    await save_notes(
        "Veg Room",
        "main",
        "auto",
        NotesBody(content=content),
        MutationRequestContext.create(),
        sink,
    )

    # Then: the event contains metadata but not the note content.
    serialized = serialize_operational_event(sink.events[0])
    assert len(sink.events) == 1
    assert content.encode() not in serialized
    assert {change.key for change in sink.events[0].payload.changes} == {
        "notes_changed",
        "notes_length",
    }
    assert content not in caplog.text


@pytest.mark.asyncio
async def test_note_save_emits_nothing_for_unchanged_content(tmp_path, monkeypatch) -> None:
    # Given: already-persisted note content.
    from app.routes import notes

    content = "same-private-note"
    monkeypatch.setattr(notes, "NOTES_DATA_DIR", str(tmp_path))
    path = notes._notes_path("Veg Room", "main", "auto")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    sink = _RecordingSink()

    # When: the same content is saved again.
    await save_notes(
        "Veg Room",
        "main",
        "auto",
        NotesBody(content=content),
        MutationRequestContext.create(),
        sink,
    )

    # Then: the no-op persistence emits no misleading mutation event.
    assert sink.events == []


@pytest.mark.asyncio
async def test_note_save_emits_nothing_when_persistence_fails(tmp_path, monkeypatch) -> None:
    # Given: a note path that cannot be read or written as a text file.
    from app.routes import notes

    monkeypatch.setattr(notes, "_notes_path", lambda *_args: tmp_path)
    sink = _RecordingSink()

    # When: the route translates the persistence failure.
    with pytest.raises(HTTPException) as error:
        await save_notes(
            "Veg Room",
            "main",
            "auto",
            NotesBody(content="private-note"),
            MutationRequestContext.create(),
            sink,
        )

    # Then: no note metadata is emitted without authoritative persistence.
    assert error.value.status_code == 500
    assert sink.events == []


@pytest.mark.asyncio
async def test_flag_update_emits_only_when_the_persisted_value_changes() -> None:
    # Given: an enabled-state transition for an allowlisted feature flag.
    sink = _RecordingSink()

    # When: the route persists the enabled value.
    await update_flag(
        "SAFE_MODE",
        FlagUpdateRequest(enabled=True),
        MutationRequestContext.create(),
        sink,
        _FlagManager(False),
    )

    # Then: exactly one scalar enabled change reaches the event sink.
    assert len(sink.events) == 1
    assert sink.events[0].payload.changes[0].key == "enabled"


@pytest.mark.asyncio
async def test_flag_update_emits_nothing_for_an_idempotent_persistence() -> None:
    # Given: a flag that is already enabled.
    sink = _RecordingSink()

    # When: the route persists the same enabled value.
    await update_flag(
        "SAFE_MODE",
        FlagUpdateRequest(enabled=True),
        MutationRequestContext.create(),
        sink,
        _FlagManager(True),
    )

    # Then: the no-op produces no operational mutation event.
    assert sink.events == []


@pytest.mark.asyncio
async def test_flag_update_emits_nothing_when_persistence_cannot_be_confirmed() -> None:
    # Given: a flag manager that cannot return the persisted definition.
    sink = _RecordingSink()

    # When: the route rejects the unconfirmed update.
    with pytest.raises(HTTPException) as error:
        await update_flag(
            "SAFE_MODE",
            FlagUpdateRequest(enabled=True),
            MutationRequestContext.create(),
            sink,
            _FailedFlagManager(False),
        )

    # Then: no mutation event is emitted without authoritative persistence.
    assert error.value.status_code == 500
    assert sink.events == []


@pytest.mark.asyncio
async def test_failsafe_clear_failure_emits_no_route_event() -> None:
    # Given: a failsafe manager that rejects an unsafe clear.
    manager = _FailsafeManager(False)

    # When: the route reports the rejected clear.
    with pytest.raises(HTTPException) as error:
        await clear_failsafe("Veg Room", "main", manager)

    # Then: the failure remains event-silent.
    assert error.value.status_code == 400


def test_failsafe_clear_is_covered_by_its_semantic_event_source() -> None:
    # Given / When: the clear route is inspected by mutation route coverage.
    marked = getattr(clear_failsafe, "__emits_operational_mutation__", False)

    # Then: coverage recognizes its manager-owned semantic event.
    assert marked is True


def test_failsafe_manager_emits_one_clear_event_for_repeated_successful_clears() -> None:
    # Given: an active failsafe and the manager-owned operational event sink.
    sink = _RecordingSink()
    manager = AlarmManager(_FailsafeRedis(), event_sink=sink)
    manager._active_failsafes.add(("Veg Room", "main"))

    # When: the manager successfully clears the same failsafe twice.
    assert manager.clear_failsafe("Veg Room", "main") is True
    assert manager.clear_failsafe("Veg Room", "main") is True

    # Then: the semantic lifecycle event is emitted exactly once.
    assert [event.event_type for event in sink.events] == ["system.failsafe_cleared"]
