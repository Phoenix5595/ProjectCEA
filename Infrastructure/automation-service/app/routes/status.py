"""Status and health check endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime
import subprocess
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends
import psutil

from app.config import ConfigLoader
from app.control.performance_monitor import get_performance_monitor
from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.middleware.profiling import get_performance_metrics

router = APIRouter()

# Service health check URLs (optional): (name, host, port, path)
# All HTTP services that expose /health (can-processor has no HTTP server)
SERVICE_HEALTH_URLS = [
    ("cea-backend", "127.0.0.1", 8000, "/health"),
    ("automation-service", "127.0.0.1", 8001, "/health"),
    ("soil-sensor-service", "127.0.0.1", 8002, "/health"),
    ("weather-service", "127.0.0.1", 8003, "/health"),
    ("onewire-worker", "127.0.0.1", 8004, "/health"),
]


# These will be overridden by main app
def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    raise RuntimeError("Dependency not injected")


def get_pid_controller_manager():
    """Dependency to get PID controller manager (for load_percent in status)."""
    raise RuntimeError("Dependency not injected")


def _get_system_stats() -> dict[str, Any] | None:
    """Collect host system stats (CPU, memory, disk, uptime, load, process count, Pi temp/throttle)."""
    out: dict[str, Any] = {}
    try:
        out["cpu_percent"] = round(psutil.cpu_percent(interval=0.1), 1)
    except Exception:
        pass
    try:
        vm = psutil.virtual_memory()
        out["memory_percent"] = round(vm.percent, 1)
        out["memory_used_mb"] = round(vm.used / (1024 * 1024), 0)
        out["memory_total_mb"] = round(vm.total / (1024 * 1024), 0)
    except Exception:
        pass
    try:
        du = psutil.disk_usage("/")
        out["disk_percent"] = round(du.percent, 1)
    except Exception:
        pass
    try:
        out["uptime_seconds"] = int(time.time() - psutil.boot_time())
    except Exception:
        pass
    try:
        load = psutil.getloadavg()
        out["load_avg"] = ", ".join(f"{x:.1f}" for x in load)
    except Exception:
        pass
    try:
        out["process_count"] = len(psutil.pids())
    except Exception:
        pass
    # Raspberry Pi CPU temp (millidegrees -> °C)
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", encoding="utf-8") as f:
            out["cpu_temp_c"] = round(int(f.read().strip()) / 1000.0, 1)
    except (FileNotFoundError, OSError, ValueError):
        pass
    # Raspberry Pi throttle (vcgencmd get_throttled)
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            raw = result.stdout.strip().split("=")[-1]
            out["throttle_status"] = raw if raw != "0x0" else "Normal"
        else:
            out["throttle_status"] = "Normal"
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return out if out else None


async def _check_service_health() -> list[dict[str, Any]]:
    """Check health of known services; returns list of {name, status, latency_ms}."""

    async def _fetch(name: str, host: str, port: int, path: str) -> dict[str, Any]:
        url = f"http://{host}:{port}{path}"
        start = time.perf_counter()
        try:
            req = Request(url, method="GET")
            async with asyncio.timeout(2.0):
                response = await asyncio.to_thread(urlopen, req, timeout=2)
            latency_ms = round((time.perf_counter() - start) * 1000)
            status = "running" if response.getcode() == 200 else "error"
            return {"name": name, "status": status, "latency_ms": latency_ms}
        except (TimeoutError, URLError, OSError, ValueError):
            latency_ms = round((time.perf_counter() - start) * 1000)
            return {"name": name, "status": "unreachable", "latency_ms": latency_ms}

    tasks = [_fetch(name, host, port, path) for name, host, port, path in SERVICE_HEALTH_URLS]
    return await asyncio.gather(*tasks)


@router.get("/health")
async def health_check(
    relay_manager: RelayManager = Depends(get_relay_manager),
) -> dict[str, Any]:
    """Health check endpoint. Includes hardware.mcp (connected, simulation)."""
    mcp = relay_manager.mcp23017
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "automation-service",
        "hardware": {
            "mcp": {
                "connected": mcp.is_connected(),
                "simulation": mcp.simulation,
            },
        },
    }


@router.get("/api/status")
async def get_status(
    database: DatabaseManager = Depends(get_database),
    relay_manager: RelayManager = Depends(get_relay_manager),
    config: ConfigLoader = Depends(get_config),
    pid_controller_manager=Depends(get_pid_controller_manager),
) -> dict[str, Any]:
    """Get full system status."""
    # Get all device states
    devices = {}
    device_states = relay_manager.get_all_states()
    pid_status: dict[str, Any] = {}
    if pid_controller_manager and hasattr(pid_controller_manager, "get_pid_status"):
        pid_status = pid_controller_manager.get_pid_status()

    redis_client = getattr(database, "_automation_redis", None)
    device_config = config.get_devices()
    for location, clusters in device_config.items():
        devices[location] = {}
        for cluster, cluster_devices in clusters.items():
            devices[location][cluster] = {}
            for device_name in cluster_devices.keys():
                key = (location, cluster, device_name)
                state = device_states.get(key, 0)
                mode = relay_manager.get_device_mode(location, cluster, device_name) or "auto"
                channel = relay_manager.get_channel(location, cluster, device_name)

                device_entry: dict[str, Any] = {
                    "state": state,
                    "mode": mode,
                    "channel": channel,
                }
                # Include light intensity from Redis (same source as device state)
                if device_name.startswith("light_") and redis_client:
                    light_data = redis_client.read_light_intensity(location, cluster, device_name)
                    if light_data and isinstance(light_data.get("intensity"), (int, float)):
                        device_entry["intensity"] = float(light_data["intensity"])
                # Include PID load% for heating/cooling/CO2 devices
                pid_key = f"{location}/{cluster}/{device_name}"
                if pid_key in pid_status and "load_percent" in pid_status[pid_key]:
                    device_entry["load_percent"] = pid_status[pid_key]["load_percent"]
                devices[location][cluster][device_name] = device_entry

    # Get sensor values
    sensors = {}
    sensor_mapping = config.get_sensor_mapping()
    for location, clusters in sensor_mapping.items():
        sensors[location] = {}
        for cluster, cluster_sensors in clusters.items():
            sensors[location][cluster] = {}
            for sensor_type, sensor_name in cluster_sensors.items():
                value = await database.sensor_repo.get_sensor_value(sensor_name)
                sensors[location][cluster][sensor_type] = value

    # Get performance metrics
    request_metrics = get_performance_metrics()
    control_metrics = get_performance_monitor().get_statistics()

    # Optional: host system stats (CPU, memory, disk, uptime, load, process count, Pi temp/throttle)
    system = _get_system_stats()

    # Optional: service health (backend, weather, etc.)
    service_health = await _check_service_health()

    # Effective setpoints from Redis (same source as control loop; dashboard uses these when backend Redis differs)
    effective_setpoints: dict[str, dict[str, dict[str, float]]] = {}
    if redis_client and redis_client.redis_enabled:
        for location, clusters in device_config.items():
            effective_setpoints[location] = {}
            for cluster in clusters:
                raw = await asyncio.to_thread(
                    redis_client.read_effective_setpoints, location, cluster
                )
                if raw:
                    effective_setpoints[location][cluster] = {
                        "heating_setpoint": raw.get("heating_setpoint"),
                        "cooling_setpoint": raw.get("cooling_setpoint"),
                        "co2_setpoint": raw.get("co2"),
                        "vpd_setpoint": raw.get("vpd"),
                    }
                    # Drop None values so frontend gets only present keys
                    effective_setpoints[location][cluster] = {
                        k: v
                        for k, v in effective_setpoints[location][cluster].items()
                        if v is not None
                    }

    result: dict[str, Any] = {
        "devices": devices,
        "sensors": sensors,
        "timestamp": datetime.now().isoformat(),
        "performance": {"api": request_metrics, "control_loop": control_metrics},
        "service_health": service_health,
    }
    if system is not None:
        result["system"] = system
    if effective_setpoints:
        result["effective_setpoints"] = effective_setpoints
    return result
