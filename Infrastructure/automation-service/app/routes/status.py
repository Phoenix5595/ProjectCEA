"""Status and health check endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime
import subprocess
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, Query
import psutil

from app.config import ConfigLoader
from app.control.performance_monitor import get_performance_monitor
from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.middleware.profiling import get_performance_metrics
from shared.health import all_ok, check_postgres_pool, check_redis_sync_client
from shared.infra_logging import get_logger

logger = get_logger(__name__)

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

# Health check caching (10 second TTL)
_health_cache: list[dict[str, Any]] | None = None
_health_cache_time: float = 0
HEALTH_CACHE_TTL: float = 10.0  # seconds


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
    """Collect host system stats (CPU, memory, disk, uptime, load, process count, Pi temp/throttle).

    Every metric is wrapped in its own try-block: psutil can raise
    ``AccessDenied``, ``NoSuchProcess``, or platform-specific OSErrors
    that we cannot meaningfully recover from at the metric-collection
    layer. We degrade gracefully (omit the missing metric, keep going)
    while logging at debug so an operator pulling logs can see *which*
    metric stopped working — pre-Phase 6 these were ``except Exception:
    pass`` which silenced real psutil regressions.
    """
    out: dict[str, Any] = {}

    def _safe(label: str, fn) -> None:
        try:
            fn()
        except Exception as e:
            logger.debug("system stat %r unavailable: %s", label, e)

    _safe("cpu_percent", lambda: out.update(cpu_percent=round(psutil.cpu_percent(interval=0.1), 1)))

    def _mem() -> None:
        vm = psutil.virtual_memory()
        out["memory_percent"] = round(vm.percent, 1)
        out["memory_used_mb"] = round(vm.used / (1024 * 1024), 0)
        out["memory_total_mb"] = round(vm.total / (1024 * 1024), 0)

    _safe("memory", _mem)
    _safe("disk_percent", lambda: out.update(disk_percent=round(psutil.disk_usage("/").percent, 1)))
    _safe(
        "uptime_seconds",
        lambda: out.update(uptime_seconds=int(time.time() - psutil.boot_time())),
    )
    _safe(
        "load_avg",
        lambda: out.update(load_avg=", ".join(f"{x:.1f}" for x in psutil.getloadavg())),
    )
    _safe("process_count", lambda: out.update(process_count=len(psutil.pids())))
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
    """Check health of known services; returns list of {name, status, latency_ms}.
    Results are cached for 10 seconds to avoid repeated HTTP calls."""

    global _health_cache, _health_cache_time

    current_time = time.perf_counter()
    # Return cached result if fresh
    if _health_cache is not None and (current_time - _health_cache_time) < HEALTH_CACHE_TTL:
        return _health_cache

    # Cache miss or stale - fetch fresh data

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
    results = await asyncio.gather(*tasks)

    _health_cache = results
    _health_cache_time = current_time

    return results


@router.get("/health")
async def health_check(
    relay_manager: RelayManager = Depends(get_relay_manager),
) -> dict[str, Any]:
    """Liveness probe. Process is responding; reports MCP state for operators.

    Does NOT touch Postgres/Redis — use /ready for hard-dependency checks.
    """
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


@router.get("/ready")
async def ready_check(
    database: DatabaseManager = Depends(get_database),
) -> Any:
    """Readiness probe.

    Verifies every hard dependency the control loop needs within 500ms via
    shared.health: postgres pool answers SELECT 1, redis pings back.
    Returns 503 on any failure so orchestrators drain traffic. MCP/hardware
    state is advisory and reported elsewhere (/health, /api/status) but
    does not fail readiness — the control loop's own degraded-mode logic
    (Phase 4) decides "fail the whole service because hardware is missing".

    Note: automation-service's Redis client is still synchronous, so we use
    ``check_redis_sync_client`` which bridges via ``asyncio.to_thread``.
    When this path migrates to ``redis.asyncio`` switch to
    ``check_redis_async_client``.
    """
    from fastapi.responses import JSONResponse

    automation_redis = getattr(database, "_automation_redis", None)
    raw_client = getattr(automation_redis, "redis_client", None) if automation_redis else None

    out: dict[str, Any] = {
        "service": "automation-service",
        "checks": {
            "postgres": await check_postgres_pool(database.pool),
            "redis": await check_redis_sync_client(raw_client),
        },
    }
    out["status"] = "ready" if all_ok(out["checks"]) else "not_ready"
    if out["status"] != "ready":
        return JSONResponse(content=out, status_code=503)
    return out


@router.get("/api/status")
async def get_status(
    database: DatabaseManager = Depends(get_database),
    relay_manager: RelayManager = Depends(get_relay_manager),
    config: ConfigLoader = Depends(get_config),
    pid_controller_manager=Depends(get_pid_controller_manager),
    health: bool = Query(default=True, description="Include service health checks"),
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

    # Optional: service health (backend, weather, etc.) - skip if health=false for faster response
    service_health = await _check_service_health() if health else []

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
