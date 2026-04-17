"""Shared FastAPI app-construction helpers.

Keeps cross-service FastAPI conventions in one place so rollouts like
"disable /docs in production" don't require rewriting 5 files and missing
one.
"""

from __future__ import annotations

import os
from typing import Any


def is_production() -> bool:
    """True if ``ENV=production`` is set in the process environment.

    Default is False (development). This is the single gate for every
    prod-only switch: doc exposure, origin whitelist strictness, future
    rate-limiting defaults.
    """
    return os.environ.get("ENV", "").strip().lower() == "production"


def docs_kwargs() -> dict[str, Any]:
    """Kwargs to pass to ``FastAPI(...)`` to control OpenAPI/docs exposure.

    In production, every doc-surface endpoint (``/docs``, ``/redoc``,
    ``/openapi.json``) is disabled so an attacker who reaches the socket
    cannot enumerate routes + request schemas. In dev, left on for easy
    introspection.

    Reversible by setting ``ENV=development`` (or unsetting ENV) + restart.
    """
    if is_production():
        return {"openapi_url": None, "docs_url": None, "redoc_url": None}
    return {}
