from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
import pytest

from app.schemas.monitoring import (
    AnchorFingerprint,
    ClimateTimelineSeries,
    DeviceTimelinePoint,
    MonitoringRange,
    Origin,
    Phase,
    PhotoperiodTimelinePoint,
    PidTimelinePoint,
    ProjectionMetadata,
    ProjectionRevision,
    Quality,
    TimelineProvenance,
)


def _at(minutes: int = 0) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes)


def _projection() -> ProjectionMetadata:
    return ProjectionMetadata(
        projection_revision=ProjectionRevision("db:3e3d5d5d"),
        anchor_fingerprint=AnchorFingerprint("anchor:76d1cbb4"),
        anchor_observed_at=_at(),
        anchor_quality=Quality.EXACT,
        anchor_valid_until=_at(1),
    )


def test_serializes_orthogonal_provenance() -> None:
    # Given: an estimated, derived aggregated climate projection.
    series = ClimateTimelineSeries(
        name="heating",
        provenance=TimelineProvenance(
            origin=Origin.PROJECTED,
            quality=Quality.ESTIMATED,
            is_aggregated=True,
        ),
        projection=_projection(),
        points=(),
    )

    # When: it crosses the JSON boundary.
    payload = series.model_dump(mode="json")

    # Then: origin, quality, aggregation, and UTC provenance remain distinct.
    assert payload["provenance"] == {
        "origin": "projected",
        "quality": "estimated",
        "is_aggregated": True,
    }
    assert payload["projection"]["anchor_observed_at"] == "2026-01-01T00:00:00Z"


def test_validates_range() -> None:
    # Given: an offset-aware, minimum-duration half-open interval.
    range_ = MonitoringRange(start=_at(), end=_at(5))

    # When: it is constructed and serialized.
    payload = range_.model_dump(mode="json")

    # Then: it is normalized to UTC.
    assert payload == {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:05:00Z"}


@pytest.mark.parametrize(
    "start,end", [(_at(), _at(4)), (_at(), _at(0)), (_at(), _at(7 * 24 * 60 + 1))]
)
def test_validates_range_rejects_invalid_duration(start: datetime, end: datetime) -> None:
    # Given: an out-of-contract interval.
    # When / Then: construction rejects it at the boundary.
    with pytest.raises(ValidationError):
        _ = MonitoringRange(start=start, end=end)


def test_rejects_projected_device_pid() -> None:
    # Given: an unavailable photoperiod record is explicit rather than inferred.
    # When / Then: historical device and PID models reject projected provenance.
    projected = TimelineProvenance(origin=Origin.PROJECTED, quality=Quality.EXACT)
    with pytest.raises(ValidationError):
        _ = DeviceTimelinePoint(
            timestamp=_at(),
            provenance=projected,
            device_name="heater-1",
            device_state=1,
            device_mode="auto",
            control_reason="demand",
        )
    with pytest.raises(ValidationError):
        _ = PidTimelinePoint(timestamp=_at(), provenance=projected, device_name="heater-1")


def test_requires_unknown_photoperiod() -> None:
    # Given: photoperiod data cannot be established.
    # When: the unavailable record is constructed.
    unknown = PhotoperiodTimelinePoint(
        timestamp=_at(),
        phase=Phase.UNKNOWN,
        provenance=TimelineProvenance(origin=Origin.DERIVED, quality=Quality.UNAVAILABLE),
    )

    # Then: UNKNOWN is explicit and a known phase cannot claim unavailable quality.
    assert unknown.phase is Phase.UNKNOWN
    with pytest.raises(ValidationError):
        PhotoperiodTimelinePoint(
            timestamp=_at(),
            phase=Phase.SUN,
            provenance=TimelineProvenance(origin=Origin.DERIVED, quality=Quality.UNAVAILABLE),
        )
