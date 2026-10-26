"""RS-485 soil placement reads: bed geometry, heatmap points and bus status (R9)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.sensor_registry_models import (
    RS485_BEDS,
    SOIL_HEATMAP_STALE_HOURS,
    SOIL_HEATMAP_WINDOW_MINUTES,
    SoilBed,
    SoilBusStatus,
    SoilHeatmapBed,
    SoilHeatmapPoint,
    SoilHeatmapResponse,
)
from shared.infra_logging import get_logger

from .sensor_registry_channels import FLOWER_ROOM
from .soil_sensor_repository import SOIL_WIRE_TO_METRIC

logger = get_logger(__name__)

SOIL_METRIC_TO_WIRE: dict[str, str] = {metric: wire for wire, metric in SOIL_WIRE_TO_METRIC.items()}
BED_DISPLAY_ORDER: tuple[str, ...] = ("Back Bed", "Front Bed")

_SELECT_BEDS = """
    SELECT r.rack_id, r.name,
           COALESCE(b.width_ft, 4.0) AS width_ft,
           COALESCE(b.length_ft, 4.0) AS length_ft
    FROM rack r
    JOIN room f ON f.room_id = r.room_id
    LEFT JOIN soil_bed_profile b ON b.rack_id = r.rack_id
    WHERE f.name = $1 AND r.name = ANY($2::text[])
"""

# One row per bed placement still inside the fade window. The value is the
# mean of the probe's metric channel over the last ten minutes the
# placement was open (ending now for active placements).
_SELECT_HEATMAP_POINTS = """
    WITH placements AS (
        SELECT p.placement_id, p.registry_id, p.rack_id, p.x_frac, p.y_frac, p.removed_at,
               sr.hardware_address, sr.device_id,
               COALESCE(p.removed_at, clock_timestamp()) AS window_end,
               GREATEST(
                   p.placed_at,
                   COALESCE(p.removed_at, clock_timestamp()) - make_interval(mins => $3)
               ) AS window_start
        FROM soil_probe_placement p
        JOIN sensor_registry sr ON sr.registry_id = p.registry_id
        WHERE p.rack_id IS NOT NULL
          AND (p.removed_at IS NULL
               OR p.removed_at >= clock_timestamp() - make_interval(hours => $2))
    )
    SELECT pl.registry_id, pl.hardware_address, pl.rack_id, pl.x_frac, pl.y_frac,
           pl.removed_at, avg(m.value) AS value
    FROM placements pl
    JOIN sensor s ON s.device_id = pl.device_id AND s.name LIKE '%\\_' || $1
    JOIN measurement m ON m.sensor_id = s.sensor_id
                       AND m.time >= pl.window_start AND m.time <= pl.window_end
    GROUP BY pl.placement_id, pl.registry_id, pl.hardware_address, pl.rack_id,
             pl.x_frac, pl.y_frac, pl.removed_at
    ORDER BY pl.hardware_address, pl.placement_id
"""


class SoilPlacementRepositoryMixin:
    async def soil_beds(self) -> list[SoilBed]:
        """Flower Room soil beds with dimensions, Back Bed then Front Bed."""
        async with self._acquire() as conn:
            rows = await conn.fetch(_SELECT_BEDS, FLOWER_ROOM, list(RS485_BEDS))
        beds = [
            SoilBed(
                rack_id=int(row["rack_id"]),
                name=row["name"],
                width_ft=float(row["width_ft"]),
                length_ft=float(row["length_ft"]),
            )
            for row in rows
        ]
        return sorted(beds, key=lambda bed: BED_DISPLAY_ORDER.index(bed.name))

    async def soil_heatmap(self, metric: str) -> SoilHeatmapResponse:
        """Current and recently closed bed placements with their mean metric value."""
        wire_key = SOIL_METRIC_TO_WIRE[metric]
        beds = await self.soil_beds()
        async with self._acquire() as conn:
            rows = await conn.fetch(
                _SELECT_HEATMAP_POINTS,
                wire_key,
                SOIL_HEATMAP_STALE_HOURS,
                SOIL_HEATMAP_WINDOW_MINUTES,
            )
        return SoilHeatmapResponse(
            metric=metric,
            stale_after_hours=SOIL_HEATMAP_STALE_HOURS,
            beds=self._heatmap_beds(beds, rows),
        )

    @staticmethod
    def _heatmap_beds(beds: list[SoilBed], rows: list[Any]) -> list[SoilHeatmapBed]:
        points: dict[int, list[SoilHeatmapPoint]] = {bed.rack_id: [] for bed in beds}
        for row in rows:
            if row["value"] is None or row["rack_id"] not in points:
                continue
            points[row["rack_id"]].append(
                SoilHeatmapPoint(
                    registry_id=int(row["registry_id"]),
                    hardware_address=int(row["hardware_address"]),
                    x_frac=float(row["x_frac"]),
                    y_frac=float(row["y_frac"]),
                    value=float(row["value"]),
                    active=row["removed_at"] is None,
                    removed_at=row["removed_at"],
                )
            )
        return [SoilHeatmapBed(**bed.model_dump(), points=points[bed.rack_id]) for bed in beds]

    async def soil_bus_status(self) -> SoilBusStatus:
        """Bus state published by the soil service; ``unknown`` when absent."""
        from app.redis_client import get_redis_client
        from shared.redis_keys import SOIL_BUS_STATUS_KEY

        unknown = SoilBusStatus(state="unknown", address=None, at=None)
        client = await get_redis_client()
        if client is None:
            return unknown
        raw = await client.get(SOIL_BUS_STATUS_KEY)
        if raw is None:
            return unknown
        try:
            return SoilBusStatus.model_validate_json(raw)
        except ValidationError as exc:
            logger.warning(f"Invalid {SOIL_BUS_STATUS_KEY} payload: {exc}")
            return unknown
