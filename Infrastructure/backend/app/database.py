"""Database manager for TimescaleDB operations."""
import json
import math
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import asyncpg
from app.models import DataPoint, StatisticsResponse


class DatabaseManager:
    """Manages TimescaleDB database connections and queries."""
    
    MAX_DATA_POINTS = 5000
    
    def __init__(self, db_config: Optional[Dict[str, str]] = None):
        """Initialize database manager.
        
        Args:
            db_config: Database connection config dict with host, database, user, password.
                      If None, uses environment variables or defaults.
        """
        self.db_config = db_config or {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "database": os.getenv("POSTGRES_DB", "cea_sensors"),
            "user": os.getenv("POSTGRES_USER", "cea_user"),
            "password": os.getenv("POSTGRES_PASSWORD", "Lenin1917"),
            "port": int(os.getenv("POSTGRES_PORT", "5432"))
        }
        self._pool: Optional[asyncpg.Pool] = None
    
    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create connection pool."""
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    host=self.db_config["host"],
                    database=self.db_config["database"],
                    user=self.db_config["user"],
                    password=self.db_config["password"],
                    port=self.db_config["port"],
                    min_size=2,
                    max_size=10
                )
            except Exception as e:
                raise ConnectionError(f"Failed to connect to TimescaleDB: {e}")
        return self._pool
    
    async def close(self):
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
    
    async def get_all_sensors_for_location(
        self,
        location: str,
        cluster: str,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, List[DataPoint]]:
        """Get all sensor data for a location/cluster within time range.
        
        Returns dict mapping sensor_type -> list of DataPoint objects.
        Uses new normalized schema: measurement -> sensor -> device -> rack -> room
        """
        # Calculate duration to identify the caller type
        duration_seconds = (end_time - start_time).total_seconds()
        duration_hours = duration_seconds / 3600
        
        # Background task queries 60 seconds (1 minute), API should query longer ranges
        if duration_seconds <= 65:  # Background task (60 seconds + small buffer)
            prefix = "🔵 BG_TASK"
        else:
            prefix = "🟢 API_CALL"
        
        print(f"{prefix}: Querying {location}/{cluster} from {start_time} to {end_time} (duration: {duration_hours:.2f} hours)")
        
        pool = await self._get_pool()
        
        print(f"DB: Query params: location={location}, cluster={cluster}, start_time={start_time}, end_time={end_time}")
        
        # For larger time ranges, use continuous aggregates for better performance
        use_hourly = duration_hours >= 12
        use_daily = duration_hours >= 72
        
        async with pool.acquire() as conn:
            if use_daily:  # multi-day ranges, use daily aggregates
                print(f"DB: Using daily continuous aggregates for large time range")
                rows = await conn.fetch("""
                    SELECT 
                        md.time,
                        s.name as sensor_name,
                        s.unit as sensor_unit,
                        md.avg_value as value
                    FROM measurement_daily md
                    JOIN sensor s ON md.sensor_id = s.sensor_id
                    JOIN device d ON s.device_id = d.device_id
                    LEFT JOIN rack rk ON d.rack_id = rk.rack_id
                    JOIN room r ON rk.room_id = r.room_id
                    WHERE r.name = $1
                    AND md.time >= $2
                    AND md.time <= $3
                    ORDER BY md.time ASC, s.name ASC
                """, location, start_time, end_time)
            elif use_hourly:  # >=12 hours, use hourly aggregates
                print(f"DB: Using hourly continuous aggregates for medium time range")
                rows = await conn.fetch("""
                    SELECT 
                        mh.time,
                        s.name as sensor_name,
                        s.unit as sensor_unit,
                        mh.avg_value as value
                    FROM measurement_hourly mh
                    JOIN sensor s ON mh.sensor_id = s.sensor_id
                    JOIN device d ON s.device_id = d.device_id
                    LEFT JOIN rack rk ON d.rack_id = rk.rack_id
                    JOIN room r ON rk.room_id = r.room_id
                    WHERE r.name = $1
                    AND mh.time >= $2
                    AND mh.time <= $3
                    ORDER BY mh.time ASC, s.name ASC
                """, location, start_time, end_time)
            else:
                # For smaller ranges, get all data points from measurement table
                rows = await conn.fetch("""
                    SELECT 
                        m.time,
                        s.name as sensor_name,
                        s.unit as sensor_unit,
                        m.value
                    FROM measurement m
                    JOIN sensor s ON m.sensor_id = s.sensor_id
                    JOIN device d ON s.device_id = d.device_id
                    LEFT JOIN rack rk ON d.rack_id = rk.rack_id
                    JOIN room r ON rk.room_id = r.room_id
                    WHERE r.name = $1
                    AND m.time >= $2
                    AND m.time <= $3
                    ORDER BY m.time ASC, s.name ASC
                """, location, start_time, end_time)
        
        print(f"DB: Found {len(rows)} rows in database")
        
        if not rows:
            print(f"DB: No data found for {location}/{cluster}")
            return {}
        
        # Parse and organize data by sensor name
        sensor_data: Dict[str, List[DataPoint]] = {}
        
        processed_count = 0
        skipped_count = 0
        error_count = 0
        
        for row in rows:
            try:
                # Get data from row
                timestamp = row['time']
                sensor_name = row['sensor_name']
                value = row['value']
                unit = row['sensor_unit']
                
                if value is None:
                    skipped_count += 1
                    continue
                    
                # Convert timestamp if needed
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                elif not isinstance(timestamp, datetime):
                    timestamp = datetime.fromtimestamp(timestamp) if isinstance(timestamp, (int, float)) else datetime.now()
                
                # Group by sensor name
                if sensor_name not in sensor_data:
                    sensor_data[sensor_name] = []
                
                sensor_data[sensor_name].append(DataPoint(
                    timestamp=timestamp,
                    value=float(value),
                    unit=unit
                ))
                
                processed_count += 1
                
            except (KeyError, ValueError, TypeError) as e:
                error_count += 1
                if error_count <= 5:  # Only print first 5 errors to avoid spam
                    print(f"DB: Error processing row: {e}")
                continue
        
        print(f"DB: Processed {processed_count} rows, skipped {skipped_count} rows, errors {error_count} rows")
        print(f"DB: Created {len(sensor_data)} sensor types with data")
        for sensor_type, data_points in sensor_data.items():
            print(f"DB:   {sensor_type}: {len(data_points)} data points")
        
        # Downsample if too many points
        for sensor_type in sensor_data:
            if len(sensor_data[sensor_type]) > self.MAX_DATA_POINTS:
                sensor_data[sensor_type] = self._downsample(
                    sensor_data[sensor_type],
                    self.MAX_DATA_POINTS
                )
        
        return sensor_data
    
    async def get_statistics(
        self,
        sensor_type: str,
        location: str,
        cluster: str,
        start_time: datetime,
        end_time: datetime
    ) -> StatisticsResponse:
        """Get statistics (min/max/avg/std_dev) for a sensor using SQL aggregation."""
        pool = await self._get_pool()

        # Use raw measurement with SQL aggregation to avoid pulling large datasets
        query = """
            SELECT
                MIN(m.value) AS min_value,
                MAX(m.value) AS max_value,
                AVG(m.value) AS avg_value,
                COALESCE(STDDEV_POP(m.value), 0) AS std_value,
                s.unit AS sensor_unit
            FROM measurement m
            JOIN sensor s ON m.sensor_id = s.sensor_id
            JOIN device d ON s.device_id = d.device_id
            LEFT JOIN rack rk ON d.rack_id = rk.rack_id
            JOIN room r ON rk.room_id = r.room_id
            WHERE r.name = $1
              AND s.name = $2
              AND m.time >= $3
              AND m.time <= $4
        """

        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, location, sensor_type, start_time, end_time)

        if not row or row["min_value"] is None:
            return StatisticsResponse(
                sensor_type=sensor_type,
                location=location,
                cluster=cluster,
                min=0.0,
                max=0.0,
                avg=0.0,
                std_dev=0.0,
                unit=""
            )

        return StatisticsResponse(
            sensor_type=sensor_type,
            location=location,
            cluster=cluster,
            min=float(row["min_value"]),
            max=float(row["max_value"]),
            avg=float(row["avg_value"]),
            std_dev=float(row["std_value"]),
            unit=row["sensor_unit"] or ""
        )
    
    def _get_node_id(self, location: str, cluster: str) -> int:
        """Map location/cluster to CAN node ID.
        
        Node IDs from v7 NodeMapping.cpp:
        - 1: Flower Room, back
        - 2: Flower Room, front
        - 3: Veg Room, main
        """
        mapping = {
            ("Flower Room", "back"): 1,
            ("Flower Room", "front"): 2,
            ("Veg Room", "main"): 3,
            # Add more mappings as needed
            ("Lab", "main"): 4,
            ("Outside", "main"): 5,
        }
        node_id = mapping.get((location, cluster), 1)
        print(f"DB: Mapped {location}/{cluster} -> node_id={node_id}")
        return node_id
    
    def _extract_sensors(
        self,
        decoded: dict,
        message_type: str,
        location: str,
        cluster: str
    ) -> List[Tuple[str, float, str]]:
        """Extract sensor values from decoded CAN message."""
        sensors = []
        suffix = self._get_sensor_suffix(location, cluster)
        
        if message_type == "PT100":
            # Dry bulb temperature
            if 'temp_dry_c' in decoded:
                if location == "Lab":
                    sensor_key = "lab_temp"
                elif suffix:
                    sensor_key = f"dry_bulb_{suffix}"
                else:
                    sensor_key = "dry_bulb"
                sensors.append((sensor_key, float(decoded['temp_dry_c']), "°C"))
            # Wet bulb temperature
            if 'temp_wet_c' in decoded:
                sensor_key = f"wet_bulb_{suffix}" if suffix else "wet_bulb"
                sensors.append((sensor_key, float(decoded['temp_wet_c']), "°C"))
        
        elif message_type == "SCD30":
            # CO2
            if 'co2_ppm' in decoded:
                sensor_key = f"co2_{suffix}" if suffix else "co2"
                sensors.append((sensor_key, float(decoded['co2_ppm']), "ppm"))
            # Secondary temperature
            if 'temperature_c' in decoded:
                if location == "Lab":
                    sensor_key = "water_temp"
                elif suffix:
                    sensor_key = f"secondary_temp_{suffix}"
                else:
                    sensor_key = "secondary_temp"
                sensors.append((sensor_key, float(decoded['temperature_c']), "°C"))
            # Secondary RH
            if 'humidity_percent' in decoded:
                sensor_key = f"secondary_rh_{suffix}" if suffix else "secondary_rh"
                sensors.append((sensor_key, float(decoded['humidity_percent']), "%"))
        
        elif message_type == "BME280":
            # Pressure
            if 'pressure_hpa' in decoded:
                sensor_key = f"pressure_{suffix}" if suffix else "pressure"
                sensors.append((sensor_key, float(decoded['pressure_hpa']), "hPa"))
        
        elif message_type == "VL53" or message_type == "VL53L0X":
            # Water level (distance)
            if 'distance' in decoded or 'distance_mm' in decoded:
                distance_key = 'distance_mm' if 'distance_mm' in decoded else 'distance'
                sensor_key = f"water_level_{suffix}" if suffix else "water_level"
                sensors.append((sensor_key, float(decoded[distance_key]), "mm"))
        
        # Note: RH and VPD are calculated separately after collecting all data points
        # to ensure we have matching timestamps
        
        return sensors
    
    def _get_sensor_suffix(self, location: str, cluster: str) -> str:
        """Get sensor name suffix based on location and cluster."""
        if location == "Flower Room":
            return "f" if cluster == "front" else "b"
        elif location == "Veg Room":
            return "v"
        elif location == "Lab":
            return ""  # Lab sensors might not have suffix
        return ""
    
    def _calculate_rh(self, temp_dry: float, temp_wet: float, pressure: float = 1013.25) -> float:
        """Calculate relative humidity from dry and wet bulb temperatures."""
        # Simplified calculation - should match v7 implementation
        es_dry = 6.112 * math.exp((17.67 * temp_dry) / (temp_dry + 243.5))
        es_wet = 6.112 * math.exp((17.67 * temp_wet) / (temp_wet + 243.5))
        e = es_wet - 0.000662 * pressure * (temp_dry - temp_wet)
        rh = (e / es_dry) * 100.0
        return max(0.0, min(100.0, rh))
    
    def _calculate_vpd(self, temp_dry: float, temp_wet: float, pressure: float = 1013.25) -> float:
        """Calculate VPD from dry and wet bulb temperatures."""
        es = 6.112 * math.exp((17.67 * temp_dry) / (temp_dry + 243.5))
        ea = 6.112 * math.exp((17.67 * temp_wet) / (temp_wet + 243.5)) - 0.000662 * pressure * (temp_dry - temp_wet)
        vpd = (es - ea) / 10.0  # Convert to kPa
        return max(0.0, vpd)
    
    def _downsample(self, data: List[DataPoint], target_points: int) -> List[DataPoint]:
        """Downsample data to target number of points."""
        if len(data) <= target_points:
            return data
        
        step = len(data) / target_points
        indices = [int(i * step) for i in range(target_points)]
        return [data[i] for i in indices if i < len(data)]
    

