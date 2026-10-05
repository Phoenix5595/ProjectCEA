from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import anyio
import redis

from app.control.current_publication_observer import build_current_snapshot
from app.monitoring_publication.current import CurrentPublicationPublisher
from app.redis.monitoring import RedisCurrentPublicationWriter
from app.repositories.monitoring_snapshot_sources import ConfigVersionSnapshotSource
from shared.monitoring_contracts import (
    ConfigVersion,
    CurrentSeriesPoint,
    CurrentSnapshot,
    FutureProjection,
    PersistenceCursor,
    PersistenceState,
    ProjectionRevision,
    ProjectionSeriesPoint,
    PublicationVersion,
    Quality,
    SemanticSeriesId,
    PhotoperiodPhase,
)

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


class RecordingWriter:
    def __init__(self, outcomes: list[bool] | None = None) -> None:
        self.outcomes: list[bool] = outcomes or []
        self.snapshots: list[CurrentSnapshot] = []

    def write_current(self, location: str, snapshot: CurrentSnapshot) -> bool:
        del location
        self.snapshots.append(snapshot)
        return self.outcomes.pop(0) if self.outcomes else True


class RecordingRedis:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, str]] = []

    def set(self, key: str, value: str) -> bool:
        self.set_calls.append((key, value))
        return True


class FailingRedis:
    def set(self, key: str, value: str) -> bool:
        del key, value
        raise redis.RedisError


def snapshot(value: float, observed_at: datetime) -> CurrentSnapshot:
    return CurrentSnapshot(
        version=PublicationVersion(
            contract_version=1,
            config_version=ConfigVersion(7),
            revision=ProjectionRevision("8f8c3db"),
        ),
        observed_at=observed_at,
        valid_until=observed_at + timedelta(seconds=5),
        series=(
            CurrentSeriesPoint(
                series_id=SemanticSeriesId(value="climate.air_temperature_setpoint"),
                value=value,
                quality=Quality.EXACT,
                observed_at=observed_at,
                valid_until=observed_at + timedelta(seconds=5),
            ),
        ),
        photoperiod=None,
        persistence=PersistenceCursor(state=PersistenceState.PENDING),
    )


def test_current_publication_keeps_latest_snapshot_without_touching_redis_on_enqueue() -> None:
    # Given: a bounded publisher and two complete current snapshots.
    writer = RecordingWriter()
    publisher = CurrentPublicationPublisher("Veg Room", writer)
    first = snapshot(22.0, NOW)
    latest = snapshot(23.0, NOW + timedelta(seconds=1))

    # When: consecutive control ticks hand off snapshots before the worker flushes.
    publisher.enqueue(first)
    publisher.enqueue(latest)

    # Then: no control-tick Redis operation occurs and only the latest snapshot is published.
    assert writer.snapshots == []
    published = anyio.run(publisher.flush_once)
    assert writer.snapshots == [latest]
    assert publisher.health.replaced_snapshots == 1
    assert published is True


def test_current_publication_preserves_normalized_active_profile_facts() -> None:
    # Given: a metadata-only active profile whose room identifiers need normalization.
    redis = RecordingRedis()
    publisher = CurrentPublicationPublisher(
        "1 Flower Room", RedisCurrentPublicationWriter(redis)
    )
    room = ("1 Flower Room", "02 Main")
    current = build_current_snapshot(
        effective_setpoints={},
        automation_context={},
        relay_states={},
        photoperiod_phases={room: PhotoperiodPhase.MOON},
        runtime_snapshot_version=7,
        observed_at=NOW,
        active_profiles={room: {"mode_id": 6, "submode_id": 3}},
        ramp_remaining_seconds={},
    )
    assert current is not None

    # When: the asynchronous publisher writes the current contract.
    publisher.offer(current)
    assert anyio.run(publisher.flush_once) is True

    # Then: normalized series identities are retained while the external Redis key stays raw.
    key, payload = redis.set_calls[0]
    assert key == "cea:monitoring:current:1 Flower Room"
    assert {point["series_id"]["value"] for point in json.loads(payload)["series"]} == {
        "v_1_flower_room.v_02_main.setpoint.profile_mode_id",
        "v_1_flower_room.v_02_main.setpoint.profile_submode_id",
    }


def test_current_publication_uses_persisted_config_cursor_for_the_written_envelope() -> None:
    writer = RecordingWriter()
    publisher = CurrentPublicationPublisher(
        "Veg Room", writer, config_version=lambda: _persisted_config_version()
    )
    publisher.enqueue(snapshot(22.0, NOW))

    assert anyio.run(publisher.flush_once) is True
    assert writer.snapshots[0].version.config_version == ConfigVersion(19)


async def _persisted_config_version() -> int:
    return 19


def test_config_version_snapshot_source_reads_persisted_cursor() -> None:
    async def persisted_version() -> int:
        return 23

    source = ConfigVersionSnapshotSource(persisted_version)

    assert anyio.run(source.read_source_versions, "Veg Room", "main") == (("configuration", 23),)


def test_current_publication_records_failed_background_write_without_requeueing_control_work() -> (
    None
):
    # Given: Redis refuses a complete snapshot after a control tick has returned.
    writer = RecordingWriter([False])
    publisher = CurrentPublicationPublisher("Veg Room", writer)
    current = snapshot(22.0, NOW)

    # When: the independent flush worker attempts the handoff.
    publisher.enqueue(current)
    published = anyio.run(publisher.flush_once)

    # Then: the failure is publication health only, with no queued retry that can affect control.
    assert writer.snapshots == [current]
    assert publisher.health.failed_publications == 1
    assert publisher.health.pending is False
    assert published is False


def test_redis_current_publication_writes_one_contract_json_with_original_timestamps() -> None:
    # Given: a validated current snapshot created at its original control observation time.
    redis = RecordingRedis()
    writer = RedisCurrentPublicationWriter(redis)
    current = snapshot(22.0, NOW)

    # When: the independent publisher persists the current facts for its location.
    published = writer.write_current("Veg Room", current)

    # Then: one atomic SET holds the versioned contract and unmodified observation timestamp.
    assert published is True
    assert len(redis.set_calls) == 1
    key, payload = redis.set_calls[0]
    assert key == "cea:monitoring:current:Veg Room"
    assert json.loads(payload)["observed_at"] == "2026-08-20T12:00:00Z"


def test_redis_future_publication_replaces_one_complete_contract_array() -> None:
    # Given: two validated intervals for one versioned room projection.
    redis = RecordingRedis()
    writer = RedisCurrentPublicationWriter(redis)
    version = PublicationVersion(
        contract_version=1,
        config_version=ConfigVersion(7),
        revision=ProjectionRevision("8f8c3db"),
    )
    projections = tuple(
        FutureProjection(
            version=version,
            generated_at=NOW,
            valid_from=NOW + timedelta(hours=index),
            valid_until=NOW + timedelta(hours=index + 1),
            series=(
                ProjectionSeriesPoint(
                    series_id=SemanticSeriesId(value="climate.heating_setpoint_target"),
                    value=22.0,
                    quality=Quality.ESTIMATED,
                    valid_from=NOW + timedelta(hours=index),
                    valid_until=NOW + timedelta(hours=index + 1),
                ),
            ),
        )
        for index in range(2)
    )

    # When: the independent publisher persists the future timeline.
    published = writer.write_future("Veg Room", projections)

    # Then: exactly one canonical key holds the whole array, never an interval fragment.
    assert published is True
    assert redis.set_calls == [
        (
            "cea:monitoring:future:Veg Room",
            json.dumps(
                [item.model_dump(mode="json") for item in projections], separators=(",", ":")
            ),
        )
    ]


def test_redis_future_publication_returns_false_on_redis_error() -> None:
    writer = RedisCurrentPublicationWriter(FailingRedis())

    assert writer.write_future("Veg Room", ()) is False
