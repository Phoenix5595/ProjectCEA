"""Membership-stability behavior tests for backend WebSocket broadcasts.

Plan step 2 (#4): broadcast recipients must be snapshotted before any send
await. A removed snapshot member may still get an attempted send; joiners
wait for the next broadcast. One send failure must not skip remaining
recipients and the failed connection is removed. All fakes; no real
sockets.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import unittest
from unittest.mock import AsyncMock

from app.websocket import WebSocketManager


class FakeWS:
    """Fake FastAPI WebSocket: controllable send, records text payloads."""

    def __init__(self, name: str, *, gate: asyncio.Event | None = None) -> None:
        self.name = name
        self.gate = gate
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, text: str) -> None:
        if self.gate is not None and not self.gate.is_set():
            await self.gate.wait()
        self.sent.append(text)

    async def accept(self) -> None:
        return None


class MembershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = WebSocketManager()

    # -- sensor broadcast -------------------------------------------------

    async def test_sensor_broadcast_attempts_only_snapshot_members_after_mutation(self) -> None:
        """Suspend a send, mutate membership, release: only the original
        snapshot recipients are attempted (late joiner not sent to)."""
        gate = asyncio.Event()
        blocker = FakeWS("blocker", gate=gate)
        stable = FakeWS("stable")
        late_joiner = FakeWS("late-joiner")

        self.manager.active_connections = {"Flower Room": {blocker, stable}}

        started = asyncio.Event()

        async def suspending_send(text: str) -> None:
            # Real coroutine: suspends on the gate so the mutation below
            # happens while this send is in flight (not a sync side_effect
            # returning a never-awaited coroutine).
            started.set()
            await gate.wait()
            blocker.sent.append(text)

        blocker.send_text = suspending_send  # type: ignore[method-assign]

        async def mutate() -> None:
            await started.wait()
            # remove a suspended member and add a late joiner mid-broadcast
            self.manager.disconnect(blocker, "Flower Room")
            self.manager.active_connections["Flower Room"].add(late_joiner)
            gate.set()

        mutation = asyncio.create_task(mutate())
        await self.manager.broadcast_sensor_update(
            location="Flower Room",
            cluster="back",
            sensor_type="dry_bulb_b",
            timestamp=datetime(2026, 1, 1, tzinfo=None),
            value=21.0,
            unit="°C",
        )
        await mutation

        self.assertEqual(
            blocker.sent, [self._sensor_payload_json()], "suspended member still attempted"
        )
        self.assertEqual(len(stable.sent), 1)
        self.assertEqual(late_joiner.sent, [])

    def _sensor_payload_json(self) -> str:
        """Expected sensor payload JSON for the fixed broadcast arguments."""
        import json

        from app.models import WebSocketMessage

        message = WebSocketMessage(
            type="sensor_update",
            location="Flower Room",
            cluster="back",
            sensor_type="dry_bulb_b",
            timestamp=datetime(2026, 1, 1),
            value=21.0,
            unit="°C",
        )
        payload = message.model_dump(mode="json")
        payload["sensor"] = "dry_bulb_b"
        return json.dumps(payload)

    async def test_removed_snapshot_member_still_receives_attempted_send(self) -> None:
        gate = asyncio.Event()
        blocker = FakeWS("blocker", gate=gate)
        stable = FakeWS("stable")
        self.manager.active_connections = {"Flower Room": {blocker, stable}}

        async def release_and_remove() -> None:
            await asyncio.sleep(0.01)
            gate.set()
            self.manager.disconnect(blocker, "Flower Room")

        task = asyncio.create_task(release_and_remove())
        await self.manager.broadcast_sensor_update(
            location="Flower Room",
            cluster="back",
            sensor_type="dry_bulb_b",
            timestamp=datetime(2026, 1, 1),
            value=22.0,
            unit="°C",
        )
        await task
        # The removed snapshot member still got this broadcast attempted.
        self.assertEqual(blocker.sent, stable.sent)
        self.assertEqual(len(blocker.sent), 1)
        self.assertNotIn(blocker, self.manager.active_connections.get("Flower Room", set()))

    async def test_one_send_failure_does_not_skip_remaining_recipients(self) -> None:
        failing = FakeWS("failing")
        stable = FakeWS("stable")
        failing_send = AsyncMock(side_effect=RuntimeError("connection gone"))
        failing.send_text = failing_send  # type: ignore[method-assign]
        self.manager.active_connections = {"Flower Room": {failing, stable}}

        await self.manager.broadcast_sensor_update(
            location="Flower Room",
            cluster="back",
            sensor_type="rh_b",
            timestamp=datetime(2026, 1, 1),
            value=55.0,
            unit="%",
        )

        self.assertEqual(len(stable.sent), 1, "failure must not skip later members")
        self.assertEqual(failing.sent, [])
        self.assertNotIn(failing, self.manager.active_connections["Flower Room"])
        self.assertIn(stable, self.manager.active_connections["Flower Room"])

    # -- config broadcast --------------------------------------------------

    async def test_config_broadcast_all_locations_snapshotted_before_awaits(self) -> None:
        """All-room config broadcast snapshots (location, recipients) for
        every location, including locations created after the snapshot."""
        gate = asyncio.Event()
        blocker = FakeWS("blocker", gate=gate)
        stable = FakeWS("stable")
        second_room = FakeWS("second-room")
        late = FakeWS("late-room-client")

        self.manager.active_connections = {
            "Flower Room": {blocker},
            "Veg Room": {stable, second_room},
        }

        started = asyncio.Event()

        async def suspending_send(text: str) -> None:
            started.set()
            await gate.wait()
            blocker.sent.append(text)

        blocker.send_text = suspending_send  # type: ignore[method-assign]

        async def mutate() -> None:
            await started.wait()
            # new location appears mid-broadcast; must not be attempted
            self.manager.active_connections["Late Room"] = {late}
            gate.set()

        mutation = asyncio.create_task(mutate())
        await self.manager.broadcast_config_event("all", {"type": "config_update"})
        await mutation

        self.assertEqual(blocker.sent, ['{"type": "config_update"}'])
        self.assertEqual(len(stable.sent), 1)
        self.assertEqual(len(second_room.sent), 1)
        self.assertEqual(late.sent, [])

    async def test_config_broadcast_unknown_targets_all_current_locations(self) -> None:
        first = FakeWS("first")
        second = FakeWS("second")
        self.manager.active_connections = {"Alpha": {first}, "Beta": {second}}

        await self.manager.broadcast_config_event("unknown", {"type": "config_update"})

        self.assertEqual(len(first.sent), 1)
        self.assertEqual(len(second.sent), 1)

    async def test_config_broadcast_unknown_still_snapshots_members(self) -> None:
        gate = asyncio.Event()
        blocker = FakeWS("blocker", gate=gate)
        follower = FakeWS("follower")
        self.manager.active_connections = {"Beta": {blocker, follower}}

        started = asyncio.Event()

        async def suspending_send(text: str) -> None:
            started.set()
            await gate.wait()
            blocker.sent.append(text)

        blocker.send_text = suspending_send  # type: ignore[method-assign]

        async def mutate() -> None:
            await started.wait()
            self.manager.active_connections["Gamma"] = {FakeWS("late")}
            gate.set()

        mutation = asyncio.create_task(mutate())
        await self.manager.broadcast_config_event("unknown", {"type": "config_update"})
        await mutation
        # blocker still receives despite no longer being subscribed at
        # release time; follower receives; the location added mid-flight
        # does not.
        self.assertEqual(blocker.sent, ['{"type": "config_update"}'])
        self.assertEqual(len(follower.sent), 1)

    async def test_config_single_location_ignores_other_rooms(self) -> None:
        alpha = FakeWS("alpha")
        beta = FakeWS("beta")
        self.manager.active_connections = {"Alpha": {alpha}, "Beta": {beta}}

        await self.manager.broadcast_config_event("Alpha", {"type": "config_update"})

        self.assertEqual(len(alpha.sent), 1)
        self.assertEqual(beta.sent, [])

    async def test_config_broadcast_failure_removes_failed_connection_only(self) -> None:
        failing = FakeWS("failing")
        stable = FakeWS("stable")
        failing.send_text = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        self.manager.active_connections = {"Alpha": {failing, stable}}

        await self.manager.broadcast_config_event("Alpha", {"type": "config_update"})

        self.assertEqual(self.manager.active_connections["Alpha"], {stable})
        self.assertEqual(len(stable.sent), 1)

    # -- snapshot paired reads --------------------------------------------
    # (malformed-cardinality coverage lives in test_live_sensor_snapshot.py
    # alongside the other _read_sensor_snapshot tests.)
