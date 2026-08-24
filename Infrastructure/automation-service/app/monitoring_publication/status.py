"""Status serialization for publication workers without Redis observation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypedDict

from .workers import MonitoringPublicationWorkers, PublicationWorkerHealth


class WorkerHealthPayload(TypedDict):
    """JSON-compatible publication health for one independent publisher."""

    running: bool
    last_success_at: str | None
    last_success_age_seconds: float | None
    last_error_at: str | None
    failed_runs: int
    last_error: str | None


class PublicationHealthPayload(TypedDict):
    """JSON-compatible health split by publication authority."""

    current: WorkerHealthPayload
    projection: WorkerHealthPayload


def publication_health_payload(
    workers: MonitoringPublicationWorkers, now: datetime | None = None
) -> PublicationHealthPayload:
    """Serialize one publication-health sample separately from control status."""
    health = workers.health_at(datetime.now(UTC) if now is None else now)
    return {
        "current": _worker_payload(health.current),
        "projection": _worker_payload(health.projection),
    }


def _worker_payload(health: PublicationWorkerHealth) -> WorkerHealthPayload:
    return {
        "running": health.running,
        "last_success_at": _timestamp(health.last_success_at),
        "last_success_age_seconds": health.last_success_age_seconds,
        "last_error_at": _timestamp(health.last_error_at),
        "failed_runs": health.failed_runs,
        "last_error": health.last_error,
    }


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
