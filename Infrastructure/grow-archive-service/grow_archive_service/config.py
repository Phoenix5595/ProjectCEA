"""Environment-backed configuration for the grow archive service.

Import-safe by design: reading the environment and touching the filesystem
happen only when :func:`load_settings`, :func:`require_startup_auth` and
:func:`ensure_archive_directories` are called from the FastAPI lifespan.
Importing this module creates no directories, opens no connections and
requires no credentials, so OpenAPI generation can import the app freely.

Environment surface (unit ``EnvironmentFile=``):

- ``ARCHIVE_ROOT`` — owned archive root (default ``/var/lib/projectcea/archives``).
- ``ARCHIVE_LIFECYCLE_ENABLED`` — destructive lifecycle gate, default ``false``.
- ``ARCHIVE_LOCAL_FILE_LIMIT_BYTES`` — per-file local ZIP copy limit (default 5 GiB).
- ``ARCHIVE_LOCAL_CACHE_LIMIT_BYTES`` — total local ZIP copy limit (default 25 GiB).
- ``ARCHIVE_WORKER_CONCURRENCY`` — bounded heavy-work threads (default 1).
- ``ARCHIVE_CPU_DUTY_CYCLE`` — maximum heavy-work CPU share (default 0.25, valid 0.05–1.0).
- ``REDIS_URL`` — shared Redis URL (read-only publication reads).
- ``POSTGRES_HOST/PORT/DB/USER/PASSWORD`` — shared DB configuration.
- ``CEA_API_KEY_REQUIRE`` / ``CEA_API_KEY`` — shared API-key enforcement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from shared.auth import auth_required
from shared.redis_client import redis_url_from_env

SERVICE_NAME: Final = "grow-archive-service"
SERVICE_PORT: Final = 8006
APPLICATION_NAME: Final = "grow-archive-service"

DEFAULT_ARCHIVE_ROOT: Final = Path("/var/lib/projectcea/archives")
DEFAULT_LIFECYCLE_ENABLED: Final = False
DEFAULT_LOCAL_FILE_LIMIT_BYTES: Final = 5_368_709_120
DEFAULT_LOCAL_CACHE_LIMIT_BYTES: Final = 26_843_545_600
DEFAULT_WORKER_CONCURRENCY: Final = 1
DEFAULT_CPU_DUTY_CYCLE: Final = 0.25
REDIS_TIMEOUT_SECONDS: Final = 2.0

# Shared pool bounds for the archive service: at most two connections, a
# five-second server-side statement budget and a one-second lock budget so a
# stuck archive query can never pin a control-loop table lock.
DB_POOL_MIN_SIZE: Final = 1
DB_POOL_MAX_SIZE: Final = 2
DB_COMMAND_TIMEOUT_SECONDS: Final = 10
DB_STATEMENT_TIMEOUT_MS: Final = 5_000
DB_LOCK_TIMEOUT_MS: Final = 1_000

_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: Final = frozenset({"", "0", "false", "no", "off"})


class ArchiveConfigurationError(RuntimeError):
    """Raised when the archive service cannot be configured or started safely."""


@dataclass(frozen=True, slots=True)
class ArchiveSettings:
    """Validated startup configuration for the grow archive service."""

    archive_root: Path
    lifecycle_enabled: bool
    local_file_limit_bytes: int
    local_cache_limit_bytes: int
    worker_concurrency: int
    redis_url: str
    redis_timeout_seconds: float = REDIS_TIMEOUT_SECONDS
    cpu_duty_cycle: float = DEFAULT_CPU_DUTY_CYCLE

    @property
    def staging_root(self) -> Path:
        """Owned staging directory for in-flight Parquet/ZIP work."""
        return self.archive_root / "staging"

    @property
    def cache_root(self) -> Path:
        """Owned directory for verified local ZIP copies."""
        return self.archive_root / "cache"


def load_settings() -> ArchiveSettings:
    """Read and validate the archive service settings from the environment.

    Raises:
        ArchiveConfigurationError: on malformed values. Missing values fall
            back to the source defaults, with lifecycle disabled.
    """
    raw_root = os.environ.get("ARCHIVE_ROOT", "").strip()
    concurrency = _parse_positive_int("ARCHIVE_WORKER_CONCURRENCY", DEFAULT_WORKER_CONCURRENCY)
    if concurrency != 1:
        raise ArchiveConfigurationError("ARCHIVE_WORKER_CONCURRENCY must be 1")
    return ArchiveSettings(
        archive_root=Path(raw_root) if raw_root else DEFAULT_ARCHIVE_ROOT,
        lifecycle_enabled=_parse_bool("ARCHIVE_LIFECYCLE_ENABLED", DEFAULT_LIFECYCLE_ENABLED),
        local_file_limit_bytes=_parse_positive_int(
            "ARCHIVE_LOCAL_FILE_LIMIT_BYTES", DEFAULT_LOCAL_FILE_LIMIT_BYTES
        ),
        local_cache_limit_bytes=_parse_positive_int(
            "ARCHIVE_LOCAL_CACHE_LIMIT_BYTES", DEFAULT_LOCAL_CACHE_LIMIT_BYTES
        ),
        worker_concurrency=concurrency,
        cpu_duty_cycle=_parse_cpu_duty_cycle(),
        redis_url=redis_url_from_env(),
    )


def require_startup_auth() -> None:
    """Refuse to start unless API-key enforcement is explicitly configured.

    The archive service exposes archive mutations and multi-gigabyte
    downloads, so the shared :class:`shared.auth.APIKeyAuthMiddleware` must be
    active: ``CEA_API_KEY_REQUIRE`` has to be ``true`` and ``CEA_API_KEY`` has
    to be nonempty. Without this gate a missing key would silently leave every
    ``/api/archives`` route unauthenticated.

    Raises:
        ArchiveConfigurationError: when either variable is missing or wrong.
    """
    if not auth_required():
        raise ArchiveConfigurationError(
            "CEA_API_KEY_REQUIRE must be 'true' for the grow archive service"
        )
    if not os.environ.get("CEA_API_KEY", "").strip():
        raise ArchiveConfigurationError(
            "CEA_API_KEY must be set to a nonempty value for the grow archive service"
        )


def ensure_archive_directories(settings: ArchiveSettings) -> None:
    """Create the owned archive root, staging and cache directories.

    Called only during service startup (FastAPI lifespan); importing this
    module never touches the filesystem. Creation is limited to the configured
    archive root subtree and fails startup when the directories cannot be
    created or written.

    Raises:
        ArchiveConfigurationError: when a directory cannot be created or is
            not writable by this process.
    """
    for directory in (settings.archive_root, settings.staging_root, settings.cache_root):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ArchiveConfigurationError(
                f"cannot create archive directory {directory}: {exc}"
            ) from exc
        if not os.access(directory, os.W_OK | os.X_OK):
            raise ArchiveConfigurationError(
                f"archive directory is not writable by this process: {directory}"
            )


def _parse_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ArchiveConfigurationError(f"{name} must be a boolean, got {raw!r}")


def _parse_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ArchiveConfigurationError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ArchiveConfigurationError(f"{name} must be >= 1, got {value}")
    return value


def _parse_cpu_duty_cycle() -> float:
    raw = os.environ.get("ARCHIVE_CPU_DUTY_CYCLE")
    if raw is None:
        return DEFAULT_CPU_DUTY_CYCLE
    try:
        value = float(raw)
    except ValueError as exc:
        raise ArchiveConfigurationError(
            "ARCHIVE_CPU_DUTY_CYCLE must be between 0.05 and 1.0"
        ) from exc
    if not 0.05 <= value <= 1.0:
        raise ArchiveConfigurationError(
            "ARCHIVE_CPU_DUTY_CYCLE must be between 0.05 and 1.0"
        )
    return value
