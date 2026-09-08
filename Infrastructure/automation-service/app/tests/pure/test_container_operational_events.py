from __future__ import annotations

import pytest

from app.container import ServiceContainer
from app.events.mutation_dependencies import NoopOperationalEventSink


class _RedisUnavailable:
    redis_enabled = False


@pytest.mark.asyncio
async def test_operational_events_use_one_noop_sink_when_redis_is_unavailable() -> None:
    # Given: initialized state Redis reports an unavailable connection.
    container = ServiceContainer()
    container.automation_redis = _RedisUnavailable()

    # When: operational runtime composition is attempted.
    await container._compose_operational_events()

    # Then: producers retain one safe no-op sink without a reader or dispatcher.
    assert isinstance(container.get_operational_event_sink(), NoopOperationalEventSink)
    assert container.operational_event_dispatcher is None
    assert container.operational_event_reader is None
