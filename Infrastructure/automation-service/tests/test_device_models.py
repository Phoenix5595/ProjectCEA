"""Tests for Device and LightDevice Pydantic domain models.

TDD pin: these tests MUST fail before device_registry.py is written,
then pass after the models are implemented.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.device_registry import Device, LightDevice, LightDeviceCreate, LightDeviceUpdate


class TestLightDeviceValidation:
    """Validation tests for the LightDevice domain model."""

    def test_valid_light_device_minimal(self):
        """A minimally valid LightDevice constructs successfully."""
        ld = LightDevice(
            device_type="light",
            board_id=0,
            dimming_channel=0,
            per_room_index=1,
            device_name="light_f_1",
            display_name="Chilled Front",
            location="Flower Room",
            cluster="main",
        )
        assert ld.device_type == "light"
        assert ld.board_id == 0
        assert ld.dimming_channel == 0
        assert ld.per_room_index == 1
        assert ld.device_name == "light_f_1"

    def test_valid_light_device_full(self):
        """A fully specified LightDevice constructs successfully."""
        ld = LightDevice(
            device_type="light",
            board_id=2,
            dimming_channel=1,
            dimming_enabled=True,
            dimming_type="dfr0971",
            safety_level=1,
            per_room_index=3,
            relay_channel=10,
            display_name="Apache",
            device_name="light_f_3",
            location="Flower Room",
            cluster="main",
        )
        assert ld.dimming_type == "dfr0971"
        assert ld.safety_level == 1
        assert ld.relay_channel == 10

    def test_rejects_dimming_channel_out_of_range(self):
        """dimming_channel must be 0 or 1."""
        with pytest.raises(ValidationError):
            LightDevice(
                device_type="light",
                board_id=0,
                dimming_channel=5,
                per_room_index=1,
                device_name="light_f_1",
                display_name="X",
                location="Flower Room",
                cluster="main",
            )

    def test_rejects_invalid_cluster(self):
        """cluster must be 'main' for device entries."""
        with pytest.raises(ValidationError):
            LightDevice(
                device_type="light",
                board_id=0,
                dimming_channel=0,
                per_room_index=1,
                device_name="light_f_1",
                display_name="X",
                location="Flower Room",
                cluster="front",
            )

    def test_rejects_wrong_device_type(self):
        """LightDevice device_type must be 'light'."""
        with pytest.raises(ValidationError):
            LightDevice(
                device_type="heater",
                board_id=0,
                dimming_channel=0,
                per_room_index=1,
                device_name="light_f_1",
                display_name="X",
                location="Flower Room",
                cluster="main",
            )

    def test_rejects_negative_board_id(self):
        """board_id must be >= 0."""
        with pytest.raises(ValidationError):
            LightDevice(
                device_type="light",
                board_id=-1,
                dimming_channel=0,
                per_room_index=1,
                device_name="light_f_1",
                display_name="X",
                location="Flower Room",
                cluster="main",
            )

    def test_rejects_per_room_index_zero(self):
        """per_room_index must be >= 1."""
        with pytest.raises(ValidationError):
            LightDevice(
                device_type="light",
                board_id=0,
                dimming_channel=0,
                per_room_index=0,
                device_name="light_f_1",
                display_name="X",
                location="Flower Room",
                cluster="main",
            )

    def test_rejects_invalid_device_name_pattern(self):
        """device_name must match ^light_[fvlo]_\d+$."""
        with pytest.raises(ValidationError):
            LightDevice(
                device_type="light",
                board_id=0,
                dimming_channel=0,
                per_room_index=1,
                device_name="not_a_light",
                display_name="X",
                location="Flower Room",
                cluster="main",
            )

    def test_accepts_all_room_prefixes(self):
        """device_name prefixes f, v, l, o are all accepted."""
        for prefix, room in (
            ("f", "Flower Room"),
            ("v", "Veg Room"),
            ("l", "Lab"),
            ("o", "Outside"),
        ):
            ld = LightDevice(
                device_type="light",
                board_id=0,
                dimming_channel=0,
                per_room_index=1,
                device_name=f"light_{prefix}_1",
                display_name="Test",
                location=room,
                cluster="main",
            )
            assert ld.device_name == f"light_{prefix}_1"

    def test_defaults(self):
        """Default values are applied correctly."""
        ld = LightDevice(
            device_type="light",
            board_id=0,
            dimming_channel=0,
            per_room_index=1,
            device_name="light_f_1",
            display_name="Test",
            location="Flower Room",
            cluster="main",
        )
        assert ld.dimming_enabled is True
        assert ld.dimming_type == "dfr0971"
        assert ld.safety_level == 0
        assert ld.relay_channel is None


class TestDeviceBaseValidation:
    """Validation tests for the generic Device base model."""

    def test_valid_non_light_device(self):
        """A valid non-light Device constructs successfully."""
        d = Device(
            device_type="heating",
            channel=0,
            pid_enabled=True,
            interlock_with=["exhaust"],
            pid_setpoints={"heating_setpoint": 1},
            display_name="Heater 1",
            device_name="heater1",
            location="Flower Room",
            cluster="main",
        )
        assert d.device_type == "heating"
        assert d.channel == 0
        assert d.pid_enabled is True

    def test_device_optional_display_name(self):
        """display_name is optional for generic Device."""
        d = Device(
            device_type="cooling",
            channel=1,
            pid_enabled=False,
            interlock_with=[],
            pid_setpoints={},
            device_name="fan1",
            location="Veg Room",
            cluster="main",
        )
        assert d.display_name is None

    def test_device_rejects_invalid_cluster(self):
        """cluster must be 'main' for device entries."""
        with pytest.raises(ValidationError):
            Device(
                device_type="heating",
                channel=0,
                pid_enabled=True,
                interlock_with=[],
                pid_setpoints={},
                device_name="heater1",
                location="Flower Room",
                cluster="back",
            )


class TestLightDeviceCreate:
    """Tests for the LightDeviceCreate request body model."""

    def test_valid_create(self):
        """Minimal valid create request."""
        req = LightDeviceCreate(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Chilled Front",
        )
        assert req.board_id == 0
        assert req.dimming_channel == 0
        assert req.room == "Flower Room"
        assert req.display_name == "Chilled Front"
        assert req.per_room_index is None

    def test_create_with_explicit_index(self):
        """Create request with explicit per_room_index."""
        req = LightDeviceCreate(
            board_id=1,
            dimming_channel=1,
            room="Veg Room",
            display_name="Ridgetop",
            per_room_index=2,
        )
        assert req.per_room_index == 2

    def test_create_rejects_negative_board_id(self):
        """board_id must be >= 0."""
        with pytest.raises(ValidationError):
            LightDeviceCreate(
                board_id=-1,
                dimming_channel=0,
                room="Flower Room",
                display_name="X",
            )

    def test_create_rejects_invalid_dimming_channel(self):
        """dimming_channel must be 0 or 1."""
        with pytest.raises(ValidationError):
            LightDeviceCreate(
                board_id=0,
                dimming_channel=2,
                room="Flower Room",
                display_name="X",
            )


class TestLightDeviceUpdate:
    """Tests for the LightDeviceUpdate request body model."""

    def test_valid_update(self):
        """Minimal valid update request."""
        req = LightDeviceUpdate(
            display_name="New Name",
            room="Veg Room",
            per_room_index=2,
            relay_channel=5,
        )
        assert req.display_name == "New Name"
        assert req.room == "Veg Room"
        assert req.per_room_index == 2
        assert req.relay_channel == 5

    def test_update_all_optional(self):
        """All fields are optional for update."""
        req = LightDeviceUpdate()
        assert req.display_name is None
        assert req.room is None
        assert req.per_room_index is None
        assert req.relay_channel is None

    def test_update_rejects_per_room_index_zero(self):
        """per_room_index must be >= 1 if provided."""
        with pytest.raises(ValidationError):
            LightDeviceUpdate(per_room_index=0)
