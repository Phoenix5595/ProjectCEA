"""Shared exporter helpers: DAV-to-archive error translation and manifest identity checks."""
from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

from shared.nextcloud_dav import DavError

from .manifest import ArchiveManifest
from .models import ArchiveError


def _translates(function: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Map DAV fixed codes onto archive errors at the service boundary."""

    @functools.wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await function(*args, **kwargs)
        except DavError as error:
            code = error.code if error.code.startswith("archive_") else "archive_remote_unavailable"
            unavailable = code in {
                "archive_remote_unavailable", "archive_remote_quota", "archive_local_space_insufficient",
            }
            raise ArchiveError(code, status_code=503 if unavailable else 409) from error

    return wrapper


def _annual_component(manifest: Mapping[str, Any], dataset_id: UUID) -> Mapping[str, Any] | None:
    components = manifest.get("components")
    if not isinstance(components, list):
        return None
    for entry in components:
        if isinstance(entry, Mapping) and str(entry.get("dataset_id")) == str(dataset_id):
            return entry
    return None


def _require_manifest_identity(manifest: ArchiveManifest, dataset_id: UUID, revision: int) -> None:
    if manifest.dataset_id != str(dataset_id) or manifest.revision != revision:
        raise ArchiveError("archive_integrity_failed")
