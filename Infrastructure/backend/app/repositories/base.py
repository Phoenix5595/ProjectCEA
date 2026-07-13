"""Base repository with asyncpg pool injection.

Mirrors the canonical pattern from
``Infrastructure/shared/base_repository.py``.
"""

from __future__ import annotations

from shared.base_repository import BaseRepository
from shared.infra_logging import get_logger

logger = get_logger(__name__)

__all__ = ["BaseRepository", "logger"]
