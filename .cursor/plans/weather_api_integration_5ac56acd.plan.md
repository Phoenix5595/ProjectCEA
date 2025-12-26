---
name: Weather API Integration
overview: Create a new weather service that fetches weather data from YUL airport (CYUL) every 15 minutes using Aviation Weather Center's METAR API, stores it in TimescaleDB using the existing normalized schema, and makes it available in Grafana dashboards.
todos:
  - id: create_service_structure
    content: Create weather-service directory structure with app/ subdirectory and initial files
    status: completed
  - id: implement_weather_client
    content: Implement weather_client.py to fetch and parse METAR data from Aviation Weather Center API for CYUL
    status: completed
    dependencies:
      - create_service_structure
  - id: implement_database_manager
    content: Implement database.py to ensure Outside room/device/sensors exist and store weather measurements
    status: completed
    dependencies:
      - create_service_structure
  - id: implement_background_tasks
    content: Implement background_tasks.py with 15-minute polling loop to fetch and store weather data
    status: completed
    dependencies:
      - implement_weather_client
      - implement_database_manager
  - id: implement_fastapi_app
    content: Implement main.py with FastAPI app, lifespan manager, and routes for status/weather endpoints
    status: completed
    dependencies:
      - implement_background_tasks
  - id: create_config_files
    content: Create weather_config.yaml and requirements.txt with necessary dependencies
    status: completed
    dependencies:
      - create_service_structure
  - id: create_systemd_service
    content: Create weather-service.service systemd service file for auto-start and management
    status: completed
    dependencies:
      - implement_fastapi_app
  - id: update_documentation
    content: Update REQUIREMENTS.md, Grafana README.md, and Infrastructure README.md with weather service documentation
    status: completed
    dependencies:
      - create_systemd_service
---

# Weather API Integration for YUL Airport

## Overview

Implement a weather service that queries weather data from Montréal-Pierre Elliott Trudeau International Airport (YUL, ICAO code: CYUL) and stores it in the existing TimescaleDB schema. The service will poll every 15 minutes and collect temperature, humidity, pressure, wind speed, wind direction, and precipitation data.

## Architecture

The service follows the same pattern as `soil-sensor-service`:

- FastAPI application with background polling task
- Stores data in TimescaleDB using the normalized schema (facility → zone → room → rack → device → sensor → measurement)
- Integrates with existing "Outside" location (node_id 5) in the database
- Uses asyncpg for database operations
- Runs as a systemd service

## Implementation Details

### 1. Create Weather Service Structure

**Location**: `Infrastructure/weather-service/`Create a new service directory with:

- `app/main.py` - FastAPI application with lifespan manager
- `app/config.py` - Configuration loader (YAML-based, similar to soil-sensor-service)
- `app/database.py` - Database manager for storing weather measurements
- `app/weather_client.py` - Client for fetching METAR data from Aviation Weather Center
- `app/background_tasks.py` - Background task that polls weather API every 15 minutes
- `app/routes/` - API routes for status and weather data
- `weather_config.yaml` - Service configuration
- `requirements.txt` - Python dependencies
- `weather-service.service` - Systemd service file

### 2. Weather API Integration

**API Choice**: Aviation Weather Center METAR API

- **URL**: `https://aviationweather.gov/api/data/metar`
- **ICAO Code**: CYUL (YUL airport)
- **Format**: JSON
- **No API key required** for basic METAR data
- **Rate limit**: Reasonable for 15-minute polling

**METAR Data Parsing**:

- Parse METAR text format or use JSON if available
- Extract: temperature (°C), dewpoint (°C), pressure (hPa), wind speed (knots → m/s), wind direction (degrees), precipitation (if available)
- Calculate relative humidity from temperature and dewpoint

### 3. Database Integration

**Schema Integration**:

- Use existing "Outside" room (already mapped to node_id 5)
- Create a virtual "Weather Station" device under Outside room
- Create sensors:
- `outside_temp` (Temperature, °C)
- `outside_rh` (Relative Humidity, %)
- `outside_pressure` (Atmospheric Pressure, hPa)
- `outside_wind_speed` (Wind Speed, m/s)
- `outside_wind_direction` (Wind Direction, degrees)
- `outside_precipitation` (Precipitation, mm - if available)

**Storage**:

- Store measurements in `measurement` hypertable
- Use same timestamp for all sensors from same METAR report
- Handle missing data gracefully (some METAR reports may not include all parameters)

### 4. Configuration File

**`weather_config.yaml`**:

```yaml
weather:
  station_icao: "CYUL"  # YUL airport
  poll_interval: 900  # 15 minutes in seconds
  api_url: "https://aviationweather.gov/api/data/metar"
  
database:
  host: "localhost"
  database: "cea_sensors"
  user: "cea_user"
  password: "Lenin1917"  # Or use env var
  port: 5432

room:
  name: "Outside"
  device_name: "Weather Station YUL"
```



### 5. Background Task Implementation

**Polling Logic**:

- Fetch METAR data every 15 minutes
- Parse METAR response
- Calculate relative humidity from temperature/dewpoint
- Convert units (wind speed: knots → m/s, pressure: inches Hg → hPa if needed)
- Store all measurements in database with same timestamp
- Log errors and retry on failure

### 6. Error Handling

- Handle API timeouts and network errors
- Retry logic with exponential backoff
- Log warnings for missing data fields
- Continue operation even if one poll fails

### 7. Systemd Service

Create `weather-service.service`:

- Similar to `soil-sensor-service.service`
- Auto-start on boot
- Restart on failure
- Log to systemd journal

### 8. Documentation Updates

Update:

- `Infrastructure/database/REQUIREMENTS.md` - Document new weather sensors
- `Infrastructure/frontend/grafana/README.md` - Add query examples for outside weather
- `Infrastructure/README.md` - Document new weather service

## Files to Create/Modify

### New Files:

- `Infrastructure/weather-service/app/main.py`
- `Infrastructure/weather-service/app/config.py`
- `Infrastructure/weather-service/app/database.py`
- `Infrastructure/weather-service/app/weather_client.py`
- `Infrastructure/weather-service/app/background_tasks.py`
- `Infrastructure/weather-service/app/routes/status.py`
- `Infrastructure/weather-service/app/routes/weather.py`
- `Infrastructure/weather-service/weather_config.yaml`
- `Infrastructure/weather-service/requirements.txt`
- `Infrastructure/weather-service.service`

### Files to Modify:

- `Infrastructure/database/REQUIREMENTS.md` - Add weather sensor documentation
- `Infrastructure/frontend/grafana/README.md` - Add outside weather query examples
- `Infrastructure/README.md` - Document weather service

## Dependencies

Add to `requirements.txt`:

- `fastapi==0.104.1`
- `uvicorn[standard]==0.24.0`
- `asyncpg>=0.28.0`
- `pyyaml==6.0.1`
- `pydantic==2.5.0`
- `httpx>=0.24.0` (for async HTTP requests)
- `metar>=1.11.0` (Python METAR parser library, optional but helpful)

## Testing

After implementation:

1. Test METAR API connection and parsing
2. Verify database storage (check `measurement` table)
3. Verify Grafana can query weather data
4. Test service restart and error recoveryQ

## Notes