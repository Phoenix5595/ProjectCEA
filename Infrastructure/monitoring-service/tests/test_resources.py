from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from types import SimpleNamespace, TracebackType
from typing import final

import asyncpg
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from typing_extensions import override

import monitoring_service.redis_resources as redis_resources
from monitoring_service.config import Settings
from monitoring_service.database import (
    AcquiredConnection,
    ConnectionLike,
    ReadOnlyDatabase,
    ReadOnlyQueryError,
)
from monitoring_service.redis_resources import LightEffectiveMetadata, RedisReadClient
from monitoring_service.resources import DatabaseResourceSettings, RedisResourceSettings
from monitoring_service.sensor_models import MonitoringUnavailableError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.transactions: list[tuple[str, bool]] = []
        self.queries: list[str] = []

    def transaction(self, *, isolation: str, readonly: bool) -> AbstractAsyncContextManager[None]:
        self.transactions.append((isolation, readonly))
        return FakeTransaction()

    async def fetch(
        self, query: str, *_: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]:
        self.queries.append(query)
        return []


class FakeAcquireContext:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection: ConnectionLike = connection

    async def __aenter__(self) -> ConnectionLike:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        return None


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection: FakeConnection = connection

    def acquire(self, *, timeout: float | None = None) -> AcquiredConnection:
        del timeout
        return FakeAcquireContext(self.connection)

    async def close(self) -> None:
        return None


class FakeRedis:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.keys = keys
        return ["72.5", None]

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def scan_iter(self, *, match: str, count: int) -> AsyncIterator[str]:
        del match, count
        keys: tuple[str, ...] = ()
        for key in keys:
            yield key


@final
class FailingRedis(FakeRedis):
    _error: RedisConnectionError | RedisTimeoutError

    def __init__(self, error: RedisConnectionError | RedisTimeoutError) -> None:
        super().__init__()
        self._error = error

    @override
    async def mget(self, keys: list[str]) -> list[str | None]:
        del keys
        raise self._error

    @override
    async def scan_iter(self, *, match: str, count: int) -> AsyncIterator[str]:
        del match, count
        yield "cea:sensor:Flower Room:back:dry_bulb_b"


class RecordingSensorRedis:
    def __init__(
        self,
        scan_keys: tuple[str, ...],
        values: dict[str, str | None],
    ) -> None:
        self.scan_keys = list(scan_keys)
        self.values = values
        self.scan_count = 0
        self.mget_calls: list[list[str]] = []
        self.scan_error: RedisConnectionError | RedisTimeoutError | None = None

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls.append(keys)
        return [self.values.get(key) for key in keys]

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def scan_iter(self, *, match: str, count: int) -> AsyncIterator[str]:
        del match, count
        self.scan_count += 1
        for key in self.scan_keys:
            yield key
        if self.scan_error is not None:
            raise self.scan_error


@pytest.mark.anyio
async def test_database_rejects_write_sql_before_acquiring_a_connection() -> None:
    # Given: an isolated monitoring database resource
    connection = FakeConnection()
    database = ReadOnlyDatabase(FakePool(connection))

    # When: a write statement is submitted to the resource
    with pytest.raises(ReadOnlyQueryError):
        _ = await database.fetch("INSERT INTO measurement(value) VALUES (1)")

    # Then: no write reaches a connection owned by the read service
    assert connection.queries == []
    assert connection.transactions == []


@pytest.mark.anyio
async def test_database_uses_repeatable_read_only_transaction_for_reads() -> None:
    # Given: an isolated monitoring database resource
    connection = FakeConnection()
    database = ReadOnlyDatabase(FakePool(connection))

    # When: a read query is submitted
    rows = await database.fetch("SELECT 1")

    # Then: the connection can only use a read-only transaction
    assert rows == []
    assert connection.transactions == [("repeatable_read", True)]


@pytest.mark.anyio
async def test_database_connect_configures_bounded_pool_and_statement_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: monitoring-specific resource settings
    captured: dict[str, object] = {}

    async def create_pool(**kwargs: object) -> FakePool:
        captured.update(kwargs)
        return FakePool(FakeConnection())

    monkeypatch.setattr("monitoring_service.database.asyncpg.create_pool", create_pool)
    settings = DatabaseResourceSettings(
        postgres_dsn="postgresql://monitor:secret@localhost/sj_cea",
        pool_size=8,
        acquire_timeout_seconds=10,
        statement_timeout_ms=750,
    )

    # When: the monitoring pool is created
    database = await ReadOnlyDatabase.connect(settings)

    # Then: it has bounded connections and both acquisition/query deadlines
    assert isinstance(database, ReadOnlyDatabase)
    assert captured["min_size"] == 1
    assert captured["max_size"] == 8
    assert captured["timeout"] == 10
    assert captured["command_timeout"] == 0.75
    assert captured["server_settings"] == {
        "application_name": "monitoring-service",
        "default_transaction_read_only": "on",
        "statement_timeout": "750",
    }


@pytest.mark.anyio
async def test_redis_exposes_light_effective_metadata_through_read_operations_only() -> None:
    # Given: a Redis read resource with two known light devices
    transport = FakeRedis()
    client = RedisReadClient(transport)

    # When: published effective light metadata is requested
    metadata = await client.read_light_effective_metadata(
        "Veg Room", "main", ("light-a", "light-b")
    )

    # Then: only existing effective values are returned through MGET
    assert metadata == {"light-a": LightEffectiveMetadata(effective_intensity=72.5)}
    assert transport.keys == [
        "cea:effective_setpoint:Veg Room:main:light:light-a:effective_intensity",
        "cea:effective_setpoint:Veg Room:main:light:light-b:effective_intensity",
    ]
    assert not hasattr(client, "set")
    assert not hasattr(client, "xadd")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [RedisTimeoutError("timed out"), RedisConnectionError("connection lost")],
)
async def test_redis_read_operations_translate_transport_errors(
    error: RedisConnectionError | RedisTimeoutError,
) -> None:
    # Given: a Redis transport whose read operations cannot reach Redis
    client = RedisReadClient(FailingRedis(error))

    # Then: every monitoring read boundary surfaces the typed 503 error
    with pytest.raises(MonitoringUnavailableError):
        _ = await client.mget(["cea:test"])
    with pytest.raises(MonitoringUnavailableError):
        _ = await client.read_light_effective_metadata("Flower Room", "main", ("light-a",))
    with pytest.raises(MonitoringUnavailableError):
        _ = await client.sensor_values("cea:sensor:*")


@pytest.mark.anyio
async def test_redis_sensor_values_caches_names_for_30_seconds_and_mgets_interleaved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a Redis sensor publication and a controlled monotonic clock
    clock = [100.0]
    monkeypatch.setattr(
        redis_resources,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )
    key = "cea:sensor:cache-once:temperature"
    timestamp_key = f"{key}_ts"
    transport = RecordingSensorRedis(
        (key, timestamp_key),
        {key: "21.5", timestamp_key: "2026-10-21T12:00:00"},
    )
    client = RedisReadClient(transport)

    # When: sensor values are read twice before the 30-second cache expires
    first = await client.sensor_values("cea:sensor:cache-once:*")
    clock[0] = 129.999
    transport.values[key] = "22.5"
    second = await client.sensor_values("cea:sensor:cache-once:*")

    # Then: SCAN runs once while each read uses one interleaved MGET
    assert first == ((key, "21.5", "2026-10-21T12:00:00"),)
    assert second == ((key, "22.5", "2026-10-21T12:00:00"),)
    assert transport.scan_count == 1
    assert transport.mget_calls == [[key, timestamp_key], [key, timestamp_key]]


@pytest.mark.anyio
async def test_redis_sensor_values_discovers_added_key_after_cache_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a sensor key cache whose monotonic clock can reach its expiry
    clock = [500.0]
    monkeypatch.setattr(
        redis_resources,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )
    key = "cea:sensor:cache-expiry:temperature"
    new_key = "cea:sensor:cache-expiry:humidity"
    transport = RecordingSensorRedis(
        (key, f"{key}_ts"),
        {
            key: "21.5",
            f"{key}_ts": "2026-10-21T12:00:00",
            new_key: "48.0",
            f"{new_key}_ts": "2026-10-21T12:00:01",
        },
    )
    client = RedisReadClient(transport)

    # When: a key is added after the initial cache reaches its 30-second TTL
    first = await client.sensor_values("cea:sensor:cache-expiry:*")
    transport.scan_keys.extend((new_key, f"{new_key}_ts"))
    clock[0] = 530.0
    second = await client.sensor_values("cea:sensor:cache-expiry:*")

    # Then: the expired cache is replaced by the complete discovered key set
    assert first == ((key, "21.5", "2026-10-21T12:00:00"),)
    assert second == (
        (key, "21.5", "2026-10-21T12:00:00"),
        (new_key, "48.0", "2026-10-21T12:00:01"),
    )
    assert transport.scan_count == 2


@pytest.mark.anyio
async def test_redis_sensor_values_omits_deleted_key_from_current_mget() -> None:
    # Given: a cached sensor key whose publication is deleted between reads
    key = "cea:sensor:deleted:temperature"
    timestamp_key = f"{key}_ts"
    transport = RecordingSensorRedis(
        (key, timestamp_key),
        {key: "21.5", timestamp_key: "2026-10-21T12:00:00"},
    )
    client = RedisReadClient(transport)

    # When: the key is deleted but the cached name remains until expiry
    initial = await client.sensor_values("cea:sensor:deleted:*")
    transport.values[key] = None
    current = await client.sensor_values("cea:sensor:deleted:*")

    # Then: MGET immediately omits the missing publication without another SCAN
    assert initial == ((key, "21.5", "2026-10-21T12:00:00"),)
    assert current == ()
    assert transport.scan_count == 1
    assert transport.mget_calls == [[key, timestamp_key], [key, timestamp_key]]


@pytest.mark.anyio
async def test_redis_sensor_values_translates_scan_errors_without_caching_partial_keys() -> None:
    # Given: a Redis SCAN that fails after returning a partial key listing
    partial_key = "cea:sensor:scan-failure:partial"
    key = "cea:sensor:scan-failure:temperature"
    transport = RecordingSensorRedis(
        (partial_key,),
        {
            partial_key: "invalid",
            key: "21.5",
            f"{key}_ts": "2026-10-21T12:00:00",
        },
    )
    transport.scan_error = RedisTimeoutError("timed out")
    client = RedisReadClient(transport)

    # When: SCAN fails, then succeeds with a complete key listing
    with pytest.raises(MonitoringUnavailableError):
        _ = await client.sensor_values("cea:sensor:scan-failure:*")
    transport.scan_error = None
    transport.scan_keys = [key, f"{key}_ts"]
    current = await client.sensor_values("cea:sensor:scan-failure:*")

    # Then: the failed partial listing was not cached and the next SCAN succeeds
    assert current == ((key, "21.5", "2026-10-21T12:00:00"),)
    assert transport.scan_count == 2


def test_settings_defaults_redis_read_timeout_to_two_seconds() -> None:
    assert Settings().redis_timeout_seconds == 2.0


def test_redis_connect_configures_connect_and_read_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: monitoring-specific Redis timeout settings
    captured: dict[str, object] = {}

    def from_url(*args: object, **kwargs: object) -> FakeRedis:
        captured["url"] = args[0]
        captured.update(kwargs)
        return FakeRedis()

    monkeypatch.setattr("monitoring_service.redis_resources.redis.asyncio.Redis.from_url", from_url)
    settings = RedisResourceSettings(redis_url="redis://localhost:6379/0", timeout_seconds=0.4)

    # When: the Redis read client is created
    client = RedisReadClient.connect(settings)

    # Then: both the connect and read boundaries are bounded
    assert isinstance(client, RedisReadClient)
    assert captured == {
        "url": "redis://localhost:6379/0",
        "decode_responses": True,
        "socket_connect_timeout": 0.4,
        "socket_timeout": 0.4,
    }
