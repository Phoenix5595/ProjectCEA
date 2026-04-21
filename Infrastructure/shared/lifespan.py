"""Helpers for FastAPI / asyncio service lifespan boilerplate.

Every long-running CEA service has converged on the same startup/shutdown
pattern: log a banner, call ``sd_notify("READY=1")`` once resources are up,
then on exit log another banner and call ``sd_notify("STOPPING=1")``. This
module bundles the log line with the systemd notification so adoption is a
single call per phase.

Usage (FastAPI lifespan)::

    from shared.lifespan import notify_started, notify_stopping

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting my-service...")
        # ... initialize resources ...
        notify_started("my-service", logger)
        try:
            yield
        finally:
            notify_stopping("my-service", logger)
            # ... close resources ...

The underlying ``sd_notify`` call is a no-op when ``NOTIFY_SOCKET`` is unset,
so ``Type=simple`` units are unaffected while ``Type=notify`` units are
correctly signaled. This lets us wire every service uniformly today and
promote units to ``Type=notify`` later without touching the Python code.
"""

from __future__ import annotations

import logging

from shared.systemd import notify_ready as _notify_ready
from shared.systemd import notify_stopping as _notify_stopping

__all__ = ["notify_started", "notify_stopping"]

_fallback_logger = logging.getLogger(__name__)


def notify_started(service_name: str, logger: logging.Logger | None = None) -> None:
    """Mark the service as started.

    Sends ``sd_notify("READY=1")`` and logs ``"<service_name> started"``.
    """
    log = logger or _fallback_logger
    _notify_ready()
    log.info("%s started", service_name)


def notify_stopping(service_name: str, logger: logging.Logger | None = None) -> None:
    """Mark the service as stopping.

    Sends ``sd_notify("STOPPING=1")`` and logs ``"Stopping <service_name>..."``.
    """
    log = logger or _fallback_logger
    _notify_stopping()
    log.info("Stopping %s...", service_name)
