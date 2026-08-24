from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
import pytest

from shared.monitoring_contracts import (
    ConfigVersion,
    CurrentSeriesPoint,
    CurrentSnapshot,
    FutureProjection,
    MonitoringPublication,
    PersistenceCursor,
    PersistenceState,
    Photoperiod,
    PhotoperiodPhase,
    ProjectionRevision,
    ProjectionSeriesPoint,
    PublicationVersion,
    Quality,
    SemanticSeriesId,
)

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
VERSION = PublicationVersion(
    contract_version=1,
    config_version=ConfigVersion(7),
    revision=ProjectionRevision("8f8c3db"),
)


def test_publication_accepts_aligned_current_snapshot_and_future_projection() -> None:
    # Given: authoritative current and future facts produced from one configuration revision.
    snapshot = CurrentSnapshot(
        version=VERSION,
        observed_at=NOW,
        valid_until=LATER,
        series=(
            CurrentSeriesPoint(
                series_id=SemanticSeriesId(value="climate.air_temperature_setpoint"),
                value=24.0,
                quality=Quality.EXACT,
                observed_at=NOW,
                valid_until=LATER,
            ),
        ),
        photoperiod=Photoperiod(
            phase=PhotoperiodPhase.SUN,
            quality=Quality.EXACT,
            observed_at=NOW,
            valid_until=LATER,
        ),
        persistence=PersistenceCursor(
            state=PersistenceState.PERSISTED,
            cursor="171",
            persisted_at=NOW,
        ),
    )
    projection = FutureProjection(
        version=VERSION,
        generated_at=NOW,
        valid_from=LATER,
        valid_until=LATER + timedelta(hours=1),
        series=(
            ProjectionSeriesPoint(
                series_id=SemanticSeriesId(value="light.intensity_percent"),
                value=80.0,
                quality=Quality.ESTIMATED,
                valid_from=LATER,
                valid_until=LATER + timedelta(hours=1),
            ),
        ),
    )

    # When: automation publishes the complete monitoring fact envelope.
    publication = MonitoringPublication(current=snapshot, future=(projection,))

    # Then: the publication preserves immutable, semantically identified facts.
    assert publication.current.series[0].series_id.value == "climate.air_temperature_setpoint"
    with pytest.raises(ValidationError):
        PublicationVersion.__setattr__(
            publication.current.version, "config_version", ConfigVersion(8)
        )


def test_publication_rejects_mixed_configuration_versions() -> None:
    # Given: a current snapshot and projection constructed from different configurations.
    snapshot = CurrentSnapshot(
        version=VERSION,
        observed_at=NOW,
        valid_until=LATER,
        series=(),
        photoperiod=None,
        persistence=PersistenceCursor(state=PersistenceState.PENDING),
    )
    projection = FutureProjection(
        version=PublicationVersion(
            contract_version=1,
            config_version=ConfigVersion(8),
            revision=ProjectionRevision("3b0d1ab"),
        ),
        generated_at=NOW,
        valid_from=LATER,
        valid_until=LATER + timedelta(hours=1),
        series=(),
    )

    # When: the envelope is parsed at its publication boundary.
    # Then: the mismatch is rejected rather than silently combining facts.
    with pytest.raises(ValidationError, match="version"):
        _ = MonitoringPublication(current=snapshot, future=(projection,))


def test_current_series_point_rejects_naive_timestamp_and_unavailable_value() -> None:
    # Given: a series fact carrying a naive timestamp or unavailable value.
    naive_time = datetime(2026, 8, 20, 12)

    # When / Then: boundary parsing fails loud for both illegal states.
    with pytest.raises(ValidationError):
        _ = CurrentSeriesPoint(
            series_id=SemanticSeriesId(value="climate.air_temperature_setpoint"),
            value=24.0,
            quality=Quality.EXACT,
            observed_at=naive_time,
            valid_until=LATER,
        )
    with pytest.raises(ValidationError, match="unavailable"):
        _ = CurrentSeriesPoint(
            series_id=SemanticSeriesId(value="climate.air_temperature_setpoint"),
            value=24.0,
            quality=Quality.UNAVAILABLE,
            observed_at=NOW,
            valid_until=LATER,
        )


def test_projection_and_persistence_reject_illegal_quality_and_state_combinations() -> None:
    # Given: a future fact claimed exact and a pending cursor claimed persisted.
    # When / Then: publication parsing refuses both contradictory combinations.
    with pytest.raises(ValidationError, match="estimated"):
        _ = ProjectionSeriesPoint(
            series_id=SemanticSeriesId(value="light.intensity_percent"),
            value=80.0,
            quality=Quality.EXACT,
            valid_from=LATER,
            valid_until=LATER + timedelta(hours=1),
        )
    with pytest.raises(ValidationError, match="pending"):
        _ = PersistenceCursor(state=PersistenceState.PENDING, cursor="171")


def test_photoperiod_rejects_unknown_exact_state() -> None:
    # Given: an unresolved photoperiod represented as an exact fact.
    # When / Then: parsing rejects the impossible confidence/phase pair.
    with pytest.raises(ValidationError, match="unknown"):
        _ = Photoperiod(
            phase=PhotoperiodPhase.UNKNOWN,
            quality=Quality.EXACT,
            observed_at=NOW,
            valid_until=LATER,
        )
