"""FastAPI entrypoint for the read-only monitoring service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import ClassVar, Final, final

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi import status as http_status
from pydantic import BaseModel, ConfigDict

from monitoring_service.config import SERVICE_NAME, Settings
from monitoring_service.control_repository import RuntimeControlReads
from monitoring_service.control_routes import ControlReadService, register_control_routes
from monitoring_service.database import ReadOnlyDatabase
from monitoring_service.redis_resources import RedisReadClient
from monitoring_service.readiness import (
    ReadinessProbe,
    ReadinessResponse,
    SharedReadinessProbe,
)
from monitoring_service.sensor_repository import SensorMonitoringRepository
from monitoring_service.sensor_routes import router as sensor_router

APP_TITLE: Final = "CEA Monitoring Service"
APP_VERSION: Final = "0.1.0"
sensor_reads: SensorMonitoringRepository | None = None


class HealthResponse(BaseModel):
    """The liveness response contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    status: str = "ok"
    service: str = SERVICE_NAME


@final
class RuntimeResources:
    """Own mutable read-client lifecycle state for the FastAPI application."""

    def __init__(self) -> None:
        self.database: ReadOnlyDatabase | None = None
        self.redis_client: RedisReadClient | None = None


def create_runtime_lifespan(resources: RuntimeResources):
    """Create the lifecycle that owns only read dependency clients."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings = Settings()
        database_settings, redis_settings = settings.resource_settings
        database = await ReadOnlyDatabase.connect(database_settings)
        redis_client = RedisReadClient.connect(redis_settings)
        resources.database = database
        resources.redis_client = redis_client
        global sensor_reads
        sensor_reads = SensorMonitoringRepository(database, redis_client)
        try:
            yield
        finally:
            resources.redis_client = None
            resources.database = None
            sensor_reads = None
            await redis_client.close()
            await database.close()

    return lifespan


def create_app(
    readiness_probe: ReadinessProbe | None = None, control_reads: ControlReadService | None = None
) -> FastAPI:
    """Create an app whose readiness checks expose DB and Redis separately."""
    if readiness_probe is None:
        resources = RuntimeResources()
        probe = SharedReadinessProbe(resources)
        app = FastAPI(
            title=APP_TITLE,
            version=APP_VERSION,
            lifespan=create_runtime_lifespan(resources),
        )
        reads = RuntimeControlReads(resources) if control_reads is None else control_reads
    else:
        probe = readiness_probe
        app = FastAPI(title=APP_TITLE, version=APP_VERSION)
        reads = RuntimeControlReads(RuntimeResources()) if control_reads is None else control_reads

    @app.exception_handler(RequestValidationError)
    async def max_points_validation(request: Request, exc: RequestValidationError) -> Response:
        """Translate only point-budget query validation into the monitoring 400 contract."""
        if any(error["loc"][-1] == "max_points" for error in exc.errors()):
            return JSONResponse(
                content=jsonable_encoder(exc.errors()),
                status_code=http_status.HTTP_400_BAD_REQUEST,
            )
        return await request_validation_exception_handler(request, exc)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Answer process liveness without touching Postgres or Redis."""
        return HealthResponse()

    @app.get("/ready", response_model=ReadinessResponse)
    async def ready(response: Response) -> ReadinessResponse:
        """Report independent read-dependency availability."""
        readiness = await probe.check()
        if readiness.status == "not_ready":
            response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
        return readiness

    register_control_routes(app, reads)

    app.include_router(sensor_router)

    return app


app = create_app()
