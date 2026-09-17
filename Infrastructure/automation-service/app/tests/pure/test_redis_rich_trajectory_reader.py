"""Stale or foreign-shape rich-trajectory cache payloads degrade to None."""

import json

from app.redis.monitoring import RedisRichTrajectoryReader


class _FakeRedis:
    """Return one canned value for rich-trajectory reads."""

    def __init__(self, payload: str | None) -> None:
        self._payload = payload

    def get(self, key: str) -> str | None:
        return self._payload


def test_read_rich_trajectory_degrades_to_none_on_a_foreign_shape_payload() -> None:
    payload = json.dumps(
        {
            "contract_version": 1,
            "room": "Veg Room",
            "generated_at": "2026-09-17T11:45:34.844265Z",
            "window": {"start": "2026-09-17T11:45:34.844265Z", "end": "2026-09-18T00:00:00Z"},
            "revision_scope": "saved",
            "base_config_revision": "00000c3",
            "segments": [
                {
                    "shape": "step",
                    "step": {"start": "2026-09-17T11:45:34.844265Z", "end": "2026-09-18T00:00:00Z"},
                }
            ],
        }
    )
    reader = RedisRichTrajectoryReader(_FakeRedis(payload))
    assert reader.read_rich_trajectory("Veg Room") is None


def test_read_rich_trajectory_returns_a_valid_envelope() -> None:
    payload = json.dumps(
        {
            "contract_version": 1,
            "room": "Veg Room",
            "generated_at": "2026-09-17T11:45:34.844265Z",
            "window": {
                "start": "2026-09-17T11:45:34.844265Z",
                "end": "2026-09-18T11:45:34.844265Z",
                "timezone": "UTC",
            },
            "revision_scope": "saved",
            "base_config_revision": "00000c3",
            "draft_revision": None,
            "segments": [
                {
                    "shape": "step",
                    "start": "2026-09-17T11:45:34.844265Z",
                    "end": "2026-09-18T00:00:00Z",
                    "metric": "heating_setpoint",
                    "unit": "celsius",
                    "trajectory_kind": "scheduled",
                    "quality": "estimated",
                    "value": 10.0,
                    "source": {"mode": "veg", "period": {"period_id": "1", "label": "Period 1"}, "config_revision": "00000c3"},
                }
            ],
        }
    )
    reader = RedisRichTrajectoryReader(_FakeRedis(payload))
    envelope = reader.read_rich_trajectory("Veg Room")
    assert envelope is not None
    assert envelope.room == "Veg Room"
    assert envelope.segments[0].start.tzinfo is not None


def test_read_rich_trajectory_missing_key_is_none() -> None:
    reader = RedisRichTrajectoryReader(_FakeRedis(None))
    assert reader.read_rich_trajectory("Veg Room") is None
