from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import final

import pytest

from monitoring_service.control_models import ControlHistoryRange
from monitoring_service.control_repository import ControlHistoryRepository

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


@final
class TransitionDatabase:
    async def fetch(
        self, query: str, *_: str | int | float | datetime
    ) -> list[dict[str, str | float | datetime | int | None]]:
        if "monitoring_automation_state" in query:
            return [
                {
                    "bucket": NOW + timedelta(minutes=index),
                    "device_name": "fan_v_1",
                    "device_state_last": index,
                    "device_mode_last": f"mode-{index}",
                    "control_reason_last": f"reason-{index}",
                    "pid_output_last": None,
                    "duty_cycle_percent_last": None,
                }
                for index in range(11)
            ]
        return []


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_budget_counts_the_predecessor_within_the_semantic_series_limit() -> None:
    # Given: eleven real state transitions, including the predecessor that establishes the first hold.
    history_range = ControlHistoryRange(start=NOW, end=NOW + timedelta(minutes=11))

    # When: the ten-point semantic budget is applied.
    response = await ControlHistoryRepository(TransitionDatabase()).read(
        "Veg Room", history_range, max_points=10
    )

    # Then: no sentinel exceeds the budget and every emitted mode/reason remains an observed string.
    points = response.devices[0].points
    assert len(points) == 10
    assert (points[0].device_state, points[-1].device_state) == (0.0, 10.0)
    assert {point.device_mode for point in points} <= {f"mode-{index}" for index in range(11)}
    assert {point.control_reason for point in points} <= {f"reason-{index}" for index in range(11)}
