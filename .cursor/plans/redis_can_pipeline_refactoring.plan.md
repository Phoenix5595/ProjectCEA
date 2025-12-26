# Redis-Based CAN Pipeline Refactoring

## Architecture Overview

The new architecture decouples the CAN scanner from the database and introduces Redis as a real-time communication layer:

```
CAN Bus
   ↓
CAN Scanner (writes raw frames)
   ↓
Redis Stream: can:raw
   ↓
CAN Worker (decode, process, store)
   ↓              ↓
DB (persistence)   Redis Keys (live state)
                         ↓
                Dashboard Backend
                (reads Redis for live, DB for history)
```

## Current State

- **CAN Scanner** (`CAN Bus/can_scanner.py`): Writes directly to SQLite database
- **Dashboard Backend** (`Infrastructure/backend/`): Reads from database for all data
- **Database**: SQLite at `/home/antoine/Project CEA/Database/CAN_Bus/can_messages.db` (will migrate to TimescaleDB)
- **3 boxes**: Flower Room (front/back), Veg Room (main)
- **Max polling**: 1 reading per second per sensor

## Implementation Decisions

### ✅ Confirmed Decisions

1. **Sensor Naming**: Use exact database sensor names (e.g., `dry_bulb_b`, `co2_f`, `rh_v`) - location already encoded in suffix
2. **Worker Location**: `CAN Bus/can-worker/` (alongside scanner)
3. **Migration Strategy**: Parallel operation - run both old (direct DB) and new (Redis) systems initially
4. **Redis TTL**: 10 seconds for sensor state keys
5. **Redis Persistence**: Both AOF and RDB (maximum durability)
6. **Error Handling**: Fallback to database writes if Redis unavailable (temporary workaround)
7. **Database Migration**: Migrate from SQLite to TimescaleDB (local installation, preserve all existing data, no retention policies)

## Implementation Plan

### Phase 1: Infrastructure Setup (Redis + TimescaleDB)

**1.1 Install and configure Redis**

   - Install Redis server on Raspberry Pi
   - Configure persistence: Both AOF and RDB
   - Set up systemd service

**1.2 Install and configure TimescaleDB**

   - Install PostgreSQL on Raspberry Pi
   - Install TimescaleDB extension
   - Create database: `cea_sensors`
   - Create user and set permissions
   - Configure PostgreSQL for time-series workloads

**1.3 Migrate SQLite to TimescaleDB**

   - Create migration script to:
     - Read all data from SQLite `can_messages` table
     - Create TimescaleDB schema (matching SQLite structure)
     - Create hypertable on `timestamp` column for time-series optimization
     - Import all existing data
     - Verify data integrity (row counts, sample data)
   - Keep SQLite as backup during migration
   - Test queries on TimescaleDB before cutover

**1.4 Add dependencies**

   - Add `redis>=5.0.0` and `hiredis>=2.2.0` to backend requirements
   - Add `psycopg2-binary>=2.9.0` or `asyncpg>=0.28.0` for PostgreSQL/TimescaleDB
   - Add `sqlalchemy>=2.0.0` (if not already present) for database abstraction
   - Create Redis client utilities
   - Update database connection utilities for TimescaleDB

### Phase 2: Refactor CAN Scanner

**File**: `CAN Bus/can_scanner.py`

- Add Redis Stream writer (`can:raw` stream) alongside existing DB writes (parallel operation)
- Write raw CAN frames as stream entries:
  ```json
  {
    "id": "0x123",
    "data": "AA FF 04 10",
    "ts": 1734001123123,
    "dlc": 8
  }
  ```

- Keep existing database writes during migration
- Add `--redis-url` argument for configuration
- Fallback to DB-only if Redis unavailable

### Phase 3: Create CAN Worker Service

**New service**: `CAN Bus/can-worker/`

This service will:

- Read from Redis Stream `can:raw` using `XREAD`
- Decode CAN frames (reuse logic from scanner)
- Apply calibration/validation
- Write processed data to:
  - **TimescaleDB**: Full history (hypertable `can_messages` with time-series optimization)
  - **Redis Keys**: Live state for dashboard (with TTL 10s)

**Redis Key Schema** (using exact database sensor names):

- `sensor:<sensor_name>` = `<value>` (e.g., `sensor:dry_bulb_b = 23.55`)
- `sensor:<sensor_name>:ts` = `<unix_ms>` (timestamp)

**Sensor names** (from database):

- Flower Room, back: `dry_bulb_b`, `wet_bulb_b`, `co2_b`, `rh_b`, `vpd_b`, `pressure_b`, `secondary_temp_b`, `secondary_rh_b`
- Flower Room, front: `dry_bulb_f`, `wet_bulb_f`, `co2_f`, `rh_f`, `vpd_f`, `pressure_f`, `secondary_temp_f`, `secondary_rh_f`
- Veg Room, main: `dry_bulb_v`, `wet_bulb_v`, `co2_v`, `rh_v`, `vpd_v`, `pressure_v`, `secondary_temp_v`, `secondary_rh_v`
- Lab: `lab_temp`, `water_temp`

**Structure**:

```
CAN Bus/can-worker/
├── app/
│   ├── __init__.py
│   ├── main.py              # Worker entry point
│   ├── redis_client.py      # Redis Stream reader
│   ├── decoder.py           # CAN frame decoder (from scanner)
│   ├── processor.py          # Data processing & validation
│   └── writer.py            # DB + Redis writer
├── requirements.txt
└── README.md
```

### Phase 4: Update Dashboard Backend

**Files to modify**:

- `Infrastructure/backend/app/routes/sensors.py`
- `Infrastructure/backend/app/database.py`
- `Infrastructure/backend/app/background_tasks.py`

**Changes**:

1. **Add Redis client** for reading live state
2. **Modify sensor endpoints**:

   - For live values: Read from Redis keys (fast, real-time)
   - For history/graphs: Continue reading from TimescaleDB
   - **Keep existing API endpoints** for current frontend compatibility
   - Ensure API responses work with both current frontend and Grafana

3. **Update background task**:

   - Read live values from Redis instead of querying DB
   - Publish to WebSocket from Redis data
   - Keep DB queries only for historical data

4. **Grafana compatibility**:

   - TimescaleDB will be accessible directly to Grafana (PostgreSQL data source)
   - Keep REST API endpoints for current frontend
   - Ensure API response formats are compatible with both
   - No breaking changes to existing endpoints

**New Redis utilities**:

- `Infrastructure/backend/app/redis_client.py`: Redis connection and key readers

### Phase 5: Systemd Services & Autostart Configuration

**Service Startup Order** (critical dependencies):

1. **postgresql.service** (must start first - database server)
2. **redis-server.service** (can start in parallel with postgresql)
3. **can-setup.service** (initializes CAN hardware)
4. **can-scanner.service** (depends on: can-setup, redis-server)
5. **can-worker.service** (depends on: redis-server, postgresql, can-scanner)
6. **cea-backend.service** (depends on: redis-server, postgresql, can-worker)
7. **cea-frontend.service** (depends on: cea-backend)

**Service Files to Create/Update**:

1. **`CAN Bus/can-worker.service`** (new):

   - Depends on: `redis-server.service`, `postgresql.service`, `can-scanner.service`
   - On failure: Execute error handler script

2. **`CAN Bus/can-scanner.service`** (update):

   - Add dependency on: `redis-server.service`
   - On failure: Execute error handler script

3. **`Infrastructure/cea-backend.service`** (update):

   - Add dependency on: `redis-server.service`, `postgresql.service`, `can-worker.service`
   - On failure: Execute error handler script

4. **Error Handler Script** (`Infrastructure/service_error_handler.sh`):

   - Opens terminal window with error message
   - Shows service name, error details, and troubleshooting steps
   - Uses `xterm` or `gnome-terminal` depending on desktop environment

**Error Handling Configuration**:

- Each service uses `OnFailure` directive to call error handler
- Error handler script receives service name and error details
- Terminal window displays:
  - Service name that failed
  - Error message from systemd
  - Status check commands
  - Log viewing commands
  - Restart instructions

### Phase 6: Monitoring Scripts

Create two terminal monitoring scripts for real-time observation:

**1. CAN Bus Database Monitor** (`CAN Bus/monitor_can_db.sh`):

- Displays CAN messages being written to database (similar to can_scanner.py output)
- Shows: timestamp, CAN ID, node, message type, decoded values
- Updates in real-time (similar to `tail -f` behavior)
- Color-coded output for different message types
- Shows message statistics (counts per type)

**2. Redis & Backend Monitor** (`Infrastructure/monitor_redis_backend.sh`):

- Displays Redis Stream length (`can:raw`)
- Shows latest sensor values from Redis keys
- Displays backend service status
- Shows Redis memory usage
- Displays recent entries from Redis Stream
- Shows backend API health status
- Updates every 1-2 seconds

Both scripts:

- Run in separate terminal windows
- Use `watch` or custom refresh loop
- Can be launched manually or via desktop shortcuts
- Include keyboard shortcuts (Ctrl+C to exit)
- Show timestamps and service status indicators

### Phase 7: Grafana Integration

**Grafana Setup**:

1. **Install Grafana**:

   - Install Grafana on Raspberry Pi (or remote server)
   - Configure systemd service for autostart
   - Set up basic authentication

2. **Configure TimescaleDB Data Source**:

   - Add PostgreSQL data source in Grafana
   - Connect to TimescaleDB database `cea_sensors`
   - Test connection and verify query access
   - Configure connection pooling if needed

3. **Create Dashboard Templates**:

   - Create example dashboards for sensor visualization
   - Set up panels for:
     - Temperature sensors (dry_bulb, wet_bulb, secondary_temp)
     - CO2 levels
     - Humidity (RH, VPD)
     - Pressure
     - Water level
   - Configure time-series visualizations
   - Set up alerts (optional)

4. **Documentation**:

   - Create `Infrastructure/grafana/README.md` with setup instructions
   - Document TimescaleDB query examples for Grafana
   - Provide dashboard JSON exports for easy import

**Grafana Compatibility Notes**:

- **Primary method**: Grafana connects directly to TimescaleDB via PostgreSQL data source
- **REST API**: Existing FastAPI endpoints remain available for current frontend
- **No breaking changes**: Current frontend continues to work unchanged
- **Dual compatibility**: Backend supports both Grafana (direct DB access) and current frontend (REST API)

**TimescaleDB Query Examples for Grafana**:

```sql
-- Get temperature data for last hour
SELECT timestamp, value as "Temperature (°C)"
FROM can_messages
WHERE message_type = 'PT100'
  AND decoded_data->>'temp_dry_c' IS NOT NULL
  AND timestamp > NOW() - INTERVAL '1 hour'
ORDER BY timestamp;
```

### Phase 8: Testing & Validation

1. **Test Redis Stream ingestion**: Verify frames are written correctly
2. **Test CAN Worker**: Verify processing and dual writes (DB + Redis)
3. **Test Dashboard**: Verify live values from Redis, history from DB
4. **Test failure scenarios**: Redis down, DB down, worker restart
5. **Test parallel operation**: Verify both DB and Redis paths work simultaneously

## Redis Stream Schema

**Stream**: `can:raw`

- **Entry fields**:
  - `id`: CAN ID (hex string, e.g., "0x123")
  - `data`: Raw hex bytes (e.g., "AA FF 04 10")
  - `ts`: Unix timestamp in milliseconds
  - `dlc`: Data length code

## Redis State Key Schema

**Live sensor values** (TTL: 10 seconds):

- `sensor:<sensor_name>` = `<float_value>`
- `sensor:<sensor_name>:ts` = `<unix_ms>`

**Examples**:

- `sensor:dry_bulb_b = 23.55`
- `sensor:co2_f = 812`
- `sensor:rh_v = 58.2`
- `sensor:dry_bulb_b:ts = 1734001123123`

## Database Schema

**Migration from SQLite to TimescaleDB**:

**SQLite Schema** (current):

```sql
CREATE TABLE can_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    can_id INTEGER NOT NULL,
    node_id INTEGER,
    message_type TEXT,
    dlc INTEGER NOT NULL,
    raw_data TEXT NOT NULL,
    decoded_data TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**TimescaleDB Schema** (new):

```sql
CREATE TABLE can_messages (
    id BIGSERIAL,
    timestamp TIMESTAMPTZ NOT NULL,
    can_id INTEGER NOT NULL,
    node_id INTEGER,
    message_type TEXT,
    dlc INTEGER NOT NULL,
    raw_data TEXT NOT NULL,
    decoded_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create hypertable for time-series optimization
SELECT create_hypertable('can_messages', 'timestamp');

-- Create indexes for performance
CREATE INDEX idx_can_messages_timestamp ON can_messages (timestamp DESC);
CREATE INDEX idx_can_messages_node_id ON can_messages (node_id);
CREATE INDEX idx_can_messages_message_type ON can_messages (message_type);
CREATE INDEX idx_can_messages_can_id ON can_messages (can_id);
```

**Key Changes**:

- `timestamp` changed from REAL to TIMESTAMPTZ (timezone-aware)
- `decoded_data` changed from TEXT to JSONB (better querying)
- `id` changed to BIGSERIAL (supports larger datasets)
- Hypertable created on `timestamp` for time-series optimization
- Indexes optimized for time-series queries

## Migration Strategy

1. **Parallel operation**: Run both old (direct DB) and new (Redis) systems initially
2. **Monitor both paths**: Verify data consistency
3. **Gradual cutover**: Once verified, can optionally disable direct DB writes from scanner
4. **Monitor**: Watch for data consistency and performance

## Service Autostart Configuration

### Startup Order & Dependencies

Services must start in this order with proper dependencies:

```
1. postgresql.service
   └─ No dependencies (starts first - database server)

2. redis-server.service
   └─ Can start in parallel with postgresql
   └─ Redis for real-time communication

3. can-setup.service
   └─ Depends on: network-online.target
   └─ Initializes CAN hardware interface

4. can-scanner.service
   └─ Depends on: can-setup.service, redis-server.service
   └─ Writes to Redis Stream + TimescaleDB

5. can-worker.service
   └─ Depends on: redis-server.service, postgresql.service, can-scanner.service
   └─ Processes Redis Stream → TimescaleDB + Redis keys

6. cea-backend.service
   └─ Depends on: redis-server.service, postgresql.service, can-worker.service
   └─ Reads from Redis for live data, TimescaleDB for history

7. cea-frontend.service
   └─ Depends on: cea-backend.service
   └─ Current frontend dashboard (Vite/React)

8. **grafana.service** (optional)
   └─ Depends on: postgresql.service
   └─ Grafana dashboard (connects directly to TimescaleDB)
```

### Error Handling

Each service includes `OnFailure` directive that executes error handler script:

```ini
[Service]
OnFailure=service-error-handler@%n.service
```

Error handler script (`Infrastructure/service_error_handler.sh`):

- Receives service name as parameter
- Opens terminal window (xterm or gnome-terminal)
- Displays:
  - Service name and status
  - Last error message from journalctl
  - Service logs (last 50 lines)
  - Troubleshooting commands
  - Manual restart instructions
- Keeps terminal window open until user closes it

### Monitoring Scripts

**1. CAN Bus Database Monitor** (`CAN Bus/monitor_can_db.sh`):

- Queries database for recent CAN messages
- Displays formatted output similar to can_scanner.py:
  ```
  [2024-12-11 12:34:56.789] [0x101] [Node 1] PT100: Dry=23.5°C, Wet=18.2°C
  [2024bal-12-11 12:34:57.123] [0x103] [Node 1] SCD30: CO2=812ppm, Temp=23.1°C, RH=58%
  ```

- Updates every 0.5-1 second
- Shows message counts per type
- Color-coded by message type

**2. Redis & Backend Monitor** (`Infrastructure/monitor_redis_backend.sh`):

- Displays Redis Stream status:
  - Stream length: `XLEN can:raw`
  - Latest entries: `XREVRANGE can:raw + - COUNT 5`
- Shows sensor values from Redis:
  - `sensor:dry_bulb_b`, `sensor:co2_f`, `sensor:rh_v`, etc.
- Backend service status:
  - Health check: `curl http://localhost:8000/health`
  - Service status: `systemctl status cea-backend`
- Redis memory usage: `redis-cli INFO memory`
- Updates every 1-2 seconds
- Formatted table layout

Both scripts:

- Can be run manually: `./monitor_can_db.sh` or `./monitor_redis_backend.sh`
- Can be added to desktop autostart for automatic launch
- Include clear headers and timestamps
- Exit cleanly with Ctrl+C

## Future: Automation Service Integration

This architecture prepares for future automation service:

- Automation service will subscribe to Redis state keys
- React to sensor updates in real-time
- Publish actuator commands to `actuators:commands` stream
- Actuator controller will consume commands and control hardware
- AI/ML models can read sensor state from Redis keys for decision-making

## Files to Create/Modify

### New Files

- `CAN Bus/can-worker/app/main.py`
- `CAN Bus/can-worker/app/redis_client.py`
- `CAN Bus/can-worker/app/decoder.py`
- `CAN Bus/can-worker/app/processor.py`
- `CAN Bus/can-worker/app/writer.py`
- `CAN Bus/can-worker/requirements.txt`
- `CAN Bus/can-worker/README.md`
- `Infrastructure/backend/app/redis_client.py`
- `CAN Bus/can-worker.service` (systemd service file)
- `Infrastructure/service_error_handler.sh` (error handler script)
- `CAN Bus/monitor_can_db.sh` (CAN bus database monitoring script)
- `Infrastructure/monitor_redis_backend.sh` (Redis & backend monitoring script)
- `Database/migrate_sqlite_to_timescaledb.py` (SQLite to TimescaleDB migration script)
- `Database/timescaledb_setup.sql` (TimescaleDB schema and hypertable creation)
- `Infrastructure/grafana/README.md` (Grafana setup and configuration guide)
- `Infrastructure/grafana/dashboards/` (Dashboard JSON exports)

### Modified Files

- `CAN Bus/can_scanner.py` (add Redis Stream writes, keep DB writes)
- `CAN Bus/can-scanner.service` (add redis-server dependency, error handler)
- `Infrastructure/backend/app/routes/sensors.py` (add Redis reads for live data)
- `Infrastructure/backend/app/database.py` (migrate from SQLite to TimescaleDB, keep DB reads for history)
- `Infrastructure/backend/app/background_tasks.py` (read from Redis)
- `Infrastructure/backend/requirements.txt` (add redis, hiredis, psycopg2-binary or asyncpg)
- `Infrastructure/cea-backend.service` (add redis-server, postgresql, and can-worker dependencies, error handler)
- `Infrastructure/grafana.service` (systemd service file for Grafana, optional)

## Benefits

1. **Decoupling**: CAN scanner no longer depends on DB availability
2. **Buffering**: Redis Stream handles bursts without blocking
3. **Real-time**: Dashboard gets instant updates from Redis
4. **Scalability**: Easy to add more consumers (automation, logging)
5. **Reliability**: Services can restart independently
6. **Performance**: Reduced DB load for live data queries
7. **Migration safety**: Parallel operation ensures no data loss during transition
8. **Time-series optimization**: TimescaleDB provides better performance for sensor data queries, automatic data compression, and time-based partitioning
9. **Grafana compatibility**: Direct TimescaleDB access enables powerful Grafana dashboards while maintaining current frontend compatibility
10. **Dual dashboard support**: Both Grafana (direct DB) and current frontend (REST API) can access data simultaneously

## Implementation Todos

### Infrastructure Setup

1. Install and configure Redis server on Raspberry Pi with AOF + RDB persistence
2. Install PostgreSQL and TimescaleDB extension on Raspberry Pi
3. Create TimescaleDB database `cea_sensors` and configure user permissions
4. Create Database/timescaledb_setup.sql with schema and hypertable creation
5. Create Database/migrate_sqlite_to_timescaledb.py migration script
6. Run migration script to migrate existing SQLite data to TimescaleDB
7. Verify data integrity after migration
8. Add redis, hiredis, and psycopg2-binary (or asyncpg) to backend requirements.txt
9. Create Infrastructure/backend/app/redis_client.py with Redis connection utilities
10. Update Infrastructure/backend/app/database.py to use TimescaleDB instead of SQLite

### CAN Scanner & Worker

11. Refactor CAN Bus/can_scanner.py to write to Redis Stream (parallel with DB writes)
12. Create CAN Bus/can-worker/ service structure with all modules
13. Implement CAN worker: read from Redis Stream, decode frames, write to TimescaleDB and Redis state keys

### Backend Updates

14. Update Infrastructure/backend/app/routes/sensors.py to read live values from Redis keys
15. Update Infrastructure/backend/app/background_tasks.py to read from Redis for live data
16. Update Infrastructure/backend/app/database.py queries to use TimescaleDB time-series functions (if needed)

### Systemd Services & Autostart

17. Create CAN Bus/can-worker.service with proper dependencies (redis-server, postgresql, can-scanner)
18. Update CAN Bus/can-scanner.service to depend on redis-server and add error handler
19. Update Infrastructure/cea-backend.service to depend on redis-server, postgresql, and can-worker, add error handler
20. Create Infrastructure/service_error_handler.sh script that opens terminal window on service failure
21. Configure all services to autostart on boot in correct order (postgresql → redis → can-setup → can-scanner → can-worker → backend → frontend)

### Monitoring Scripts

22. Create CAN Bus/monitor_can_db.sh - displays CAN messages being written to TimescaleDB (similar to scanner output)
23. Create Infrastructure/monitor_redis_backend.sh - displays Redis Stream, sensor values, and backend status

### Grafana Integration

30. Install Grafana on Raspberry Pi (or configure remote access)
31. Configure Grafana PostgreSQL data source to connect to TimescaleDB
32. Test Grafana connection to TimescaleDB and verify query access
33. Create example Grafana dashboards for sensor visualization
34. Document Grafana setup and TimescaleDB query examples
35. Verify current frontend still works with existing API endpoints
36. Test dual compatibility: Grafana (direct DB) and current frontend (REST API)

### Testing

37. Test end-to-end: CAN scanner → Redis → Worker → TimescaleDB + Redis → Dashboard
38. Test TimescaleDB queries: verify time-series queries perform well
39. Test Grafana dashboards: verify data visualization works correctly
40. Test service autostart on boot with correct startup order (including postgresql)
41. Test error handling: verify terminal windows open when services fail
42. Test monitoring scripts: verify they display correct information
43. Verify data consistency: compare TimescaleDB data with original SQLite backup
44. Test API compatibility: verify both Grafana and current frontend can access data