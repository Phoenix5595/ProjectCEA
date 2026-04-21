"""Sensor data API routes."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request

from app.models import DataPoint, SensorDataResponse
from app.redis_client import get_all_sensor_values, get_sensor_timestamp, get_sensor_value
from app.redis_stream_reader import RedisStreamReader
from app.stream_processor import process_stream_entries_to_sensor_data
from shared.cluster_topology import (
    ClusterMismatchError,
    UnknownRoomError,
    assert_sensor_cluster,
)
from shared.infra_logging import get_logger
from shared.redis_keys import SENSOR_RAW_STREAM

router = APIRouter(prefix="/api/sensors", tags=["sensors"])

# Import from dependencies to avoid circular imports
from app.dependencies import get_db_manager  # noqa: E402


def _validate_sensor_cluster_or_400(location: str, cluster: str) -> None:
    """Reject sensor requests whose ``cluster`` is the wrong type for ``location``.

    Wraps ``shared.cluster_topology.assert_sensor_cluster`` and converts
    its exceptions into FastAPI ``HTTPException(400)`` responses. The
    ``hint`` field of ``ClusterMismatchError`` is forwarded verbatim so
    the API client gets a directly actionable error (e.g. "'main' is
    the device cluster for 'Flower Room'; sensor data lives under
    ['front', 'back']").
    """
    try:
        assert_sensor_cluster(location, cluster)
    except ClusterMismatchError as exc:
        raise HTTPException(status_code=400, detail=exc.hint) from exc
    except UnknownRoomError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def parse_datetime_param(param: str | None) -> datetime | None:
    """Parse datetime from query parameter (ISO string)."""
    if param is None:
        return None
    if isinstance(param, datetime):
        return param
    try:
        # Try parsing ISO format
        return datetime.fromisoformat(param.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        try:
            # Try parsing with strptime as fallback
            return datetime.strptime(param, "%Y-%m-%dT%H:%M:%S.%fZ")
        except (ValueError, AttributeError):
            return None


@router.get("/{location}/{cluster}", response_model=dict[str, SensorDataResponse])
async def get_sensor_data(
    location: str,
    cluster: str,
    request: Request,
    start_time: datetime | None = Query(None, alias="start_time"),
    end_time: datetime | None = Query(None, alias="end_time"),
    time_range: str | None = Query("1 Hour", alias="time_range"),
):
    """Get all sensor data for a location/cluster within time range.

    Args:
        location: Room name (e.g., "Flower Room")
        cluster: Sensor cluster (e.g., "front", "back", "main")
        start_time: Start timestamp (optional)
        end_time: End timestamp (optional)
        time_range: Time range string if start/end not provided (e.g., "1 Hour")
    """
    logger = get_logger(__name__)

    # Phase 5e: validate cluster against the canonical topology BEFORE
    # touching the DB. Pre-Phase-5e the wrong-type case (e.g.
    # `Flower Room/main`) silently returned an empty dict, which
    # masked frontend wiring bugs (the dashboard polled sensor URLs
    # with device-cluster names) and made every "no data" alert
    # ambiguous. We now reject up-front with a 400 + hint message,
    # so a misconfigured caller is told exactly which cluster to use.
    _validate_sensor_cluster_or_400(location, cluster)

    db = get_db_manager()

    # Debug: Log raw query parameters
    query_params = dict(request.query_params)
    print(f"\n{'=' * 80}")
    logger.debug(f"API ENDPOINT CALLED: /api/sensors/{location}/{cluster}")
    logger.debug(f"API: Raw query params: {query_params}")
    logger.debug(
        f"API: Parsed parameters - start_time: {start_time} (type: {type(start_time)}), end_time: {end_time} (type: {type(end_time)}), time_range: {time_range}"
    )
    print(f"{'=' * 80}\n")

    # Try to parse datetime from query string if FastAPI didn't parse it
    if start_time is None and "start_time" in query_params:
        start_time = parse_datetime_param(query_params["start_time"])
        logger.debug(f"API: Manually parsed start_time: {start_time}")
    if end_time is None and "end_time" in query_params:
        end_time = parse_datetime_param(query_params["end_time"])
        logger.debug(f"API: Manually parsed end_time: {end_time}")

    # Calculate time range if not provided
    # If both start_time and end_time are provided, use them directly
    # Otherwise, calculate from time_range
    if start_time is None or end_time is None:
        end_time = datetime.now()
        time_ranges = {
            "1 Minute": 60,
            "5 Minutes": 300,
            "15 Minutes": 900,
            "1 Hour": 3600,
            "6 Hours": 21600,
            "12 Hours": 43200,
            "24 Hours": 86400,
            "7 Days": 604800,
        }
        seconds = time_ranges.get(str(time_range), 3600)
        start_time = end_time - timedelta(seconds=seconds)
        logger.debug(
            f"API: Using time_range '{time_range}' ({seconds} seconds) - calculated start_time: {start_time}, end_time: {end_time}"
        )
    else:
        duration = (end_time - start_time).total_seconds()
        logger.debug(
            f"API: Using provided start_time: {start_time}, end_time: {end_time} (duration: {duration} seconds = {duration / 3600:.2f} hours)"
        )

    logger.debug(
        f"API: Fetching sensor data for {location}/{cluster} from {start_time} to {end_time}"
    )

    # Calculate duration to determine if we should check stream first
    duration_seconds = (end_time - start_time).total_seconds()
    duration_hours = duration_seconds / 3600

    # Check Redis Stream first for recent queries (within last 6 hours)
    # Stream has ~100,000 messages which covers roughly last day
    sensor_data: dict[str, list[DataPoint]] = {}
    use_stream = duration_hours <= 6  # Check stream for queries within 6 hours

    if use_stream:
        try:
            # Async-context-managed: connect + close guaranteed, no stale
            # connection leak on exception. Per-request connection for now
            # (Phase 4 may pool these).
            async with RedisStreamReader(stream_name=SENSOR_RAW_STREAM) as stream_reader:
                if stream_reader.client is None:
                    # connect() failed inside __aenter__ — fall through to DB.
                    use_stream = False
                else:
                    logger.debug(
                        f"API: Checking Redis Stream for recent data (time range: {duration_hours:.2f} hours)"
                    )
                    stream_entries = await stream_reader.read_by_time_range(
                        start_time=start_time,
                        end_time=end_time,
                        sensor_type="can",  # Only CAN sensors for this endpoint
                        max_count=20000,
                    )

                    if stream_entries:
                        logger.debug(f"API: Found {len(stream_entries)} entries in Redis Stream")
                        stream_sensor_data = process_stream_entries_to_sensor_data(
                            stream_entries, location, cluster
                        )

                        if stream_sensor_data:
                            logger.debug(
                                f"API: Processed {len(stream_sensor_data)} sensor types from Stream"
                            )
                            sensor_data = stream_sensor_data
                            total_points = sum(len(points) for points in sensor_data.values())
                            if total_points > 0:
                                logger.debug(
                                    f"API: Using data from Redis Stream ({total_points} total data points)"
                                )
                                use_stream = True
        except Exception as e:
            logger.warning(f"Error reading from Redis Stream: {e}, falling back to database")
            use_stream = False

    # If stream didn't provide data or time range is too old, query database
    if not sensor_data or not use_stream:
        logger.debug("API: Querying TimescaleDB for sensor data")
        sensor_data = await db.get_all_sensors_for_location(location, cluster, start_time, end_time)

    logger.debug(f"API: Retrieved {len(sensor_data)} sensor types: {list(sensor_data.keys())}")
    for sensor_type, data_points in sensor_data.items():
        logger.debug(f"API: {sensor_type}: {len(data_points)} data points")

    # Convert to response format
    response = {}
    for sensor_type, data_points in sensor_data.items():
        if not data_points:
            continue

        response[sensor_type] = SensorDataResponse(
            sensor_type=sensor_type,
            location=location,
            cluster=cluster,
            data=data_points,
            unit=data_points[0].unit if data_points else "",
        )

    logger.debug(f"API: Returning {len(response)} sensors in response")
    return response


@router.get("/{location}/{cluster}/live")
async def get_live_sensor_data(
    location: str,
    cluster: str,
):
    """Get current live sensor values from Redis.

    This endpoint returns the most recent sensor values from Redis state keys,
    providing real-time data without querying the database.

    Args:
        location: Room name (e.g., "Flower Room")
        cluster: Sensor cluster (e.g., "front", "back", "main")

    Returns:
        Dictionary of sensor_type -> SensorDataResponse with single latest data point
    """
    get_logger(__name__)

    # Phase 5e: same up-front validation as the historical endpoint.
    # The legacy short-circuit ("Flower Room + missing suffix → return
    # {}") is still semantically what we want, but expressing it as a
    # 400 makes the contract observable to the caller (and surfaces
    # unknown rooms instead of silently returning nothing).
    _validate_sensor_cluster_or_400(location, cluster)

    suffix = get_sensor_suffix(location, cluster)

    # Map of sensor types to check
    sensor_types = []
    if location == "Lab":
        sensor_types = ["lab_temp", "water_temperature"]
    else:
        base_types = [
            "dry_bulb",
            "wet_bulb",
            "co2",
            "rh",
            "vpd",
            "pressure",
            "secondary_temp",
            "secondary_rh",
            "water_level",
        ]
        for base_type in base_types:
            if suffix:
                sensor_types.append(f"{base_type}_{suffix}")
            else:
                sensor_types.append(base_type)

    response = {}

    # Read from Redis for each sensor
    for sensor_type in sensor_types:
        value = await get_sensor_value(sensor_type)
        if value is not None:
            ts_ms = await get_sensor_timestamp(sensor_type)
            if ts_ms:
                timestamp = datetime.fromtimestamp(ts_ms / 1000.0)
            else:
                timestamp = datetime.now()

            # Determine unit based on sensor type
            unit = (
                "°C"
                if "temp" in sensor_type or "bulb" in sensor_type
                else "ppm"
                if "co2" in sensor_type
                else "%"
                if "rh" in sensor_type
                else "kPa"
                if "vpd" in sensor_type
                else "hPa"
                if "pressure" in sensor_type
                else "mm"
                if "water_level" in sensor_type
                else ""
            )

            response[sensor_type] = SensorDataResponse(
                sensor_type=sensor_type,
                location=location,
                cluster=cluster,
                data=[DataPoint(timestamp=timestamp, value=value, unit=unit)],
                unit=unit,
            )

    return response


@router.get("/live/all")
async def get_all_live_sensor_data():
    """Get all current live sensor values from Redis.

    This endpoint returns all sensor values from Redis state keys,
    providing real-time data without querying the database.
    Useful for Grafana HTTP API datasource.

    Returns:
        List of sensor data with name, value, timestamp, and unit
    """
    get_logger(__name__)

    # Get all sensor values from Redis
    sensor_values = await get_all_sensor_values()

    if not sensor_values:
        return []

    # Unit mapping
    unit_map = {
        "temp": "°C",
        "bulb": "°C",
        "co2": "ppm",
        "rh": "%",
        "vpd": "kPa",
        "pressure": "hPa",
        "water_level": "mm",
    }

    result = []
    for sensor_name, value in sensor_values.items():
        # Get timestamp
        ts_ms = await get_sensor_timestamp(sensor_name)
        if ts_ms:
            timestamp = datetime.fromtimestamp(ts_ms / 1000.0)
        else:
            timestamp = datetime.now()

        # Determine unit
        unit = ""
        for key, u in unit_map.items():
            if key in sensor_name:
                unit = u
                break

        result.append(
            {"sensor": sensor_name, "value": value, "time": timestamp.isoformat(), "unit": unit}
        )

    return result


def get_sensor_suffix(location: str, cluster: str) -> str | None:
    if location == "Flower Room":
        if cluster == "front":
            return "f"
        if cluster == "back":
            return "b"
        return None
    elif location == "Veg Room":
        return "v"
    elif location == "Lab":
        return ""
    return None
