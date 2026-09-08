from __future__ import annotations

from datetime import datetime

from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.control.scheduler import Scheduler


def test_recreated_light_mid_ramp_uses_remaining_schedule_time_for_ten_percent_target() -> None:
    # Given: a newly recreated light halfway through a one-hour sunrise ramp.
    scheduler = Scheduler([])
    snapshot = RuntimeDeviceSnapshot.create(
        version=1,
        hierarchy={"Veg Room": {"main": {"light_v_1": {"device_id": 42, "device_type": "light"}}}},
        mode_parameters={
            ("Veg Room", "main"): {
                "mode_id": 1,
                "day_start": "08:00",
                "night_start": "20:00",
                "ramp_up": 60,
                "ramp_down": 0,
            }
        },
        light_intensities={(42, 1): 10.0},
        light_programs=[],
    )
    scheduler.install_snapshot(snapshot)
    current_time = datetime(2026, 7, 30, 8, 30)

    # When: the first AUTO calculation runs at the recreated light's current ramp position.
    intensity = scheduler.get_schedule_intensity("Veg Room", "main", "light_v_1", current_time)

    # Then: it uses the 10% target and only the remaining thirty minutes, not a new full ramp.
    assert intensity == 10.0
    assert scheduler._light_ramp_state[("Veg Room", "main", "light_v_1")]["ramp_duration"] == 30.0
