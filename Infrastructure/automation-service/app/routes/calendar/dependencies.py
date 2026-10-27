"""Calendar route dependencies; app.routes.routes.setup_dependency_overrides injects the database."""

from __future__ import annotations

from app.database import DatabaseManager


def get_database() -> DatabaseManager:
    raise RuntimeError("Dependency not injected")
