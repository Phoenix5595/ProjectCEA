from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.middleware.exception_handler import ConflictError, NotFoundError
from app.sensor_registry_models import NewIdentityRequest, SameIdentityRequest
from app.tests.sensor_registry.fakes import FakeConnection


def _registry_row(
    registry_id: int,
    *,
    identity_status: str = "pending",
    hardware_address: int | None = 233,
    pending_since: datetime | None = None,
    last_seen: datetime | None = None,
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "registry_id": registry_id,
        "bus": "rs485",
        "hardware_address": hardware_address,
        "sensor_uid": UUID(int=registry_id),
        "identity_status": identity_status,
        "pending_candidate_registry_id": None,
        "pending_since": pending_since or now,
        "last_seen": last_seen or now,
        "device_id": registry_id + 100,
        "display_name": f"soil_sensor_{hardware_address or 'detached'}",
        "room_id": None,
        "rack_id": None,
        "location_in_room": None,
        "room_name": None,
        "bed_name": None,
        "bed_room_name": None,
        "first_seen": now,
    }


@pytest.mark.asyncio
async def test_new_decision_confirms_pending_record(
    repository, fake_connection: FakeConnection
) -> None:
    row = _registry_row(4, identity_status="confirmed", pending_since=None)
    fake_connection.respond_to(
        lambda query: "SELECT identity_status" in query,
        lambda _query, *_args: {"identity_status": "pending"},
    )
    fake_connection.respond_to(
        lambda query: "UPDATE sensor_registry" in query,
        lambda _query, *_args: None,
    )
    fake_connection.respond_to(
        lambda query: "sr.sensor_uid" in query,
        lambda _query, *_args: row,
    )

    result = await repository.resolve_identity(4, NewIdentityRequest(decision="new"))

    assert result.registry_id == 4
    assert "SET identity_status = 'confirmed'" in fake_connection.statements[1][0]


@pytest.mark.asyncio
async def test_identity_endpoint_rejects_nonpending_record(repository, fake_connection):
    from app.routes.sensor_registry import resolve_sensor_registry_identity

    fake_connection.respond_to(
        lambda query: "SELECT identity_status" in query,
        lambda _query, *_args: {"identity_status": "confirmed"},
    )

    with pytest.raises(ConflictError) as error:
        await resolve_sensor_registry_identity(8, NewIdentityRequest(decision="new"), repository)

    assert error.value.error_code == "identity_not_pending"


@pytest.mark.asyncio
async def test_identity_endpoint_reports_missing_registry_record(repository, fake_connection):
    from app.routes.sensor_registry import resolve_sensor_registry_identity

    fake_connection.respond_to(
        lambda query: "SELECT identity_status" in query,
        lambda _query, *_args: None,
    )

    with pytest.raises(NotFoundError) as error:
        await resolve_sensor_registry_identity(99, NewIdentityRequest(decision="new"), repository)

    assert error.value.error_code == "registry_not_found"


@pytest.mark.asyncio
async def test_same_decision_rejects_ineligible_target(repository, fake_connection):
    now = datetime.now(UTC)
    pending = _registry_row(4, pending_since=now)
    other_pending = _registry_row(5, pending_since=now)
    fake_connection.respond_to(
        lambda query: "FOR UPDATE OF sr" in query,
        lambda _query, *_args: [pending, other_pending],
    )

    with pytest.raises(ConflictError) as error:
        await repository.resolve_identity(
            4, SameIdentityRequest(decision="same", target_registry_id=5)
        )

    assert error.value.error_code == "target_not_eligible"


@pytest.mark.asyncio
async def test_same_decision_blocks_ranges_removed_by_archives(repository, fake_connection):
    now = datetime.now(UTC)
    pending = _registry_row(4, pending_since=now - timedelta(minutes=1))
    detached = _registry_row(
        5,
        identity_status="detached",
        hardware_address=None,
        pending_since=None,
    )
    fake_connection.respond_to(
        lambda query: "FOR UPDATE OF sr" in query,
        lambda _query, *_args: [pending, detached],
    )
    fake_connection.respond_to(
        lambda query: "FROM public.archive_availability" in query,
        lambda _query, *_args: True,
    )

    with pytest.raises(ConflictError) as error:
        await repository.resolve_identity(
            4, SameIdentityRequest(decision="same", target_registry_id=5)
        )

    assert error.value.error_code == "identity_range_archived"
