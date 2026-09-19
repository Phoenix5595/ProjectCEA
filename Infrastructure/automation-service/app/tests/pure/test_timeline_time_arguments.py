"""Pure unit coverage for the apply repository's time-argument conversion."""

import pytest

from app.repositories.climate_timeline_apply import _parse_time_text


def test_parses_hh_mm_clock_text() -> None:
    parsed = _parse_time_text("16:00")
    assert (parsed.hour, parsed.minute) == (16, 0)


def test_parses_hh_mm_ss_clock_text() -> None:
    parsed = _parse_time_text("06:00:00")
    assert (parsed.hour, parsed.minute, parsed.second) == (6, 0, 0)


def test_rejects_non_time_text() -> None:
    with pytest.raises(ValueError, match="invalid time text"):
        _parse_time_text("not-a-time")
