"""AI Training Data Export Module.

Exports time-series data in formats suitable for ML training:
- Synchronized sensor readings
- Control actions and setpoints
- Environmental conditions
"""

from __future__ import annotations

import csv
from datetime import datetime
import json

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class AIDataExporter:
    """Exports training data for AI/ML models."""

    def __init__(self, db_connection):
        self.db = db_connection

    async def export_training_data(
        self,
        start_time: datetime,
        end_time: datetime,
        location: str,
        cluster: str,
        interval_minutes: int = 5,
        output_format: str = "csv",
    ) -> str:
        """Export synchronized training data.

        Args:
            start_time: Start of export range
            end_time: End of export range
            location: Location filter
            cluster: Cluster filter
            interval_minutes: Resampling interval
            output_format: "csv" or "json"

        Returns:
            Path to exported file
        """
        query = """
        WITH time_buckets AS (
            SELECT generate_series(
                date_trunc('minute', %s),
                date_trunc('minute', %s),
                interval '%s minutes'
            ) AS bucket
        ),
        sensor_data AS (
            SELECT
                time_bucket('%s minutes', m.time) AS bucket,
                s.name AS sensor_name,
                AVG(m.value) AS value
            FROM measurement m
            JOIN sensor s ON m.sensor_id = s.sensor_id
            JOIN device d ON s.device_id = d.device_id
            WHERE m.time BETWEEN %s AND %s
              AND d.location = %s
              AND d.cluster = %s
            GROUP BY bucket, s.name
        ),
        setpoint_data AS (
            SELECT
                time_bucket('%s minutes', time) AS bucket,
                AVG(day_temp) AS temp_setpoint,
                AVG(day_humidity) AS humidity_setpoint,
                AVG(day_co2) AS co2_setpoint
            FROM effective_setpoints
            WHERE time BETWEEN %s AND %s
              AND location = %s
              AND cluster = %s
            GROUP BY bucket
        )
        SELECT
            tb.bucket AS timestamp,
            MAX(CASE WHEN sd.sensor_name = 'temperature' THEN sd.value END) AS temperature,
            MAX(CASE WHEN sd.sensor_name = 'humidity' THEN sd.value END) AS humidity,
            MAX(CASE WHEN sd.sensor_name = 'co2' THEN sd.value END) AS co2,
            MAX(CASE WHEN sd.sensor_name = 'light' THEN sd.value END) AS light,
            sp.temp_setpoint,
            sp.humidity_setpoint,
            sp.co2_setpoint
        FROM time_buckets tb
        LEFT JOIN sensor_data sd ON tb.bucket = sd.bucket
        LEFT JOIN setpoint_data sp ON tb.bucket = sp.bucket
        GROUP BY tb.bucket, sp.temp_setpoint, sp.humidity_setpoint, sp.co2_setpoint
        ORDER BY tb.bucket
        """

        interval_str = str(interval_minutes)
        params = (
            start_time,
            end_time,
            interval_str,
            interval_str,
            start_time,
            end_time,
            location,
            cluster,
            interval_str,
            start_time,
            end_time,
            location,
            cluster,
        )

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()

        filename = (
            f"/tmp/ai_training_{location}_{cluster}_{start_time.strftime('%Y%m%d')}.{output_format}"
        )

        if output_format == "csv":
            with open(filename, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "timestamp",
                        "temperature",
                        "humidity",
                        "co2",
                        "light",
                        "temp_setpoint",
                        "humidity_setpoint",
                        "co2_setpoint",
                    ]
                )
                for row in rows:
                    writer.writerow(row)
        else:
            data = []
            for row in rows:
                data.append(
                    {
                        "timestamp": row[0].isoformat() if row[0] else None,
                        "temperature": row[1],
                        "humidity": row[2],
                        "co2": row[3],
                        "light": row[4],
                        "temp_setpoint": row[5],
                        "humidity_setpoint": row[6],
                        "co2_setpoint": row[7],
                    }
                )
            with open(filename, "w") as f:
                json.dump(data, f, indent=2)

        logger.info(f"Exported {len(rows)} rows to {filename}")
        return filename

    async def get_available_ranges(self, location: str, cluster: str) -> dict:
        """Get available data ranges for a location/cluster."""
        query = """
        SELECT
            MIN(time) as earliest,
            MAX(time) as latest,
            COUNT(*) as total_rows
        FROM measurement m
        JOIN sensor s ON m.sensor_id = s.sensor_id
        JOIN device d ON s.device_id = d.device_id
        WHERE d.location = %s AND d.cluster = %s
        """
        cursor = await self.db.execute(query, (location, cluster))
        row = await cursor.fetchone()
        return {"earliest": row[0], "latest": row[1], "total_rows": row[2]}
