"""FastAPI entrypoint for the grow archive service.

Importing this module is side-effect free: no directories, connections or
credentials are touched until the lifespan runs, so OpenAPI generation can
import the app without a database, Redis or the archive root.

Startup (lifespan) validates the environment, creates the owned archive
directories, opens the shared DB pool (max two connections,
``application_name=grow-archive-service``, ``statement_timeout=5000``,
``lock_timeout=1000``), refuses to run on a superuser database role, connects
the existing read-only Redis client, starts the publication-freshness guard
and installs the bounded heavy-work executor. Shutdown joins the heavy worker
before releasing the remaining resources.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, ClassVar, Final, final

from fastapi import FastAPI, Request, Response
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict

from grow_archive_service.config import (
    APPLICATION_NAME,
    DB_COMMAND_TIMEOUT_SECONDS,
    DB_LOCK_TIMEOUT_MS,
    DB_POOL_MAX_SIZE,
    DB_POOL_MIN_SIZE,
    DB_STATEMENT_TIMEOUT_MS,
    SERVICE_NAME,
    SERVICE_PORT,
    ArchiveSettings,
    ensure_archive_directories,
    load_settings,
    require_startup_auth,
)
from grow_archive_service.execution import HeavyWorkExecutor
from grow_archive_service.cache import ArchiveCache, CacheLimits
from grow_archive_service.derived_refresh import DerivedHistoryMaintenance
from grow_archive_service.dispatcher import ArchiveDispatcher, LifecycleScheduler
from grow_archive_service.exporter import ExportService
from grow_archive_service.jobs import ArchiveJobQueue
from grow_archive_service.lifecycle import LifecycleService
from grow_archive_service.capture import SourceCapture
from grow_archive_service.deletion import SourceDeletion
from grow_archive_service.freshness import PublicationFreshnessGuard
from grow_archive_service.readiness import (
    ArchiveReadinessProbe,
    ReadinessResponse,
    require_non_superuser_role,
)
from grow_archive_service.models import ArchiveError
from grow_archive_service import archive_routes, routes
from monitoring_service.redis_resources import RedisReadClient
from monitoring_service.resources import RedisResourceSettings
from shared.auth import APIKeyAuthMiddleware
from shared.db import create_pool, db_config_from_env
from shared.fastapi_helpers import docs_kwargs
from shared.infra_logging import get_logger, setup_structured_logging
from shared.lifespan import notify_started, notify_stopping
from shared.middleware import setup_cors

if TYPE_CHECKING:
    import asyncpg

APP_TITLE: Final = "CEA Grow Archive Service"
APP_VERSION: Final = "0.1.0"

logger = get_logger(__name__)


class HealthResponse(BaseModel):
    """The liveness response contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    status: str = "ok"
    service: str = SERVICE_NAME


@final
class RuntimeResources:
    """Own the archive service's runtime dependency state for the app."""

    def __init__(self) -> None:
        self.settings: ArchiveSettings | None = None
        self.pool: asyncpg.Pool | None = None
        self.redis_reader: RedisReadClient | None = None
        self.heavy_work: HeavyWorkExecutor | None = None
        self.freshness: PublicationFreshnessGuard | None = None
        self.queue: ArchiveJobQueue | None = None
        self.export_service: ExportService | None = None
        self.lifecycle: LifecycleService | None = None

    def clear(self) -> None:
        self.settings = None
        self.pool = None
        self.redis_reader = None
        self.heavy_work = None
        self.freshness = None
        self.queue = None
        self.export_service = None
        self.lifecycle = None


def create_runtime_lifespan(resources: RuntimeResources):
    """Create the lifecycle that owns the archive service's runtime resources."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        setup_structured_logging(
            service_name=SERVICE_NAME, log_level="INFO", console_output=True, json_format=True
        )
        settings = load_settings()
        require_startup_auth()
        ensure_archive_directories(settings)

        stop_event = asyncio.Event()
        pool: asyncpg.Pool | None = None
        redis_reader: RedisReadClient | None = None
        heavy_work: HeavyWorkExecutor | None = None
        freshness_task: asyncio.Task[None] | None = None
        worker_tasks: list[asyncio.Task[None]] = []
        started = False
        try:
            pool = await create_pool(
                db_config_from_env(),
                application_name=APPLICATION_NAME,
                min_size=DB_POOL_MIN_SIZE,
                max_size=DB_POOL_MAX_SIZE,
                command_timeout=DB_COMMAND_TIMEOUT_SECONDS,
                server_settings={
                    "statement_timeout": str(DB_STATEMENT_TIMEOUT_MS),
                    "lock_timeout": str(DB_LOCK_TIMEOUT_MS),
                },
            )
            await require_non_superuser_role(pool)
            redis_reader = RedisReadClient.connect(
                RedisResourceSettings(
                    redis_url=settings.redis_url,
                    timeout_seconds=settings.redis_timeout_seconds,
                )
            )
            heavy_work = HeavyWorkExecutor(duty_cycle=settings.cpu_duty_cycle)
            freshness = PublicationFreshnessGuard(redis_reader, heavy_work.control)
            cache = ArchiveCache(
                settings.cache_root, settings.staging_root,
                limits=CacheLimits(settings.local_file_limit_bytes, settings.local_cache_limit_bytes),
            )
            capture = SourceCapture(pool, heavy_work)
            exporter = ExportService(
                pool, settings.archive_root, heavy_work, capture, cache=cache
            )
            derived = DerivedHistoryMaintenance(pool, heavy_work)
            deletion = SourceDeletion(pool, heavy_work, exporter.queue, derived)
            lifecycle = LifecycleService(
                pool, exporter, deletion, derived,
                enabled=settings.lifecycle_enabled, freshness=freshness,
            )
            resources.queue = exporter.queue
            resources.export_service = exporter
            resources.lifecycle = lifecycle
            worker_tasks = [
                asyncio.create_task(LifecycleScheduler(lifecycle).run(stop_event), name="grow-archive-scheduler"),
                asyncio.create_task(ArchiveDispatcher(lifecycle).run(stop_event), name="grow-archive-dispatcher"),
            ]
            freshness_task = asyncio.create_task(
                freshness.run(stop_event), name="grow-archive-freshness"
            )

            resources.settings = settings
            resources.pool = pool
            resources.redis_reader = redis_reader
            resources.heavy_work = heavy_work
            resources.freshness = freshness
            started = True
            notify_started(SERVICE_NAME, logger)
            yield
        finally:
            if started:
                notify_stopping(SERVICE_NAME, logger)
            stop_event.set()
            for task in worker_tasks:
                task.cancel()
            if heavy_work is not None:
                heavy_work.control.request_cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            if freshness_task is not None:
                await asyncio.gather(freshness_task, return_exceptions=True)
            if heavy_work is not None:
                await heavy_work.shutdown()
            if redis_reader is not None:
                await redis_reader.close()
            if pool is not None:
                await pool.close()
            resources.clear()

    return lifespan


def create_app() -> FastAPI:
    """Create the archive app with liveness and readiness probes."""
    resources = RuntimeResources()
    app = FastAPI(
        title=APP_TITLE,
        description=(
            "Grow-aware lossless storage for Project CEA: permanent Nextcloud "
            "archives, source-resident history and explicit ZIP selection for "
            "the Historical log."
        ),
        version=APP_VERSION,
        lifespan=create_runtime_lifespan(resources),
        **docs_kwargs(),
    )
    app.state.resources = resources
    app.include_router(routes.router)
    app.include_router(archive_routes.router)

    @app.exception_handler(ArchiveError)
    async def archive_error(_: Request, error: ArchiveError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code,
                            content=jsonable_encoder({'detail': {**error.detail, 'code': error.code}}))

    probe = ArchiveReadinessProbe(resources)

    setup_cors(app, service_name=SERVICE_NAME)
    # API-key gate for /api/*. Enforcement is required at startup; /health and
    # /ready stay open for the orchestrator probes.
    app.add_middleware(APIKeyAuthMiddleware)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Answer process liveness without touching Postgres or Redis."""
        return HealthResponse()

    @app.get("/ready", response_model=ReadinessResponse)
    async def ready(response: Response) -> ReadinessResponse:
        """Report DB/schema and owned archive-root availability."""
        readiness = await probe.check()
        if readiness.status == "not_ready":
            response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
        return readiness

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT)
