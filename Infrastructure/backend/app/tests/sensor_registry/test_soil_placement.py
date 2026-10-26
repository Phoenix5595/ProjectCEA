from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
import pytest

from app.middleware.exception_handler import ConflictError, NotFoundError
from app.repositories.soil_placement_repository import SoilPlacementRepositoryMixin
from app.sensor_registry_models import Rs485AssignmentRequest, SoilBed
from app.tests.sensor_registry.fakes import FakeConnection

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _record_row(registry_id: int, bus: str = "rs485", **assignment: object) -> dict[str, object]:
    row: dict[str, object] = {
        "registry_id": registry_id,
        "bus": bus,
        "hardware_address": registry_id,
        "sensor_uid": UUID(int=registry_id),
        "identity_status": "confirmed",
        "pending_candidate_registry_id": None,
        "pending_since": None,
        "display_name": f"soil_sensor_{registry_id}",
        "first_seen": NOW,
        "last_seen": NOW,
        "room_id": None,
        "rack_id": None,
        "location_in_room": None,
        "room_name": None,
        "bed_name": None,
        "bed_room_name": None,
        "x_frac": None,
        "y_frac": None,
    }
    row.update(assignment)
    return row


def _script_assignment(conn: FakeConnection, record: dict[str, object]) -> None:
    conn.respond_to(
        lambda q: "FROM sensor_registry" in q and "FOR UPDATE" in q,
        lambda _q, *_a: {
            "registry_id": 13,
            "bus": "rs485",
            "room_id": None,
            "rack_id": None,
            "location_in_room": None,
            "device_id": 113,
        },
    )
    conn.respond_to(lambda q: "FOR UPDATE OF r" in q, lambda _q, *_a: {"rack_id": 1, "room_id": 1})
    conn.respond_to(lambda q: "SELECT room_id FROM room" in q, lambda _q, *_a: 2)
    conn.respond_to(lambda q: "clock_timestamp()" in q and "SELECT" in q, lambda _q, *_a: NOW)
    conn.respond_to(lambda q: q.lstrip().startswith(("UPDATE", "INSERT")), lambda _q, *_a: None)
    conn.respond_to(lambda q: "sr.sensor_uid" in q, lambda _q, *_a: record)


def _mutations(conn: FakeConnection) -> list[tuple[str, tuple[object, ...]]]:
    return [
        (query, args)
        for query, args in conn.statements
        if query.lstrip().startswith(("UPDATE", "INSERT"))
    ]


def test_placement_body_shapes() -> None:
    flower = Rs485AssignmentRequest(
        kind="rs485", room="Flower Room", bed="Back Bed", x_frac=0.25, y_frac=0.75
    )
    assert (flower.bed, flower.x_frac, flower.y_frac) == ("Back Bed", 0.25, 0.75)
    with pytest.raises(ValidationError, match="requires bed, x_frac and y_frac"):
        Rs485AssignmentRequest(kind="rs485", room="Flower Room", bed="Back Bed", x_frac=0.2)
    with pytest.raises(ValidationError, match="room-level"):
        Rs485AssignmentRequest(kind="rs485", room="Veg Room", bed="Front Bed")
    with pytest.raises(ValidationError):
        Rs485AssignmentRequest(
            kind="rs485", room="Flower Room", bed="Back Bed", x_frac=1.2, y_frac=0.5
        )


@pytest.mark.asyncio
async def test_flower_placement_closes_inserts_then_mirrors_with_one_timestamp(
    repository, fake_connection: FakeConnection
) -> None:
    record = _record_row(
        13,
        rack_id=1,
        bed_name="Back Bed",
        bed_room_name="Flower Room",
        x_frac=Decimal("0.250"),
        y_frac=Decimal("0.750"),
    )
    _script_assignment(fake_connection, record)

    result = await repository.assign(
        13,
        Rs485AssignmentRequest(
            kind="rs485", room="Flower Room", bed="Back Bed", x_frac=0.25, y_frac=0.75
        ),
    )

    (close_q, close_a), (insert_q, insert_a), (registry_q, registry_a), (device_q, device_a) = (
        _mutations(fake_connection)
    )
    assert "UPDATE soil_probe_placement" in close_q and "'moved'" in close_q
    assert close_a == (13, NOW)
    assert "INSERT INTO soil_probe_placement" in insert_q
    assert insert_a == (13, 1, 1, 0.25, 0.75, NOW)
    assert "UPDATE sensor_registry" in registry_q and registry_a == (13, 1, None)
    assert "UPDATE device" in device_q and device_a == (113, 1)
    assert result.assignment is not None
    assert result.assignment.model_dump() == {
        "kind": "rs485",
        "room": "Flower Room",
        "bed": "Back Bed",
        "x_frac": 0.25,
        "y_frac": 0.75,
    }


@pytest.mark.asyncio
async def test_veg_placement_is_room_level(repository, fake_connection: FakeConnection) -> None:
    record = _record_row(13, room_id=2, room_name="Veg Room")
    _script_assignment(fake_connection, record)

    result = await repository.assign(13, Rs485AssignmentRequest(kind="rs485", room="Veg Room"))

    _close, (_iq, insert_a), (_rq, registry_a), (_dq, device_a) = _mutations(fake_connection)
    assert insert_a == (13, 2, None, None, None, NOW)
    assert registry_a == (13, None, 2)
    assert device_a == (113, None)
    assert result.assignment is not None
    assert result.assignment.model_dump() == {
        "kind": "rs485",
        "room": "Veg Room",
        "bed": None,
        "x_frac": None,
        "y_frac": None,
    }


@pytest.mark.asyncio
async def test_unassign_rejects_can_and_unknown(
    repository, fake_connection: FakeConnection
) -> None:
    rows = {7: {"registry_id": 7, "bus": "can", "device_id": 70}, 8: None}
    fake_connection.respond_to(lambda q: "FOR UPDATE" in q, lambda _q, rid: rows[rid])

    with pytest.raises(ConflictError) as conflict:
        await repository.unassign(7)
    assert conflict.value.error_code == "not_rs485"
    with pytest.raises(NotFoundError):
        await repository.unassign(8)
    assert _mutations(fake_connection) == []


class _FakeRedis:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def get(self, _key: str) -> str | None:
        return self.value


@pytest.mark.asyncio
async def test_bus_status_unknown_without_client_or_key_and_parsed_when_valid(
    repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.redis_client as redis_module

    clients: list[_FakeRedis | None] = [
        None,
        _FakeRedis(None),
        _FakeRedis("not json"),
        _FakeRedis('{"state": "power_cycle_required", "address": 3, "at": "2026-10-26T12:00:00Z"}'),
    ]

    async def fake_client() -> _FakeRedis | None:
        return clients.pop(0)

    monkeypatch.setattr(redis_module, "get_redis_client", fake_client)

    for _ in range(3):
        status = await repository.soil_bus_status()
        assert (status.state, status.address, status.at) == ("unknown", None, None)
    status = await repository.soil_bus_status()
    assert (status.state, status.address, status.at) == ("power_cycle_required", 3, NOW)


def test_heatmap_rows_drop_missing_values_and_keep_bed_order() -> None:
    beds = [
        SoilBed(rack_id=1, name="Back Bed", width_ft=4, length_ft=4),
        SoilBed(rack_id=5, name="Front Bed", width_ft=4, length_ft=4),
    ]
    rows = [
        {
            "registry_id": 15,
            "hardware_address": 5,
            "rack_id": 5,
            "x_frac": Decimal("0.600"),
            "y_frac": Decimal("0.500"),
            "removed_at": None,
            "value": Decimal("28.4"),
        },
        {
            "registry_id": 16,
            "hardware_address": 6,
            "rack_id": 1,
            "x_frac": Decimal("0.700"),
            "y_frac": Decimal("0.700"),
            "removed_at": NOW,
            "value": None,
        },
    ]

    result = SoilPlacementRepositoryMixin._heatmap_beds(beds, rows)

    assert [bed.name for bed in result] == ["Back Bed", "Front Bed"]
    assert result[0].points == []
    (point,) = result[1].points
    assert (point.hardware_address, point.x_frac, point.value, point.active) == (5, 0.6, 28.4, True)
