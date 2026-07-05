"""System configuration endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from starlette.status import HTTP_403_FORBIDDEN
import yaml

from app.config import ConfigLoader
from app.schemas.system_config import ConfigUpdateRequest
from shared.infra_logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

RESTART_REQUIRED_CONTROL_KEYS = (
    "safety_limits",
    "update_interval",
    "last_good_hold_period",
    "binary_hysteresis",
    "pid_limits",
)


def get_config() -> ConfigLoader:
    """Get config loader."""
    from app.main import container

    return container.get_config()


def _restart_hash_sidecar_path(config: ConfigLoader) -> Path:
    return config.config_path.parent / "automation_config.restart_hash"


def _extract_restart_subset(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Extract the restart-required subset from raw config."""
    control = raw_config.get("control") or {}
    return {
        "hardware": raw_config.get("hardware", {}),
        "control": {
            "safety_limits": control.get("safety_limits", {}),
            "update_interval": control.get("update_interval"),
            "last_good_hold_period": control.get("last_good_hold_period"),
            "binary_hysteresis": control.get("binary_hysteresis"),
            "pid_limits": control.get("pid_limits", {}),
        },
    }


def _compute_restart_hash(raw_config: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON of restart-required subset."""
    restart_subset = _extract_restart_subset(raw_config)
    canonical = json.dumps(restart_subset, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _read_sidecar(config: ConfigLoader) -> dict[str, Any] | None:
    """Read the sidecar file. Returns dict with 'hash' and 'subset' keys, or None."""
    path = _restart_hash_sidecar_path(config)
    if not path.exists():
        return None
    try:
        text = path.read_text().strip()
        # Try parsing as JSON (new format)
        data = json.loads(text)
        if isinstance(data, dict) and "subset" in data:
            return data
        # Old format: just a hash string — treat as clean state
        return None
    except (OSError, json.JSONDecodeError):
        return None


def _write_sidecar(config: ConfigLoader, hash_value: str, subset: dict[str, Any]) -> None:
    """Write the sidecar file with hash + subset."""
    path = _restart_hash_sidecar_path(config)
    data = {"hash": hash_value, "subset": subset}
    path.write_text(json.dumps(data, sort_keys=True))


def _deep_diff(current: Any, previous: Any, path: str = "") -> list[str]:
    """Recursively compare two values and return list of dotted paths that differ."""
    changes: list[str] = []
    if type(current) is not type(previous):
        changes.append(path)
        return changes
    if isinstance(current, dict):
        all_keys = set(current.keys()) | set(previous.keys())
        for key in sorted(all_keys):
            new_path = f"{path}.{key}" if path else key
            if key not in current or key not in previous:
                changes.append(new_path)
            else:
                changes.extend(_deep_diff(current[key], previous[key], new_path))
    elif isinstance(current, list):
        if len(current) != len(previous):
            changes.append(path)
        else:
            for i, (c, p) in enumerate(zip(current, previous, strict=True)):
                new_path = f"{path}[{i}]"
                changes.extend(_deep_diff(c, p, new_path))
    else:
        if current != previous:
            changes.append(path)
    return changes


def _compute_pending_changes(config: ConfigLoader) -> list[str]:
    """Compute list of field paths that differ between current config and sidecar."""
    with open(config.config_path) as f:
        raw = yaml.safe_load(f) or {}
    current_subset = _extract_restart_subset(raw)
    sidecar = _read_sidecar(config)
    if sidecar is None:
        # No sidecar or old format — assume clean state
        return []
    previous_subset = sidecar.get("subset", {})
    return _deep_diff(current_subset, previous_subset)


def _merge_update(raw: dict[str, Any], update: ConfigUpdateRequest) -> dict[str, Any]:
    merged = dict(raw)
    if update.hardware is not None:
        merged["hardware"] = merged.get("hardware") or {}
        hw = update.hardware.model_dump(exclude_none=True)
        merged["hardware"].update(hw)
    if update.safety_limits is not None:
        merged["control"] = merged.get("control") or {}
        sl = update.safety_limits.model_dump(exclude_none=True)
        merged["control"]["safety_limits"] = merged["control"].get("safety_limits") or {}
        merged["control"]["safety_limits"].update(sl)
    if update.tuning is not None:
        merged["control"] = merged.get("control") or {}
        tuning = update.tuning.model_dump(exclude_none=True)
        for key, value in tuning.items():
            if key == "pid_limits" and value is not None:
                merged["control"]["pid_limits"] = merged["control"].get("pid_limits") or {}
                for device_type, limits in value.items():
                    if limits is not None:
                        merged["control"]["pid_limits"][device_type] = limits
            else:
                merged["control"][key] = value
    return merged


@router.get("/api/config")
async def get_system_config(config: ConfigLoader = Depends(get_config)) -> dict[str, Any]:
    """Read raw YAML from disk and return hardware + control fields + pending changes + hashes."""
    with open(config.config_path) as f:
        raw = yaml.safe_load(f) or {}

    hardware = raw.get("hardware", {})
    control = raw.get("control", {})
    current_hash = _compute_restart_hash(raw)
    sidecar = _read_sidecar(config)
    pending_changes = _compute_pending_changes(config)

    return {
        "hardware": hardware,
        "safety_limits": control.get("safety_limits", {}),
        "tuning": {
            "update_interval": control.get("update_interval"),
            "last_good_hold_period": control.get("last_good_hold_period"),
            "binary_hysteresis": control.get("binary_hysteresis"),
        },
        "pid_limits": control.get("pid_limits", {}),
        "pending_restart_required_changes": pending_changes,
        "restart_hashes": {
            "current": current_hash,
            "sidecar": sidecar.get("hash") if sidecar else None,
        },
    }


@router.put("/api/config")
async def update_system_config(
    update: ConfigUpdateRequest,
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    """Acquire lock, read raw YAML, merge request, validate, write atomically, reload, return pending."""
    with config._config_lock:
        with open(config.config_path) as f:
            raw = yaml.safe_load(f) or {}

        merged = _merge_update(raw, update)

        try:
            config.validate_in_memory(merged)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        config.write_full_config(merged)

    config.reload()

    current_hash = _compute_restart_hash(merged)
    pending_changes = _compute_pending_changes(config)
    sidecar = _read_sidecar(config)

    return {
        "pending_restart_required_changes": pending_changes,
        "restart_hashes": {
            "current": current_hash,
            "sidecar": sidecar.get("hash") if sidecar else None,
        },
    }


@router.post("/api/config/restart")
async def restart_service(config: ConfigLoader = Depends(get_config)) -> dict[str, Any]:
    """Write restart-hash sidecar, schedule systemctl restart, return 202."""
    cmd = ["sudo", "-n", "systemctl", "restart", "automation-service.service"]

    # Probe sudo
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        if result.returncode != 0:
            probe = subprocess.run(
                ["sudo", "-n", "systemctl", "is-active", "automation-service.service"],
                capture_output=True,
                timeout=5,
            )
            if probe.returncode != 0 and b"a password is required" in probe.stderr.lower():
                raise HTTPException(
                    status_code=HTTP_403_FORBIDDEN,
                    detail=f"sudo probe failed: {' '.join(cmd)}",
                )
    except FileNotFoundError:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail=f"sudo probe failed: {' '.join(cmd)}",
        ) from None
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail=f"sudo probe failed: {' '.join(cmd)}",
        ) from None

    with open(config.config_path) as f:
        raw = yaml.safe_load(f) or {}
    current_hash = _compute_restart_hash(raw)
    subset = _extract_restart_subset(raw)
    _write_sidecar(config, current_hash, subset)

    asyncio.get_event_loop().call_later(
        1.0,
        lambda: subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ),
    )

    return {
        "status": "restarting",
        "delay_seconds": 1,
        "command": "sudo -n systemctl restart automation-service.service",
    }
