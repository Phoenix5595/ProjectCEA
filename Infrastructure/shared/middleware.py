"""Shared FastAPI middleware helpers.

Currently covers CORS. Grows as more cross-cutting middleware is lifted
into ``shared/``.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.infra_logging import get_logger

logger = get_logger(__name__)


def _parse_origins_env(var_name: str = "FRONTEND_ORIGINS") -> list[str]:
    raw = os.environ.get(var_name, "").strip()
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


def setup_cors(app: FastAPI, *, service_name: str = "unknown") -> None:
    """Install ``CORSMiddleware`` with a safe, env-driven origin list.

    Behavior:
      * If ``FRONTEND_ORIGINS`` is set (comma-separated list), use it as
        the explicit allow-list with ``allow_credentials=True``. This is
        the intended production configuration once we run behind Caddy.
      * If ``FRONTEND_ORIGINS`` is empty/unset, fall back to
        ``allow_origins=["*"]`` with ``allow_credentials=False``. This is
        the browser-spec-correct form of "allow anyone without cookies"
        and matches the current live backend behavior before Phase 3.

    Never sets the insecure pair ``allow_origins=["*"] +
    allow_credentials=True`` that three of our services shipped with. The
    CORS spec rejects that combination in every browser, so it was
    silently broken anyway — but it signaled intent that we want to
    fix.
    """
    origins = _parse_origins_env()
    if origins:
        logger.info(
            "CORS [%s]: explicit allow-list (%d origins), credentials on",
            service_name,
            len(origins),
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        logger.info(
            "CORS [%s]: FRONTEND_ORIGINS unset, falling back to '*' without credentials",
            service_name,
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
