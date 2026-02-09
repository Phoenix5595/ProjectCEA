from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Connection


async def sync_climate_setpoints_from_mode_parameters(
    conn: Connection,
    location: str,
    cluster: str,
    mode_id: str,
    submode_id: str | None,
) -> dict[str, Any]:
    """Sync climate setpoints from mode_parameters to setpoints table.

    This function reads from mode_parameters table and writes to setpoints table
    for day and night modes, mapping the fields appropriately.

    Args:
        conn: asyncpg connection object (must be passed for transaction atomicity)
        location: Location name
        cluster: Cluster name
        mode_id: Mode ID from room_modes table
        submode_id: Optional submode ID from flower_submodes table

    Returns:
        Dict with synced values including day and night setpoints

    Raises:
        Exception: If database operations fail
    """
    try:
        # Read from mode_parameters table
        if submode_id:
            mode_params_row = await conn.fetchrow(
                """
                SELECT 
                    heating_setpoint_day, cooling_setpoint_day, humidity_day,
                    heating_setpoint_night, cooling_setpoint_night, humidity_night
                FROM mode_parameters
                WHERE location = $1 AND cluster = $2 AND mode_id = $3 AND submode_id = $4
                """,
                location,
                cluster,
                mode_id,
                submode_id,
            )
        else:
            mode_params_row = await conn.fetchrow(
                """
                SELECT 
                    heating_setpoint_day, cooling_setpoint_day, humidity_day,
                    heating_setpoint_night, cooling_setpoint_night, humidity_night
                FROM mode_parameters
                WHERE location = $1 AND cluster = $2 AND mode_id = $3 AND submode_id IS NULL
                """,
                location,
                cluster,
                mode_id,
            )

        if not mode_params_row:
            logger.warning(
                f"No mode parameters found for location={location}, cluster={cluster}, "
                f"mode_id={mode_id}, submode_id={submode_id}"
            )
            return {}

        # Extract values from mode_parameters
        day_heating = mode_params_row["heating_setpoint_day"]
        day_cooling = mode_params_row["cooling_setpoint_day"]
        day_humidity = mode_params_row["humidity_day"]
        night_heating = mode_params_row["heating_setpoint_night"]
        night_cooling = mode_params_row["cooling_setpoint_night"]
        night_humidity = mode_params_row["humidity_night"]

        # Write to setpoints table for day mode
        await conn.execute(
            """
            INSERT INTO setpoints (location, cluster, mode, heating_setpoint, cooling_setpoint, humidity, updated_at)
            VALUES ($1, $2, 'day', $3, $4, $5, NOW())
            ON CONFLICT (location, cluster, mode)
            DO UPDATE SET 
                heating_setpoint = $3, 
                cooling_setpoint = $4, 
                humidity = $5, 
                updated_at = NOW()
            """,
            location,
            cluster,
            day_heating,
            day_cooling,
            day_humidity,
        )

        # Write to setpoints table for night mode
        await conn.execute(
            """
            INSERT INTO setpoints (location, cluster, mode, heating_setpoint, cooling_setpoint, humidity, updated_at)
            VALUES ($1, $2, 'night', $3, $4, $5, NOW())
            ON CONFLICT (location, cluster, mode)
            DO UPDATE SET 
                heating_setpoint = $3, 
                cooling_setpoint = $4, 
                humidity = $5, 
                updated_at = NOW()
            """,
            location,
            cluster,
            night_heating,
            night_cooling,
            night_humidity,
        )

        # Prepare result dict with synced values
        result = {
            "location": location,
            "cluster": cluster,
            "mode_id": mode_id,
            "submode_id": submode_id,
            "day_setpoints": {
                "heating_setpoint": day_heating,
                "cooling_setpoint": day_cooling,
                "humidity": day_humidity,
            },
            "night_setpoints": {
                "heating_setpoint": night_heating,
                "cooling_setpoint": night_cooling,
                "humidity": night_humidity,
            },
        }

        logger.info(
            f"Synced climate setpoints for {location}/{cluster}: "
            f"day(heat={day_heating}, cool={day_cooling}, hum={day_humidity}), "
            f"night(heat={night_heating}, cool={night_cooling}, hum={night_humidity})"
        )

        return result

    except Exception as e:
        logger.error(
            f"Failed to sync climate setpoints for location={location}, cluster={cluster}, "
            f"mode_id={mode_id}, submode_id={submode_id}: {e}"
        )
        raise
