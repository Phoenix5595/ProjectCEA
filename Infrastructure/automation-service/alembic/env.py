"""Alembic migration environment configuration."""

from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# Build database URL from environment variables (same as database.py)
def get_database_url() -> str:
    """Build PostgreSQL URL from environment variables."""
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "cea_sensors")
    user = os.getenv("POSTGRES_USER", "cea_user")
    password = os.getenv("POSTGRES_PASSWORD", "")

    if password:
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"
    else:
        # For local dev with peer auth
        return f"postgresql://{user}@{host}:{port}/{database}"


# Override sqlalchemy.url with environment-based URL
config.set_main_option("sqlalchemy.url", get_database_url())

target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
