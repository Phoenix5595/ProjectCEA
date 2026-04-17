"""FastAPI app for 1-Wire reader service."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response, status as http_status

from shared.infra_logging import setup_structured_logging

from .background_tasks import BackgroundTasks
from .config import ConfigLoader
from .onewire_reader import read_temperature_c
from .redis_client import RedisClient

_READY_TIMEOUT_SEC = 0.5

logger = setup_structured_logging(
    service_name="onewire-worker", log_level="INFO", console_output=True, json_format=True
)

config: ConfigLoader | None = None
redis_client: RedisClient | None = None
background_tasks: BackgroundTasks | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global config, redis_client, background_tasks
    logger.info("Starting onewire-worker...")
    try:
        config = ConfigLoader()
        redis_client = RedisClient()
        await redis_client.connect()
        background_tasks = BackgroundTasks(config, redis_client)
        await background_tasks.start()
        logger.info("onewire-worker started")
        yield
    finally:
        if background_tasks:
            await background_tasks.stop()
        if redis_client:
            await redis_client.close()
        logger.info("onewire-worker stopped")


from shared.auth import APIKeyAuthMiddleware  # noqa: E402
from shared.fastapi_helpers import docs_kwargs  # noqa: E402

app = FastAPI(
    title="1-Wire Reader Service",
    version="1.0.0",
    lifespan=lifespan,
    **docs_kwargs(),  # ENV=production closes /docs, /redoc, /openapi.json.
)
# API-key gate for /api/*. No-op until CEA_API_KEY_REQUIRE=true.
app.add_middleware(APIKeyAuthMiddleware)


@app.get("/health")
async def health():
    """Liveness: process responds. Does not touch Redis."""
    return {"status": "ok", "service": "onewire-worker"}


@app.get("/ready")
async def ready(response: Response) -> dict[str, Any]:
    """Readiness: redis reachable within 500ms. Onewire has no DB dependency."""
    out: dict[str, Any] = {"service": "onewire-worker", "checks": {}}
    ok = True

    t0 = time.perf_counter()
    if redis_client is None or redis_client.client is None:
        out["checks"]["redis"] = {"ok": False, "detail": "client not initialized"}
        ok = False
    else:
        try:
            pong = await asyncio.wait_for(redis_client.client.ping(), timeout=_READY_TIMEOUT_SEC)
            out["checks"]["redis"] = {
                "ok": bool(pong),
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
            if not pong:
                ok = False
        except asyncio.TimeoutError:
            out["checks"]["redis"] = {
                "ok": False,
                "detail": f"timeout after {_READY_TIMEOUT_SEC*1000:.0f}ms",
            }
            ok = False
        except Exception as e:
            out["checks"]["redis"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
            ok = False

    if background_tasks is None or not background_tasks.running:
        out["checks"]["background_task"] = {"ok": False, "detail": "not running"}
        ok = False
    else:
        out["checks"]["background_task"] = {"ok": True}

    out["status"] = "ready" if ok else "not_ready"
    if not ok:
        response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
    return out


@app.get("/readings")
async def readings():
    """Current readings from configured devices (for debugging)."""
    if not config:
        return {}
    out = {}
    for device_id, sensor_name in config.get_devices().items():
        temp = read_temperature_c(device_id)
        out[sensor_name] = temp
    return out


@app.get("/")
async def root():
    return {"service": "1-Wire Reader Service", "version": "1.0.0", "status": "running"}
