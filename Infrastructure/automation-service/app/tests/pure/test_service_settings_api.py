"""Focused tests for automation's shared service-settings API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.events import ConfigChangeEvent, ConfigEventType
from app.events.mutation_context import MutationRequestContext
from app.routes import service_settings


class FakeTransaction:
    def __init__(self, events: list[str]):
        self.events = events
        self.active = False

    async def __aenter__(self):
        self.active = True
        self.events.append("begin")

    async def __aexit__(self, exc_type, exc, traceback):
        self.active = False
        self.events.append("rollback" if exc_type else "commit")


class FakeConnection:
    def __init__(self, pool):
        self.pool = pool
        self.transaction_context = FakeTransaction(pool.events)

    def transaction(self):
        return self.transaction_context

    async def execute(self, query, service, key, value_json, updated_by):
        assert self.transaction_context.active
        self.pool.events.append(f"upsert:{key}")
        self.pool.values.setdefault(service, {})[key] = json.loads(value_json)
        self.pool.metadata[(service, key)] = {
            "updated_at": datetime.now(UTC),
            "updated_by": updated_by,
        }


class FakePool:
    def __init__(self):
        self.events: list[str] = []
        self.values = {"automation": {"update_interval": 2}}
        self.metadata: dict[tuple[str, str], dict[str, object]] = {}
        self.connection = FakeConnection(self)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection

    async def fetch(self, query, *args):
        self.events.append("metadata")
        return [
            {"service": service, "key": key, **metadata}
            for (service, key), metadata in self.metadata.items()
        ]


class FakeEventBus:
    def __init__(self, events: list[str]):
        self.events = events
        self.published = []

    async def publish(self, event):
        self.events.append("published")
        self.published.append(event)
        return True

    def emit_nowait(self, event) -> None:
        self.events.append("sink")
        self.published.append(event)


@pytest.mark.asyncio
async def test_get_returns_all_service_schemas_and_metadata(monkeypatch):
    pool = FakePool()
    updated_at = datetime.now(UTC)
    pool.metadata[("automation", "update_interval")] = {
        "updated_at": updated_at,
        "updated_by": "operator",
    }
    values = {"automation": {"update_interval": 4}}

    async def load_settings(_pool, service, **kwargs):
        return dict(values.get(service, {}))

    monkeypatch.setattr(service_settings.ServiceSettings, "load", load_settings)
    response = await service_settings.get_service_settings(
        database=SimpleNamespace(pool=pool),
        config=SimpleNamespace(service_settings_fallback=lambda: {}),
    )

    assert set(response["services"]) == {"automation", "soil", "onewire", "weather"}
    automation = response["services"]["automation"]
    assert automation["settings"]["update_interval"] == {
        "value": 4,
        "updated_at": updated_at,
        "updated_by": "operator",
    }
    assert automation["schema"]["update_interval"]["type"] == "integer"


@pytest.mark.asyncio
async def test_put_commits_values_and_version_before_publishing(monkeypatch):
    pool = FakePool()
    event_bus = FakeEventBus(pool.events)
    version_calls = []

    async def load_settings(_pool, service, **kwargs):
        return dict(pool.values.get(service, {}))

    async def log_config_version(**kwargs):
        assert pool.connection.transaction_context.active
        assert kwargs["conn"] is pool.connection
        version_calls.append(kwargs)
        pool.events.append("version")

    monkeypatch.setattr(service_settings.ServiceSettings, "load", load_settings)
    monkeypatch.setattr(service_settings, "get_event_bus", lambda: event_bus)

    config = SimpleNamespace(
        service_settings_fallback=lambda: {},
        get_control_config=lambda: dict(pool.values["automation"]),
    )

    async def refresh_service_settings():
        pool.events.append("refresh")

    config.refresh_service_settings = refresh_service_settings
    response = await service_settings.update_service_settings(
        "automation",
        {"update_interval": 3},
        database=SimpleNamespace(
            pool=pool,
            config_repo=SimpleNamespace(log_config_version=log_config_version),
        ),
        config=config,
        context=MutationRequestContext(correlation_id=uuid4()),
        sink=event_bus,
    )

    assert pool.values["automation"]["update_interval"] == 3
    assert pool.events == [
        "begin",
        "upsert:update_interval",
        "version",
        "commit",
        "refresh",
        "metadata",
        "sink",
        "published",
    ]
    assert version_calls[0]["config_type"] == "service_setting"
    assert version_calls[0]["comment"] == "update_interval"
    assert response["settings"]["update_interval"]["value"] == 3
    assert response["settings"]["update_interval"]["updated_by"] == "api_client"
    assert response["schema"]["update_interval"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 5,
        "pattern": None,
        "read_only": False,
    }
    mutation_event = next(
        event for event in event_bus.published if getattr(event, "event_type", None) == "mutation.updated"
    )
    assert [change.key for change in mutation_event.payload.changes] == ["update_interval"]
    config_event = next(
        event for event in event_bus.published if isinstance(event, ConfigChangeEvent)
    )
    assert config_event.event_type is ConfigEventType.SERVICE_SETTINGS_CHANGED
    assert config_event.location == "global"
    assert config_event.cluster == "global"
    assert config_event.data == {
        "service": "automation",
        "keys": ["update_interval"],
        "settings": {"update_interval": 3},
    }


@pytest.mark.asyncio
async def test_put_nested_safety_limits_commits_and_emits_flat_changes(monkeypatch):
    limits = {
        "min_temperature": 10,
        "max_temperature": 35,
        "min_humidity": 30,
        "max_humidity": 90,
        "min_co2": 400,
        "max_co2": 2000,
    }
    pool = FakePool()
    pool.values["automation"]["safety_limits"] = dict(limits)
    event_bus = FakeEventBus(pool.events)

    async def load_settings(_pool, service, **kwargs):
        return json.loads(json.dumps(pool.values.get(service, {})))

    async def log_config_version(**kwargs):
        pool.events.append("version")

    async def refresh_service_settings():
        pool.events.append("refresh")

    monkeypatch.setattr(service_settings.ServiceSettings, "load", load_settings)
    monkeypatch.setattr(service_settings, "get_event_bus", lambda: event_bus)
    config = SimpleNamespace(
        service_settings_fallback=lambda: {},
        get_control_config=lambda: dict(pool.values["automation"]),
        refresh_service_settings=refresh_service_settings,
    )

    response = await service_settings.update_service_settings(
        "automation",
        {"safety_limits": {**limits, "max_temperature": 30}},
        database=SimpleNamespace(
            pool=pool,
            config_repo=SimpleNamespace(log_config_version=log_config_version),
        ),
        config=config,
        context=MutationRequestContext(correlation_id=uuid4()),
        sink=event_bus,
    )

    assert pool.values["automation"]["safety_limits"]["max_temperature"] == 30
    assert response["settings"]["safety_limits"]["value"]["max_temperature"] == 30
    mutation_events = [
        event
        for event in event_bus.published
        if getattr(event, "event_type", None) == "mutation.updated"
    ]
    assert len(mutation_events) == 1
    assert [
        (change.key, change.before, change.after) for change in mutation_events[0].payload.changes
    ] == [("safety_limits.max_temperature", 35, 30)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "updates", "status", "code"),
    [
        ("automation", {"update_interval": 0}, 422, "setting_invalid"),
        ("automation", ["not", "an", "object"], 422, "setting_invalid"),
        ("weather", {"room_name": "Other"}, 422, "setting_read_only"),
        ("missing", {}, 404, "unknown_service"),
    ],
)
async def test_put_returns_contract_errors_before_writes(service, updates, status, code):
    with pytest.raises(service_settings.HTTPException) as caught:
        await service_settings.update_service_settings(
            service,
            updates,
            database=SimpleNamespace(pool=None),
            config=SimpleNamespace(service_settings_fallback=lambda: {}),
            context=MutationRequestContext(correlation_id=uuid4()),
        )

    assert caught.value.status_code == status
    assert caught.value.detail["code"] == code
