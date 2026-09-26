from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Mapping, Sequence
from uuid import UUID

import httpx
import pytest

from monitoring_service.control_models import (
    ControlHistoryEnvelope,
    ControlHistoryRange,
    ControlPublicationResponse,
    RelayTimelineRange,
    RelayTimelineResponse,
)
from monitoring_service.control_repository import RelayTimelineRepository
from monitoring_service.main import create_app
from monitoring_service.relay_timeline import (
    RELAY_1MIN_PID_LOAD_SQL,
    RELAY_CHANNEL_ANCHORS_SQL,
    RELAY_HEARTBEAT_HISTORY_SQL,
    RELAY_RAW_PID_LOAD_SQL,
    RELAY_TRANSITIONS_AFTER_CURSOR_SQL,
    RELAY_TRANSITIONS_FIRST_PAGE_SQL,
    RELAY_WATERMARK_SQL,
    raw_load_points,
)

START = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
SESSION = UUID("1728e11a-7b38-472a-8d60-65084fb64928")
TRANSITION_FIELDS = {
    "observation_id",
    "observed_at",
    "channel",
    "observed_state",
    "reason",
    "session_id",
    "registry_version",
    "device_id",
    "device_name",
    "device_type",
    "location",
    "cluster",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _observation(
    observation_id: int,
    observed_at: datetime,
    *,
    reason: str = "state_changed",
    channel: int | None = 0,
    observed_state: bool | None = False,
    session_id: UUID = SESSION,
    device_id: int | None = 101,
    device_name: str | None = "heater-a",
    device_type: str | None = "heating",
    location: str | None = "Flower Room",
    cluster: str | None = "main",
) -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "observed_at": observed_at,
        "channel": channel,
        "observed_state": observed_state,
        "reason": reason,
        "session_id": session_id,
        "registry_version": 7,
        "device_id": device_id,
        "device_name": device_name,
        "device_type": device_type,
        "location": location,
        "cluster": cluster,
    }


def _heartbeat(observation_id: int, observed_at: datetime) -> dict[str, object]:
    return _observation(
        observation_id,
        observed_at,
        reason="heartbeat",
        channel=None,
        observed_state=None,
        device_id=None,
        device_name=None,
        device_type=None,
        location=None,
        cluster=None,
    )


def _healthy_database(
    *, duration: timedelta = timedelta(minutes=10), off_at: timedelta | None = timedelta(minutes=5)
) -> FakeRelayDatabase:
    end = START + duration
    facts: list[dict[str, object]] = [
        _observation(0, START - timedelta(seconds=40), reason="initial", observed_state=True),
    ]
    fact_times = [START - timedelta(seconds=20)]
    cursor = START
    while cursor < end:
        fact_times.append(cursor)
        cursor += timedelta(seconds=30)
    facts.extend(
        _heartbeat(0, at)
        for at in fact_times
    )
    if off_at is not None:
        facts.append(
            _observation(
                0,
                START + off_at,
                reason="state_changed",
                observed_state=False,
            )
        )
    facts.sort(key=lambda row: (row["observed_at"], 0 if row["reason"] == "heartbeat" else 1))
    for observation_id, fact in enumerate(facts, start=1):
        fact["observation_id"] = observation_id
    database = FakeRelayDatabase(facts)
    database.coverage = {
        "relevant_channel_count": 1,
        "valid_anchor_count": 1,
        "has_coverage_break": False,
        "has_session_change": False,
        "has_owner_change": False,
    }
    return database


class FakeRelayDatabase:
    """In-memory stand-in that evaluates only the timeline's read-only query shapes."""

    def __init__(self, observations: Sequence[dict[str, object]] = ()) -> None:
        self.observations = list(observations)
        self.raw_samples: list[dict[str, object]] = []
        self.aggregate_samples: list[dict[str, object]] = []
        self.coverage: dict[str, object] = {
            "relevant_channel_count": 0,
            "valid_anchor_count": 0,
            "has_coverage_break": False,
            "has_session_change": False,
            "has_owner_change": False,
        }
        self.queries: list[str] = []
        self.fail = False

    async def fetch(self, query: str, *arguments: object) -> list[dict[str, object]]:
        if self.fail:
            raise RuntimeError("fake read database unavailable")
        self.queries.append(query)
        if query == RELAY_WATERMARK_SQL:
            return [{"watermark": max((int(row["observation_id"]) for row in self.observations), default=0)}]
        if query in {RELAY_TRANSITIONS_FIRST_PAGE_SQL, RELAY_TRANSITIONS_AFTER_CURSOR_SQL}:
            return self._transition_page(query, arguments)
        if "WITH relay_heartbeat_before_range" in query:
            return self._heartbeat_history(arguments)
        if query == RELAY_CHANNEL_ANCHORS_SQL:
            return self._channel_anchors(arguments)
        if "WITH relay_candidate_channels" in query:
            return [self.coverage]
        if query == RELAY_RAW_PID_LOAD_SQL:
            return self._load_rows(arguments, aggregated=False)
        if query == RELAY_1MIN_PID_LOAD_SQL:
            return self._load_rows(arguments, aggregated=True)
        if "monitoring_automation_state_5min" in query:
            return self._load_rows(arguments, aggregated=True)
        raise AssertionError(f"unexpected query: {query[:80]}")

    def _transition_page(
        self, query: str, arguments: tuple[object, ...]
    ) -> list[dict[str, object]]:
        watermark = int(arguments[0])
        start = arguments[1]
        end = arguments[2]
        location = str(arguments[3])
        cursor_at: datetime | None = None
        cursor_id = 0
        if query == RELAY_TRANSITIONS_FIRST_PAGE_SQL:
            page_limit = int(arguments[4])
        else:
            cursor_at = arguments[4]
            cursor_id = int(arguments[5])
            page_limit = int(arguments[6])
        rows: list[dict[str, object]] = []
        for source in self.observations:
            observed_at = source["observed_at"]
            if int(source["observation_id"]) > watermark or not start <= observed_at < end:
                continue
            target = source["location"] == location and source["cluster"] == "main"
            reason = source["reason"]
            visible = (
                reason == "heartbeat"
                or reason in {"stale", "recovered", "recording_gap", "assignment_changed"}
                or (
                    target
                    and source["channel"] is not None
                    and source["device_type"] != "light"
                )
            )
            if not visible:
                continue
            if cursor_at is not None and (observed_at, int(source["observation_id"])) <= (
                cursor_at,
                cursor_id,
            ):
                continue
            row = dict(source)
            if not target and reason != "heartbeat":
                for field in (
                    "observed_state",
                    "device_id",
                    "device_name",
                    "device_type",
                    "location",
                    "cluster",
                ):
                    row[field] = None
            rows.append(row)
        rows.sort(key=lambda row: (row["observed_at"], row["observation_id"]))
        return rows[:page_limit]

    def _heartbeat_history(self, arguments: tuple[object, ...]) -> list[dict[str, object]]:
        watermark, start, end = int(arguments[0]), arguments[1], arguments[2]
        heartbeats = [
            row
            for row in self.observations
            if row["reason"] == "heartbeat" and int(row["observation_id"]) <= watermark
        ]
        before = [row for row in heartbeats if row["observed_at"] < start]
        window = [row for row in heartbeats if start <= row["observed_at"] < end]
        rows = ([max(before, key=lambda row: (row["observed_at"], row["observation_id"]))] if before else [])
        rows.extend(window)
        return [dict(row, window_count=len(window)) for row in sorted(rows, key=lambda item: (item["observed_at"], item["observation_id"]))]

    def _channel_anchors(self, arguments: tuple[object, ...]) -> list[dict[str, object]]:
        start, watermark, location = arguments
        latest: dict[int, dict[str, object]] = {}
        for row in self.observations:
            channel = row["channel"]
            if channel is None or row["observed_at"] >= start or int(row["observation_id"]) > watermark:
                continue
            current = latest.get(int(channel))
            if current is None or (row["observed_at"], row["observation_id"]) > (
                current["observed_at"],
                current["observation_id"],
            ):
                latest[int(channel)] = row
        return [
            latest[channel]
            for channel in sorted(latest)
            if latest[channel]["location"] == location
            and latest[channel]["cluster"] == "main"
            and latest[channel]["device_type"] != "light"
        ]

    def _load_rows(
        self, arguments: tuple[object, ...], *, aggregated: bool
    ) -> list[dict[str, object]]:
        watermark, start, end, location, limit = arguments
        source_rows = self.aggregate_samples if aggregated else self.raw_samples
        associated: list[dict[str, object]] = []
        for sample in source_rows:
            timestamp = sample["timestamp"]
            if not start <= timestamp < end:
                continue
            owners: set[int] = set()
            latest: dict[int, dict[str, object]] = {}
            for row in self.observations:
                channel = row["channel"]
                if (
                    channel is None
                    or row["observed_at"] > timestamp
                    or int(row["observation_id"]) > int(watermark)
                ):
                    continue
                channel = int(channel)
                current = latest.get(channel)
                if current is None or (row["observed_at"], row["observation_id"]) > (
                    current["observed_at"],
                    current["observation_id"],
                ):
                    latest[channel] = row
            for owner in latest.values():
                if (
                    owner["location"] == location
                    and owner["cluster"] == "main"
                    and owner["device_type"] in {"heating", "cooling", "co2"}
                    and owner["device_name"] == sample["device_name"]
                    and isinstance(owner["device_id"], int)
                ):
                    owners.add(owner["device_id"])
            if not owners:
                continue
            owner_count = len(owners)
            value = sample.get("pid_output")
            if aggregated and sample.get("pid_output_count", 1) == 0:
                value = None
            associated.append(
                {
                    "id": sample.get("id", len(associated) + 1),
                    "timestamp": timestamp,
                    "device_name": sample["device_name"],
                    "device_id": next(iter(owners)) if owner_count == 1 else None,
                    "pid_output": value if owner_count == 1 else None,
                    "owner_count": owner_count,
                }
            )
        associated.sort(key=lambda row: (row["timestamp"], row["device_id"] or -1, row["device_name"]))
        total = len(associated)
        return [dict(row, total_count=total) for row in associated[: int(limit)]]


class FakeControlReads:
    def __init__(self, database: FakeRelayDatabase) -> None:
        self.repository = RelayTimelineRepository(database)

    async def relay_timeline(
        self,
        location: str,
        history_range: RelayTimelineRange,
        limit: int,
        cursor: str | None = None,
    ) -> RelayTimelineResponse:
        return await self.repository.read(location, history_range, limit, cursor)

    async def history(
        self,
        location: str,
        history_range: ControlHistoryRange,
        max_points: int | None = None,
    ) -> ControlHistoryEnvelope:
        raise AssertionError("history is not part of this test")

    async def publications(self, location: str) -> ControlPublicationResponse:
        raise AssertionError("publications are not part of this test")


async def _get_timeline(
    database: FakeRelayDatabase,
    *,
    start: datetime = START,
    end: datetime = START + timedelta(minutes=10),
    limit: int = 2_000,
    cursor: str | None = None,
    location: str = "Flower Room",
) -> httpx.Response:
    app = create_app(control_reads=FakeControlReads(database))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        params: dict[str, str | int] = {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = cursor
        return await client.get(
            f"/api/monitoring/control/{location.replace(' ', '%20')}/relay-timeline",
            params=params,
        )


@pytest.mark.anyio
async def test_route_returns_strict_sample_time_transitions_and_predecessor_anchor() -> None:
    database = _healthy_database()
    response = await _get_timeline(database)

    assert response.status_code == 200
    payload = response.json()
    assert payload["range"] == {
        "start": "2026-08-20T12:00:00Z",
        "end": "2026-08-20T12:10:00Z",
    }
    assert payload["coverage_complete"] is True
    assert payload["last_heartbeat_at"] == "2026-08-20T12:09:30Z"
    assert {"channel": 0, "observed_state": True}.items() <= payload["anchors"][0].items()
    off = [row for row in payload["transitions"] if row["reason"] == "state_changed"]
    assert [(row["channel"], row["observed_state"]) for row in off] == [(0, False)]
    assert set(payload["transitions"][0]) == TRANSITION_FIELDS
    route = next(
        route
        for route in create_app(control_reads=FakeControlReads(database)).routes
        if getattr(route, "path", None)
        == "/api/monitoring/control/{location}/relay-timeline"
    )
    assert route.methods == {"GET"}


@pytest.mark.anyio
async def test_heartbeat_outage_marks_coverage_incomplete() -> None:
    database = _healthy_database()
    database.observations = [
        row
        for row in database.observations
        if not (
            row["reason"] == "heartbeat"
            and START + timedelta(minutes=3) <= row["observed_at"] <= START + timedelta(minutes=4)
        )
    ]
    response = await _get_timeline(database)

    assert response.status_code == 200
    assert response.json()["coverage_complete"] is False


@pytest.mark.anyio
async def test_raw_pid_output_is_percent_and_owner_changes_do_not_leak_predecessor() -> None:
    database = _healthy_database(off_at=None)
    assignment_at = START + timedelta(minutes=4)
    database.observations.append(
        _observation(
            max(int(row["observation_id"]) for row in database.observations) + 1,
            assignment_at,
            reason="assignment_changed",
            observed_state=True,
            device_id=202,
            device_name="heater-b",
            device_type="heating",
        )
    )
    database.observations.sort(key=lambda row: (row["observed_at"], row["observation_id"]))
    database.coverage["has_coverage_break"] = True
    database.coverage["has_owner_change"] = True
    database.raw_samples = [
        {"id": 1, "timestamp": START + timedelta(minutes=1), "device_name": "heater-a", "pid_output": 0.0},
        {"id": 2, "timestamp": START + timedelta(minutes=2), "device_name": "heater-a", "pid_output": 0.5},
        {"id": 3, "timestamp": START + timedelta(minutes=3), "device_name": "heater-a", "pid_output": None},
        {"id": 4, "timestamp": START + timedelta(minutes=5), "device_name": "heater-a", "pid_output": 0.9},
        {"id": 5, "timestamp": START + timedelta(minutes=5), "device_name": "heater-b", "pid_output": 1.0},
    ]

    response = await _get_timeline(database)

    assert response.status_code == 200
    load = response.json()["load"]
    assert [(row["device_id"], row["requested_percent"]) for row in load] == [
        (101, 0.0),
        (101, 50.0),
        (101, None),
        (202, 100.0),
    ]
    assert all(row["aggregated"] is False for row in load)
    assert response.json()["coverage_complete"] is False
    assert "LEAD(observed_at)" in RELAY_RAW_PID_LOAD_SQL


@pytest.mark.anyio
async def test_cursor_pages_are_stable_ordered_and_do_not_repeat_anchors_or_load() -> None:
    database = _healthy_database()
    repository = RelayTimelineRepository(database)
    history_range = RelayTimelineRange(start=START, end=START + timedelta(minutes=10))
    page = await repository.read("Flower Room", history_range, limit=3)
    assert page.has_more is True
    assert page.anchors
    assert page.load == ()

    received = list(page.transitions)
    watermark = page.watermark
    cursor = page.next_cursor
    late_insert = _observation(
        watermark + 1,
        history_range.end - timedelta(seconds=1),
        reason="state_changed",
        observed_state=True,
    )
    database.observations.append(late_insert)
    while cursor is not None:
        next_page = await repository.read("Flower Room", history_range, limit=3, cursor=cursor)
        assert next_page.watermark == watermark
        assert next_page.anchors == ()
        assert next_page.load == ()
        received.extend(next_page.transitions)
        cursor = next_page.next_cursor

    expected = [
        row
        for row in database.observations
        if int(row["observation_id"]) <= watermark
        and row["observed_at"] >= START
        and row["observed_at"] < history_range.end
        and (
            row["reason"] == "heartbeat"
            or row["reason"] in {"stale", "recovered", "recording_gap", "assignment_changed"}
            or (row["location"] == "Flower Room" and row["device_type"] != "light")
        )
    ]
    expected.sort(key=lambda row: (row["observed_at"], row["observation_id"]))
    assert [(row.observed_at, row.observation_id) for row in received] == [
        (row["observed_at"], row["observation_id"]) for row in expected
    ]
    assert len({row.observation_id for row in received}) == len(received)
    assert sum(query == RELAY_CHANNEL_ANCHORS_SQL for query in database.queries) == 1
    assert sum(query == RELAY_RAW_PID_LOAD_SQL for query in database.queries) == 1
    assert all(query == RELAY_TRANSITIONS_FIRST_PAGE_SQL or query == RELAY_TRANSITIONS_AFTER_CURSOR_SQL or query in {
        RELAY_WATERMARK_SQL,
        RELAY_HEARTBEAT_HISTORY_SQL,
        RELAY_CHANNEL_ANCHORS_SQL,
        RELAY_RAW_PID_LOAD_SQL,
    } or "relay_candidate_channels" in query for query in database.queries)


@pytest.mark.anyio
async def test_coarse_pid_load_does_not_replace_exact_physical_transitions() -> None:
    end = START + timedelta(hours=2)
    database = _healthy_database(duration=timedelta(hours=2), off_at=timedelta(minutes=30))
    database.aggregate_samples = [
        {
            "timestamp": START + timedelta(minutes=1),
            "device_name": "heater-a",
            "pid_output": 0.5,
            "pid_output_count": 1,
        }
    ]
    repository = RelayTimelineRepository(database)
    response = await repository.read(
        "Flower Room", RelayTimelineRange(start=START, end=end), limit=2000
    )

    physical = [row for row in response.transitions if row.reason == "state_changed"]
    assert [(row.observed_at, row.observed_state) for row in physical] == [
        (START + timedelta(minutes=30), False)
    ]
    assert len(response.load) == 1
    assert response.load[0].aggregated is True
    assert response.load[0].interval_seconds == 60
    assert response.load[0].requested_percent == 50.0
    assert any("monitoring_automation_state_1min" in query for query in database.queries)


@pytest.mark.anyio
async def test_24_hour_range_uses_five_minute_output_aggregates_only() -> None:
    end = START + timedelta(hours=24)
    database = _healthy_database(duration=timedelta(hours=24), off_at=timedelta(hours=1))
    database.aggregate_samples = [
        {
            "timestamp": START + timedelta(minutes=5),
            "device_name": "heater-a",
            "pid_output": 0.25,
            "pid_output_count": 1,
        }
    ]

    response = await RelayTimelineRepository(database).read(
        "Flower Room", RelayTimelineRange(start=START, end=end), limit=2_000
    )

    assert response.load[0].aggregated is True
    assert response.load[0].interval_seconds == 300
    assert response.load[0].requested_percent == 25.0
    assert any("monitoring_automation_state_5min" in query for query in database.queries)


@pytest.mark.anyio
async def test_route_rejects_bad_cursors_unknown_rooms_and_database_outage() -> None:
    database = _healthy_database()
    first = await _get_timeline(database, limit=1)
    cursor = first.json()["next_cursor"]
    assert cursor

    mismatched = await _get_timeline(
        database,
        start=START,
        end=START + timedelta(minutes=11),
        limit=1,
        cursor=cursor,
    )
    malformed = await _get_timeline(database, cursor="not-a-cursor")
    unknown = await _get_timeline(database, location="Unknown Room")
    unavailable_db = _healthy_database()
    unavailable_db.fail = True
    unavailable = await _get_timeline(unavailable_db)

    assert mismatched.status_code == 400
    assert malformed.status_code == 400
    assert unknown.status_code == 404
    assert unavailable.status_code == 503


@pytest.mark.anyio
async def test_route_rejects_naive_and_out_of_bounds_ranges_and_limits() -> None:
    database = _healthy_database()
    app = create_app(control_reads=FakeControlReads(database))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        naive = await client.get(
            "/api/monitoring/control/Flower%20Room/relay-timeline",
            params={"start": "2026-08-20T12:00:00", "end": "2026-08-20T12:10:00Z"},
        )
        too_short = await client.get(
            "/api/monitoring/control/Flower%20Room/relay-timeline",
            params={"start": "2026-08-20T12:00:00Z", "end": "2026-08-20T12:04:59Z"},
        )
        too_large = await client.get(
            "/api/monitoring/control/Flower%20Room/relay-timeline",
            params={
                "start": "2026-08-20T12:00:00Z",
                "end": "2026-08-20T12:10:00Z",
                "limit": "2001",
            },
        )
        too_long = await client.get(
            "/api/monitoring/control/Flower%20Room/relay-timeline",
            params={
                "start": "2026-08-13T12:00:00Z",
                "end": "2026-08-20T12:00:01Z",
            },
        )

    assert naive.status_code == 400
    assert too_short.status_code == 400
    assert too_large.status_code == 400
    assert too_long.status_code == 400


def test_raw_pid_downsampling_keeps_extrema_and_null_gap_edges() -> None:
    rows = [
        {
            "id": index,
            "timestamp": START + timedelta(seconds=index),
            "device_name": "heater-a",
            "device_id": 101,
            "pid_output": 0.25,
            "owner_count": 1,
            "total_count": 2_505,
        }
        for index in range(2_505)
    ]
    rows[10]["pid_output"] = 0.0
    rows[1_200]["pid_output"] = 1.0
    for index in range(1_500, 1_503):
        rows[index]["pid_output"] = None
    rows[-1]["pid_output"] = 0.75

    points, truncated = raw_load_points(rows)

    assert truncated is False
    assert len(points) < len(rows)
    assert points[0].timestamp == START
    assert points[-1].timestamp == START + timedelta(seconds=2_504)
    assert {point.requested_percent for point in points} >= {0.0, 100.0}
    null_times = {
        point.timestamp for point in points if point.requested_percent is None
    }
    assert {
        START + timedelta(seconds=1_500),
        START + timedelta(seconds=1_502),
    } <= null_times
    assert null_times <= {
        START + timedelta(seconds=1_500),
        START + timedelta(seconds=1_501),
        START + timedelta(seconds=1_502),
    }
