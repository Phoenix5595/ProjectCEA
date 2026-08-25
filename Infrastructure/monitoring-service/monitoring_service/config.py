"""Environment-backed monitoring-service configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import override

from monitoring_service.resources import DatabaseResourceSettings, RedisResourceSettings

SERVICE_NAME: Final = "monitoring-service"
SERVICE_PORT: Final = 8005


@dataclass(frozen=True, slots=True)
class ReadDependencySettings:
    """Validated connection settings for monitoring's read dependencies."""

    postgres_dsn: PostgresDsn
    redis_url: str
    postgres_pool_size: int
    postgres_acquire_timeout_seconds: float
    postgres_statement_timeout_ms: int
    redis_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class MissingReadDependencyConfigurationError(Exception):
    """Identify the required read dependency variables absent from the environment."""

    missing_variables: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return f"missing required monitoring configuration: {', '.join(self.missing_variables)}"


class Settings(BaseSettings):
    """Parse the service's read-only dependency configuration from the environment."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MONITORING_", extra="ignore", frozen=True
    )

    postgres_dsn: PostgresDsn | None = None
    redis_url: str | None = None
    postgres_pool_size: int = Field(default=8, ge=1, le=8)
    postgres_acquire_timeout_seconds: float = Field(default=10, gt=0, le=60)
    postgres_statement_timeout_ms: int = Field(default=8_000, ge=1, le=60_000)
    redis_timeout_seconds: float = Field(default=2.0, gt=0, le=60)

    @property
    def read_dependencies(self) -> ReadDependencySettings:
        """Return typed connection settings or fail before serving monitoring reads."""
        postgres_dsn = self.postgres_dsn
        redis_url = self.redis_url
        missing_variables = tuple(
            variable
            for variable, configured in (
                ("MONITORING_POSTGRES_DSN", postgres_dsn),
                ("MONITORING_REDIS_URL", redis_url),
            )
            if configured is None
        )
        if postgres_dsn is None or redis_url is None:
            raise MissingReadDependencyConfigurationError(missing_variables)
        return ReadDependencySettings(
            postgres_dsn=postgres_dsn,
            redis_url=redis_url,
            postgres_pool_size=self.postgres_pool_size,
            postgres_acquire_timeout_seconds=self.postgres_acquire_timeout_seconds,
            postgres_statement_timeout_ms=self.postgres_statement_timeout_ms,
            redis_timeout_seconds=self.redis_timeout_seconds,
        )

    @property
    def resource_settings(self) -> tuple[DatabaseResourceSettings, RedisResourceSettings]:
        """Return the bounded connection settings after parsing the environment once."""
        dependencies = self.read_dependencies
        return (
            DatabaseResourceSettings(
                postgres_dsn=str(dependencies.postgres_dsn),
                pool_size=dependencies.postgres_pool_size,
                acquire_timeout_seconds=dependencies.postgres_acquire_timeout_seconds,
                statement_timeout_ms=dependencies.postgres_statement_timeout_ms,
            ),
            RedisResourceSettings(
                redis_url=dependencies.redis_url,
                timeout_seconds=dependencies.redis_timeout_seconds,
            ),
        )
