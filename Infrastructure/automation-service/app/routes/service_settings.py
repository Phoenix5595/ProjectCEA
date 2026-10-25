"""Service-wide settings API backed by shared settings storage."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from starlette.status import (
    HTTP_404_NOT_FOUND,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus
from app.events.mutation_context import (
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_coverage import emits_operational_mutation
from app.events.mutation_dependencies import get_mutation_event_sink, get_mutation_request_context
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext
from app.events.operational_ports import OperationalEventSink
from shared.infra_logging import get_logger
from shared.service_settings import (
    SETTINGS_SCHEMA,
    ServiceSettings,
    SettingValidationError,
    UnknownServiceError,
    validate_settings,
)

logger = get_logger(__name__)
router = APIRouter()


def get_config() -> ConfigLoader:
    from app.main import container

    return container.get_config()


def get_database() -> DatabaseManager:
    from app.main import container

    return container.get_database()


def _automation_fallback(config: ConfigLoader, service: str) -> dict[str, Any] | None:
    return config.service_settings_fallback() if service == "automation" else None


def _service_response(
    service: str,
    values: dict[str, Any],
    metadata: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    schema = SETTINGS_SCHEMA[service]
    return {
        "settings": {
            key: {
                "value": values.get(key),
                "updated_at": metadata.get(key, {}).get("updated_at"),
                "updated_by": metadata.get(key, {}).get("updated_by"),
            }
            for key in schema
        },
        "schema": {
            key: {
                "type": spec.value_type,
                "minimum": spec.minimum,
                "maximum": spec.maximum,
                "pattern": spec.pattern,
                "read_only": spec.read_only,
            }
            for key, spec in schema.items()
        },
    }


async def _metadata_by_service(pool: Any) -> dict[str, dict[str, dict[str, Any]]]:
    if pool is None:
        return {}
    try:
        rows = await pool.fetch(
            """SELECT service, key, updated_at, updated_by
                 FROM public.service_setting"""
        )
    except Exception:
        logger.warning("Unable to read service settings metadata", exc_info=True)
        return {}

    metadata: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        metadata.setdefault(row["service"], {})[row["key"]] = {
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }
    return metadata


@router.get("/api/settings")
async def get_service_settings(
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    """Return effective settings and schema metadata for all registered services."""
    pool = database.pool
    metadata = await _metadata_by_service(pool)
    services: dict[str, Any] = {}
    for service in SETTINGS_SCHEMA:
        values = await ServiceSettings.load(
            pool,
            service,
            fallback=_automation_fallback(config, service),
        )
        services[service] = _service_response(service, values, metadata.get(service, {}))
    return {"services": services}


@router.put("/api/settings/{service}")
@emits_operational_mutation
async def update_service_settings(
    service: str,
    updates: Any = Body(...),
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    """Validate and atomically persist a partial service settings update."""
    try:
        validate_settings(service, {})
        validate_settings(service, updates)
    except UnknownServiceError as error:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail={"code": error.code, "service": error.service},
        ) from error
    except SettingValidationError as error:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": error.code, "key": error.key, "reason": error.reason},
        ) from error

    pool = database.pool
    if pool is None:
        raise HTTPException(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "settings_unavailable", "reason": "database_unavailable"},
        )

    fallback = _automation_fallback(config, service)
    current = await ServiceSettings.load(pool, service, fallback=fallback)
    try:
        validated = validate_settings(service, updates, current=current)
    except SettingValidationError as error:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": error.code, "key": error.key, "reason": error.reason},
        ) from error

    if validated:
        async with (
            pool.acquire() as connection,
            connection.transaction(),
        ):
            for key, value in validated.items():
                await connection.execute(
                    """INSERT INTO public.service_setting
                           (service, key, value, updated_by)
                         VALUES ($1, $2, $3::jsonb, $4)
                         ON CONFLICT (service, key) DO UPDATE SET
                           value = EXCLUDED.value,
                           updated_at = clock_timestamp(),
                           updated_by = EXCLUDED.updated_by""",
                    service,
                    key,
                    json.dumps(value),
                    context.actor_id,
                )
            await database.config_repo.log_config_version(
                config_type="service_setting",
                author=context.actor_id,
                comment=", ".join(sorted(validated)),
                changes=validated,
                conn=connection,
            )

    if service == "automation":
        await config.refresh_service_settings()
        values = dict(config.get_control_config())
    else:
        values = await ServiceSettings.load(pool, service, fallback=fallback)

    metadata = await _metadata_by_service(pool)
    response = _service_response(service, values, metadata.get(service, {}))
    if validated:
        emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="update",
                entity=EntityContext(entity_type="service_setting", entity_id=service),
                changes=safe_allowlisted_diff(
                    before={key: current.get(key) for key in validated},
                    after={key: validated[key] for key in sorted(validated)},
                    allowed_fields=frozenset(validated),
                ),
            ),
            context,
        )
        await get_event_bus().publish(
            ConfigChangeEvent(
                event_type=ConfigEventType.SERVICE_SETTINGS_CHANGED,
                location="global",
                cluster="global",
                config_type="service_setting",
                data={
                    "service": service,
                    "keys": sorted(validated),
                    "settings": validated,
                },
            )
        )
    return response
