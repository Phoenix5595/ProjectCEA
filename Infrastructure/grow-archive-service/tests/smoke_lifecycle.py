"""Loopback API proof for Calendar grow membership and explicit lifecycle routes.

The shell harness supplies a marked monitoring_test_* database and this script
starts only owned loopback Redis/HTTP resources. Calendar routes are production
router code backed by the real DatabaseManager and CalendarRepository; the
archive app runs its real lifespan and owns a separate service pool. No
hardware/control service or external network destination is initialized.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

HERE = Path(__file__).resolve().parent
SERVICE_ROOT = HERE.parent
INFRASTRUCTURE = SERVICE_ROOT.parent
REPOSITORY_ROOT = INFRASTRUCTURE.parent
AUTOMATION_ROOT = INFRASTRUCTURE / "automation-service"
MONITORING_ROOT = INFRASTRUCTURE / "monitoring-service"
for _entry in (str(AUTOMATION_ROOT), str(INFRASTRUCTURE), str(MONITORING_ROOT), str(SERVICE_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import redis  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.database import DatabaseManager  # noqa: E402
from app.events.mutation_dependencies import (  # noqa: E402
    get_mutation_event_sink,
)
from app.repositories.calendar import CalendarRepository  # noqa: E402
from app.routes.calendar import get_database as calendar_get_database  # noqa: E402
from app.routes.calendar import router as calendar_router  # noqa: E402
from grow_archive_service.main import create_app as create_archive_app  # noqa: E402
from shared.auth import APIKeyAuthMiddleware  # noqa: E402
from shared.grow_lifecycle import calendar_windows, toronto_midnight  # noqa: E402
from shared.monitoring_contracts import CurrentSnapshot, PersistenceState  # noqa: E402
from shared.redis_keys import monitoring_current_publication_key  # noqa: E402
from smoke_scope import assert_scope_closed_guard, exercise_scope_corrections  # noqa: E402


class SmokeFailure(AssertionError):
    """An observable acceptance condition failed in the disposable fixture."""


class CapturingOperationalEventSink:
    """Collect real route emissions without initializing the production bus."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit_nowait(self, event: Any) -> None:
        self.events.append(event)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _parse_database_url(value: str) -> dict[str, Any]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise SmokeFailure("database URL must use PostgreSQL")
    host = (parsed.hostname or "").lower()
    expected_host = os.environ.get("CEA_TEST_PGHOST", "127.0.0.1").lower()
    if host != expected_host:
        raise SmokeFailure(f"database URL host does not match CEA_TEST_PGHOST: {host!r}")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SmokeFailure(f"refusing non-loopback database host {host!r}")
    for marker in ("mothernode", "projectcea"):
        if marker in host:
            raise SmokeFailure(f"refusing production-looking database host {host!r}")
    database = unquote(parsed.path.removeprefix("/"))
    lowered = database.lower()
    if any(name in lowered for name in ("cea_sensors", "sj_cea")):
        raise SmokeFailure("refusing a protected production database name")
    if not re.fullmatch(r"monitoring_test_[a-z0-9_]+", lowered):
        raise SmokeFailure("database name must use monitoring_test_* namespace")
    username = unquote(parsed.username or "")
    if not re.fullmatch(r"monitoring_role_[a-z0-9_]+", username):
        raise SmokeFailure("database role must use monitoring_role_* namespace")
    password = unquote(parsed.password or "")
    port = parsed.port
    expected_port = int(os.environ.get("CEA_TEST_PGPORT", "5433"))
    if not password or port is None or not 1 <= port <= 65535:
        raise SmokeFailure("database URL is missing a password or valid port")
    if port != expected_port:
        raise SmokeFailure(f"database URL port does not match CEA_TEST_PGPORT: {port}")
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": username,
        "password": password,
    }


def _owned_socket() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)
    return sock, int(sock.getsockname()[1])


async def _start_server(app: FastAPI, sock: socket.socket) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=int(sock.getsockname()[1]),
        log_level="error",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(300):
        if server.started:
            return server, task
        if task.done():
            task.result()
            raise SmokeFailure("owned loopback API server exited during startup")
        await asyncio.sleep(0.05)
    server.should_exit = True
    try:
        await asyncio.wait_for(task, timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    raise SmokeFailure("owned loopback API server did not start within 15 seconds")


async def _stop_server(server: uvicorn.Server | None, task: asyncio.Task[None] | None) -> None:
    if server is not None:
        server.should_exit = True
    if task is not None:
        await asyncio.wait_for(task, timeout=15)


def _snapshot_payload(now: datetime) -> str:
    snapshot = CurrentSnapshot.model_validate(
        {
            "version": {
                "contract_version": 1,
                "config_version": 1,
                "revision": "abcdef0123456789",
            },
            "observed_at": now,
            "valid_until": now + timedelta(seconds=5),
            "series": (),
            "photoperiod": None,
            "persistence": {"state": PersistenceState.PENDING},
        }
    )
    return snapshot.model_dump_json()


async def _publish_freshness_fixture(client: redis.Redis, stop: asyncio.Event) -> None:
    keys = [
        monitoring_current_publication_key("Flower Room"),
        monitoring_current_publication_key("Veg Room"),
    ]
    while not stop.is_set():
        payload = _snapshot_payload(datetime.now(UTC))
        await asyncio.to_thread(client.mset, {key: payload for key in keys})
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            pass


async def _assign_remaining_identities(
    client: httpx.AsyncClient, conn: asyncpg.Connection
) -> None:
    room_by_identity = {1: "Flower Room", 2: "Flower Room", 5: "Veg Room", 7: "Veg Room"}
    rows = await conn.fetch(
        "SELECT sensor_id AS identity,min(time) AS first,max(time) AS last "
        "FROM measurement GROUP BY sensor_id ORDER BY sensor_id"
    )
    for row in rows:
        identity = int(row["identity"])
        if identity == 1:
            # exercise_scope_corrections already stored this exact identity's
            # full fixture interval and proved partial-overlap rejection.
            continue
        if identity not in room_by_identity:
            raise SmokeFailure(f"fixture has an unclassified measurement identity {identity}")
        body = {
            "dataset_id": None,
            "expected_revision": None,
            "source_table": "measurement",
            "source_identity": {"sensor_id": identity},
            "start": (row["first"] - timedelta(seconds=1)).isoformat(),
            "end": (row["last"] + timedelta(seconds=1)).isoformat(),
            "room": room_by_identity[identity],
        }
        response = await client.post("/api/archives/scope-corrections", json=body)
        _assert(response.status_code == 200, f"dated fixture attribution failed: {response.text}")

async def _assert_toronto_boundaries(conn: asyncpg.Connection) -> None:
    samples = ((date(2026, 3, 8), 23), (date(2026, 11, 1), 25))
    for day, expected_hours in samples:
        window = calendar_windows(
            [
                {
                    "id": 1,
                    "grow_plan_id": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                    "event_type": "bed_veg",
                    "location": "Flower Room",
                    "start_date": day,
                    "end_date": day,
                }
            ]
        )[0]
        _assert(
            int((window.end_at - window.start_at).total_seconds() // 3600) == expected_hours,
            f"Toronto {day.isoformat()} local day must span {expected_hours} UTC hours",
        )
        _assert(window.start_at == toronto_midnight(day), "phase start must be Toronto midnight")
        _assert(
            window.end_at == toronto_midnight(day + timedelta(days=1)),
            "inclusive Calendar end must use the following Toronto midnight",
        )
        before_end = window.end_at - timedelta(microseconds=1)
        included = await conn.fetchval(
            "SELECT tstzrange($1,$2,'[)') @> $3::timestamptz",
            window.start_at,
            window.end_at,
            before_end,
        )
        at_end = await conn.fetchval(
            "SELECT tstzrange($1,$2,'[)') @> $3::timestamptz",
            window.start_at,
            window.end_at,
            window.end_at,
        )
        _assert(included is True and at_end is False, "phase membership must be half-open")


async def _create_overlapping_grows(client: httpx.AsyncClient) -> dict[str, Any]:
    # Anchored at the flower start (2026-01-11): bed veg 2025-12-21..2026-01-10,
    # ripen end 2026-01-31, drying end 2026-02-07.
    common = {
        "crop_name": "Membership fixture grow",
        "environment": "indoor",
        "anchor": "flower_start",
        "anchor_date": "2026-01-11",
        "bed_veg_start": "2025-12-21",
        "bed_veg_days": 21,
        "stretch_days": 7,
        "bulk_days": 7,
        "ripen_days": 7,
        "drying_days": 7,
        "pot_start": "2025-12-14",
        "clone_start": "2025-12-07",
        "auto_mode_transition": False,
    }
    requests = [
        client.post(
            "/api/calendar/grow-plans/flower",
            json={**common, "idempotency_key": str(uuid4()), "crop_name": f"Grow {i}"},
        )
        for i in range(2)
    ]
    results = await asyncio.gather(*requests)
    accepted = [result for result in results if result.status_code == 200]
    rejected = [result for result in results if result.status_code == 409]
    _assert(len(accepted) == 1, f"exactly one overlapping grow must be accepted: {[r.text for r in results]}")
    _assert(len(rejected) == 1, f"exactly one overlapping grow must be rejected: {[r.text for r in results]}")
    _assert(rejected[0].json().get("detail", {}).get("code") == "grow_overlap", rejected[0].text)
    return accepted[0].json()


async def _exercise_calendar_membership(
    calendar_client: httpx.AsyncClient,
    archive_client: httpx.AsyncClient,
    pool: asyncpg.Pool,
    sink: CapturingOperationalEventSink,
) -> None:
    async with pool.acquire() as conn:
        await exercise_scope_corrections(archive_client, conn)
        await _assign_remaining_identities(archive_client, conn)
        await _assert_toronto_boundaries(conn)

    plan = await _create_overlapping_grows(calendar_client)
    grow_id = UUID(plan["grow_plan_id"])
    batch_id = plan["crop_batch_id"]
    plan_events = plan["events"]
    _assert(
        any(event["event_type"] == "bed_veg" and event["location"] == "Flower Room" for event in plan_events),
        "generated bed vegetation must belong to Flower Room",
    )
    _assert(
        any(event["event_type"] in {"clone_window", "pot_veg"} and event["location"] == "Veg Room"
            for event in plan_events),
        "clone/pot phases must remain in Veg Room",
    )

    # The top-level grow UUID resolves the exact existing batch; no caller batch
    # ID is accepted as a substitute.
    created = await calendar_client.post(
        "/api/calendar/events",
        json={
            "location": "Flower Room",
            "cluster": "main",
            "event_type": "bed_veg",
            "title": "Retrospective overlap bed",
            "start_date": "2026-01-04",
            "end_date": "2026-01-06",
            "grow_plan_id": str(grow_id),
        },
    )
    _assert(created.status_code == 200, f"retrospective phase create failed: {created.text}")
    _assert(created.json()["crop_batch_id"] == batch_id, "grow UUID did not resolve its exact crop batch")

    # Associate a separately created retrospective phase, then make its dates
    # overlap a same-grow bed phase. Same-grow overlap is legal and counted once.
    standalone = await calendar_client.post(
        "/api/calendar/events",
        json={
            "location": "Flower Room",
            "cluster": "main",
            "event_type": "bed_veg",
            "title": "Unassociated historical bed",
            "start_date": "2025-12-01",
            "end_date": "2025-12-07",
        },
    )
    _assert(standalone.status_code == 200, f"standalone retrospective phase failed: {standalone.text}")
    standalone_id = int(standalone.json()["numericId"])
    lifecycle = await calendar_client.get(f"/api/calendar/grow-plans/{grow_id}/lifecycle")
    _assert(lifecycle.status_code == 200, lifecycle.text)
    association = await calendar_client.post(
        f"/api/calendar/events/{standalone_id}/grow-association",
        json={"grow_plan_id": str(grow_id), "expected_revision": lifecycle.json()["calendar_revision"]},
    )
    _assert(association.status_code == 200, f"retrospective association failed: {association.text}")
    _assert(association.json()["crop_batch_id"] == batch_id, "association did not pin the target batch")
    overlap_edit = await calendar_client.patch(
        f"/api/calendar/events/{standalone_id}",
        json={"start_date": "2025-12-28", "end_date": "2026-01-03"},
    )
    _assert(overlap_edit.status_code == 200, f"same-grow overlap edit failed: {overlap_edit.text}")
    transfer_attempt = await calendar_client.patch(
        f"/api/calendar/events/{standalone_id}",
        json={"grow_plan_id": str(UUID("99999999-9999-4999-8999-999999999999"))},
    )
    _assert(
        transfer_attempt.status_code == 409
        and transfer_attempt.json().get("detail", {}).get("code") == "grow_association_required",
        f"PATCH silently accepted grow transfer: {transfer_attempt.text}",
    )

    lifecycle = await calendar_client.get(f"/api/calendar/grow-plans/{grow_id}/lifecycle")
    revision = lifecycle.json()["calendar_revision"]
    preview = await calendar_client.get(
        f"/api/calendar/grow-plans/{grow_id}/close-preview"
    )
    _assert(preview.status_code == 200, preview.text)
    preview_body = preview.json()
    _assert(preview_body["revision"] == revision, "preview did not report Calendar revision")
    _assert(not preview_body["scope_unresolved"], f"dated correction did not resolve scope: {preview_body['scope_unresolved']}")
    _assert(not preview_body["conflicts"], f"valid grow preview has conflicts: {preview_body['conflicts']}")
    _assert(preview_body["affected_counts"]["raw_by_table"]["measurement"] == 9,
            "overlapping same-grow windows must count the nine Flower-assigned raw measurements once")
    _assert(any(
        window["phase_type"] == "bed_veg" and window["room"] == "Flower Room"
        for window in preview_body["phase_windows"]
    ), "preview omitted a Flower Room bed phase")
    _assert(preview_body["newly_included"]["raw_by_table"]["measurement"] == 9,
            "initial preview must report actual newly included raw measurements")

    close = await calendar_client.post(
        f"/api/calendar/grow-plans/{grow_id}/close", json={"expected_revision": revision}
    )
    _assert(close.status_code == 200, f"explicit grow close failed: {close.text}")
    closed = close.json()
    expected_anniversary = toronto_midnight(date(2027, 2, 8))
    _assert(closed["state"] == "closed", "close route did not close the lifecycle")
    _assert(closed["original_drying_end"] == "2026-02-07", "first close did not pin final drying end")
    _assert(datetime.fromisoformat(closed["anniversary_at"]) == expected_anniversary,
            "anniversary must be Toronto midnight after final drying end plus 12 calendar months")
    _assert(closed["data_revision"] == 1 and closed["published_revision"] is None,
            "pure first closure must not advance content revision or invent a published revision")

    phase_id = int(next(event["numericId"] for event in plan_events if event["event_type"] == "bed_veg"))
    for method, path, payload in (
        ("PATCH", f"/api/calendar/events/{phase_id}", {"title": "closed edit"}),
        ("DELETE", f"/api/calendar/events/{phase_id}", None),
        ("DELETE", f"/api/calendar/grow-plans/{grow_id}", None),
    ):
        response = (
            await calendar_client.patch(path, json=payload)
            if method == "PATCH"
            else await calendar_client.delete(path)
        )
        _assert(
            response.status_code == 409
            and response.json().get("detail", {}).get("code") == "grow_closed",
            f"closed direct {method} was not rejected: {response.text}",
        )

    async with pool.acquire() as conn:
        measurement = await conn.fetchrow(
            "SELECT min(time) AS first FROM measurement WHERE sensor_id=1"
        )
        await assert_scope_closed_guard(
            archive_client,
            sensor_id=1,
            start=measurement["first"],
            end=measurement["first"] + timedelta(microseconds=1),
        )
        dataset = await conn.fetchrow(
            "SELECT dataset_id,data_revision,calendar_revision FROM archive_dataset WHERE grow_plan_id=$1",
            grow_id,
        )
        job = await conn.fetchrow(
            "SELECT kind,state,requested_revision,checkpoint FROM archive_job WHERE dataset_id=$1 "
            "ORDER BY created_at DESC LIMIT 1",
            dataset["dataset_id"],
        )
        checkpoint = json.loads(job["checkpoint"]) if isinstance(job["checkpoint"], str) else dict(job["checkpoint"])
        # The worker may claim the queued export before this assertion reads it.
        _assert(
            job["kind"] == "export" and job["state"] in {"pending", "claimed", "running"},
            "close must queue export",
        )
        _assert(job["requested_revision"] == dataset["data_revision"] == 1, "job content revision fence changed on close")
        _assert(checkpoint["calendar_revision"] == dataset["calendar_revision"] == closed["calendar_revision"],
                "job checkpoint must pin Calendar and content revisions")

    reopen = await calendar_client.post(
        f"/api/calendar/grow-plans/{grow_id}/reopen",
        json={"expected_revision": closed["calendar_revision"]},
    )
    _assert(reopen.status_code == 200, f"reopen failed: {reopen.text}")
    reopened = reopen.json()
    _assert(reopened["state"] == "open", "reopen route did not reopen lifecycle")
    _assert(reopened["calendar_revision"] == closed["calendar_revision"] + 1,
            "reopen must advance the Calendar revision once")
    _assert(reopened["data_revision"] == closed["data_revision"],
            "pure reopen must not advance content revision")
    _assert(reopened["original_drying_end"] == closed["original_drying_end"]
            and reopened["anniversary_at"] == closed["anniversary_at"],
            "reopen must preserve first-closure date and anniversary")
    async with pool.acquire() as conn:
        superseded = await conn.fetchrow(
            "SELECT state,last_error_code FROM archive_job WHERE dataset_id=$1 AND requested_revision=1",
            reopened["dataset_id"],
        )
        _assert(
            superseded["state"] == "failed"
            and superseded["last_error_code"] == "archive_job_superseded",
            "reopen must invalidate the old Calendar-fenced job",
        )

    edit_after_reopen = await calendar_client.patch(
        f"/api/calendar/events/{phase_id}", json={"title": "corrected after reopening"}
    )
    _assert(edit_after_reopen.status_code == 200, f"reopened phase edit failed: {edit_after_reopen.text}")
    changed = await calendar_client.get(f"/api/calendar/grow-plans/{grow_id}/lifecycle")
    _assert(changed.json()["data_revision"] == closed["data_revision"] + 1,
            "phase edit must advance content revision")
    reclose = await calendar_client.post(
        f"/api/calendar/grow-plans/{grow_id}/close",
        json={"expected_revision": changed.json()["calendar_revision"]},
    )
    _assert(reclose.status_code == 200, f"re-close failed: {reclose.text}")
    _assert(reclose.json()["original_drying_end"] == closed["original_drying_end"]
            and reclose.json()["anniversary_at"] == closed["anniversary_at"],
            "re-close must not reset the first drying date or anniversary")
    _assert(reclose.json()["data_revision"] == changed.json()["data_revision"],
            "re-close must preserve the current content revision")

    _assert(len(sink.events) >= 8, "production mutation routes did not emit persisted event evidence")
    print(
        "membership API smoke passed: concurrent overlap fence, retrospective association, "
        "Flower bed/Veg clone-pot scope, exact raw counts, explicit close/reopen and fences"
    )


async def run(database_url: str, *, scenario=None) -> None:
    db_config = _parse_database_url(database_url)
    os.environ.pop("CREDENTIALS_DIRECTORY", None)
    os.environ.pop("NOTIFY_SOCKET", None)
    api_key = secrets.token_urlsafe(32)
    os.environ.update(
        {
            "POSTGRES_HOST": db_config["host"],
            "POSTGRES_PORT": str(db_config["port"]),
            "POSTGRES_DB": db_config["database"],
            "POSTGRES_USER": db_config["user"],
            "POSTGRES_PASSWORD": db_config["password"],
            "CEA_API_KEY_REQUIRE": "true",
            "CEA_API_KEY": api_key,
            "ARCHIVE_LIFECYCLE_ENABLED": "false",
            "ARCHIVE_WORKER_CONCURRENCY": "1",
        }
    )
    with tempfile.TemporaryDirectory(prefix="grow-lifecycle-membership-") as temporary_root:
        owned_root = Path(temporary_root)
        redis_socket, redis_port = _owned_socket()
        redis_socket.close()
        redis_process = subprocess.Popen(
            [
                "redis-server",
                "--bind",
                "127.0.0.1",
                "--port",
                str(redis_port),
                "--protected-mode",
                "no",
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                str(owned_root),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        redis_url = f"redis://127.0.0.1:{redis_port}/0"
        os.environ["REDIS_URL"] = redis_url
        os.environ["ARCHIVE_ROOT"] = str(owned_root / "archive-root")
        redis_fixture = redis.Redis.from_url(redis_url, decode_responses=True)
        pool: asyncpg.Pool | None = None
        archive_server: uvicorn.Server | None = None
        archive_task: asyncio.Task[None] | None = None
        calendar_server: uvicorn.Server | None = None
        calendar_task: asyncio.Task[None] | None = None
        archive_socket: socket.socket | None = None
        calendar_socket: socket.socket | None = None
        publish_stop = asyncio.Event()
        publisher: asyncio.Task[None] | None = None
        try:
            for _ in range(100):
                if redis_process.poll() is not None:
                    raise SmokeFailure("owned loopback Redis fixture exited during startup")
                try:
                    await asyncio.to_thread(redis_fixture.ping)
                    break
                except redis.RedisError:
                    await asyncio.sleep(0.05)
            else:
                raise SmokeFailure("owned loopback Redis fixture did not start")
            publisher = asyncio.create_task(_publish_freshness_fixture(redis_fixture, publish_stop))
            pool = await asyncpg.create_pool(**db_config, min_size=2, max_size=6, command_timeout=10)
            database = DatabaseManager(db_config=db_config, redis_url=redis_url)
            database._pool = pool
            database._db_connected = True
            database._calendar_repo = CalendarRepository(pool)
            sink = CapturingOperationalEventSink()

            calendar_host = FastAPI(title="Isolated production Calendar router host")
            calendar_host.include_router(calendar_router)
            calendar_host.add_middleware(APIKeyAuthMiddleware)
            calendar_host.dependency_overrides[calendar_get_database] = lambda: database
            calendar_host.dependency_overrides[get_mutation_event_sink] = lambda: sink

            archive_host = create_archive_app()
            archive_socket, archive_port = _owned_socket()
            calendar_socket, calendar_port = _owned_socket()
            archive_server, archive_task = await _start_server(archive_host, archive_socket)
            calendar_server, calendar_task = await _start_server(calendar_host, calendar_socket)

            headers = {"X-API-Key": api_key}
            async with (
                httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{archive_port}",
                    headers=headers,
                    trust_env=False,
                    timeout=10,
                ) as archive_client,
                httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{calendar_port}",
                    headers=headers,
                    trust_env=False,
                    timeout=10,
                ) as calendar_client,
            ):
                health = await archive_client.get("/health")
                _assert(health.status_code == 200, f"archive app health failed: {health.text}")
                ready = await archive_client.get("/ready")
                _assert(ready.status_code == 200, f"archive app readiness failed: {ready.text}")
                calendar_read = await calendar_client.get(
                    "/api/calendar/events",
                    params={"from": "2026-01-01", "to": "2026-02-15", "location": "Flower Room"},
                )
                _assert(calendar_read.status_code == 200, f"Calendar route host failed: {calendar_read.text}")
                _assert(
                    any(item.get("numericId") == 1 for item in calendar_read.json()["items"]),
                    "Calendar host is not reading the real fixture calendar_event row",
                )
                if scenario is None:
                    await _exercise_calendar_membership(calendar_client, archive_client, pool, sink)
                else:
                    await scenario(calendar_client, archive_client, pool, sink, database)
        finally:
            publish_stop.set()
            if publisher is not None:
                await asyncio.gather(publisher, return_exceptions=True)
            await _stop_server(calendar_server, calendar_task)
            await _stop_server(archive_server, archive_task)
            if calendar_socket is not None:
                calendar_socket.close()
            if archive_socket is not None:
                archive_socket.close()
            if pool is not None:
                await pool.close()
            await asyncio.to_thread(redis_fixture.close)
            if redis_process.poll() is None:
                redis_process.terminate()
                try:
                    redis_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    redis_process.kill()
                    redis_process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.database_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
