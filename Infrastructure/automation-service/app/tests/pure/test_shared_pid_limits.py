from pydantic import ValidationError
import pytest

from app.models.config_schema import AppConfig


def test_app_config_accepts_pid_limits_through_shared_model() -> None:
    result = AppConfig.model_validate(
        {
            "control": {
                "pid_limits": {
                    "heater": {
                        "kp_min": 0,
                        "kp_max": 100,
                        "ki_min": 0,
                        "ki_max": 1,
                        "kd_min": 0,
                        "kd_max": 10,
                    }
                }
            }
        }
    )

    assert result.control["pid_limits"]["heater"]["kp_max"] == 100


def test_app_config_rejects_invalid_pid_limits_through_shared_model() -> None:
    with pytest.raises(ValidationError, match="control.pid_limits.heater.kp_min"):
        AppConfig.model_validate(
            {"control": {"pid_limits": {"heater": {"kp_min": -1, "kp_max": 100}}}}
        )
