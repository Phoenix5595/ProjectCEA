"""Plan step 1 / #12 regression tests: request-local GET config reads.

GET /api/config must derive pending changes from its already-read raw YAML and
sidecar (including a missing sidecar) instead of re-reading the YAML from disk.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import app.routes.system_config as system_config

BASE_YAML = """\
hardware:
  i2c_bus: 1
control:
  update_interval: 2
  safety_limits:
    min_temperature: 5
"""


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)


def _compute_fixture_hash() -> str:
    return system_config._compute_restart_hash({"hardware": {"i2c_bus": 2}})


def write_yaml(tmp_path, text: str = BASE_YAML):
    path = tmp_path / "automation_config.yaml"
    path.write_text(text)
    return path


def sidecar_path(tmp_path):
    return tmp_path / "automation_config.restart_hash"


def write_new_format_sidecar(tmp_path, raw: dict[str, Any]) -> None:
    sidecar_path(tmp_path).write_text(
        json.dumps(
            {"hash": "previous-hash", "subset": system_config._extract_restart_subset(raw)},
            sort_keys=True,
        )
    )


async def read_config(config_path) -> dict[str, Any]:
    config = system_config.ConfigLoader(str(config_path))
    return await system_config.get_system_config(config=config)


def test_get_reports_exact_changed_fields_for_new_format_sidecar(tmp_path) -> None:
    """Hardware changes affect restart hashes; service settings do not."""
    write_yaml(tmp_path)
    write_new_format_sidecar(
        tmp_path,
        {
            "hardware": {"i2c_bus": 0},
            "control": {"update_interval": 2, "safety_limits": {"min_temperature": 5}},
        },
    )
    (tmp_path / "automation_config.yaml").write_text(
        BASE_YAML.replace("i2c_bus: 1", "i2c_bus: 2")
    )

    response = _run(read_config(tmp_path / "automation_config.yaml"))

    assert response == {
        "hardware": {"i2c_bus": 2},
        "pending_restart_required_changes": ["hardware.i2c_bus"],
        "restart_hashes": {"current": _compute_fixture_hash(), "sidecar": "previous-hash"},
    }


def test_control_setting_changes_do_not_require_restart(tmp_path) -> None:
    write_yaml(tmp_path)
    sidecar_path(tmp_path).write_text(
        json.dumps(
            {
                "hash": "previous-hash",
                "subset": {
                    "hardware": {"i2c_bus": 1},
                    "control": {"update_interval": 1},
                },
            }
        )
    )

    response = _run(read_config(tmp_path / "automation_config.yaml"))

    assert response["pending_restart_required_changes"] == []
    assert response["restart_hashes"]["current"] == system_config._compute_restart_hash(
        {"hardware": {"i2c_bus": 1}, "control": {"update_interval": 2}}
    )


def test_get_reports_empty_pending_for_missing_sidecar(tmp_path) -> None:
    """Missing sidecar => pending [] and sidecar hash None."""
    write_yaml(tmp_path)
    response = _run(read_config(tmp_path / "automation_config.yaml"))

    assert response["pending_restart_required_changes"] == []
    assert response["restart_hashes"]["sidecar"] is None
    assert response["restart_hashes"]["current"] == system_config._compute_restart_hash(
        {
            "hardware": {"i2c_bus": 1},
            "control": {"safety_limits": {"min_temperature": 5}, "update_interval": 2},
        }
    )


def test_get_reports_empty_pending_for_legacy_hash_sidecar(tmp_path) -> None:
    """Legacy single-hash sidecar text is clean state."""
    write_yaml(tmp_path)
    sidecar_path(tmp_path).write_text("legacyhash")

    response = _run(read_config(tmp_path / "automation_config.yaml"))

    assert response["pending_restart_required_changes"] == []
    assert response["restart_hashes"]["sidecar"] is None


def test_get_reports_empty_pending_for_malformed_sidecar(tmp_path) -> None:
    """Malformed sidecar JSON is clean state, not an error."""
    write_yaml(tmp_path)
    sidecar_path(tmp_path).write_text("{not json")

    response = _run(read_config(tmp_path / "automation_config.yaml"))

    assert response["pending_restart_required_changes"] == []
    assert response["restart_hashes"]["sidecar"] is None


def test_malformed_yaml_preserves_existing_failure_behavior(tmp_path) -> None:
    """Malformed YAML keeps today's failure mode (yaml parse error from GET)."""
    import yaml

    path = write_yaml(tmp_path, "control: [unclosed")
    with pytest.raises(yaml.YAMLError):
        _run(read_config(path))
