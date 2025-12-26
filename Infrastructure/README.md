# CEA Infrastructure

Complete infrastructure documentation for the Controlled Environment Agriculture (CEA) project.

## Table of Contents

1. [Services Structure](#services-structure)
2. [Setup Guide](#setup-guide)
3. [Quick Reference](#quick-reference)
4. [Data Storage Pattern](#data-storage-pattern)
5. [Redis Setup and Troubleshooting](#redis-setup-and-troubleshooting)
6. [TimescaleDB Installation](#timescaledb-installation)
7. [Deployment Checklist](#deployment-checklist)
8. [Service Management](#service-management)

---

## Services Structure

All services are organized in the `Infrastructure/` directory, each in its own service folder.

### Service Directories

```
Infrastructure/
├── can-processor-service/     # CAN Bus processing service
├── soil-sensor-service/        # RS485 soil sensor service
├── weather-service/            # Weather API service (YUL Airport)
├── automation-service/         # Device control and automation service
└── backend/                     # Backend API service (CEA Dashboard)
```

### Service Files

All service files are located in `Infrastructure/`:

- `can-processor-service.service` - CAN processor systemd service
- `soil-sensor-service.service` - Soil sensor systemd service
- `weather-service.service` - Weather API service
- `automation-service.service` - Automation service systemd service
- `cea-backend.service` - Backend API service

### Installation

Service files should be copied to `/etc/systemd/system/`:

```bash
cd "/home/antoine/Project CEA/Infrastructure"
sudo cp can-processor-service.service /etc/systemd/system/
sudo cp soil-sensor-service.service /etc/systemd/system/
sudo cp weather-service.service /etc/systemd/system/
sudo cp automation-service.service /etc/systemd/system/
sudo cp cea-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### Service Dependencies

```
can-setup.service
    ↓
redis-server.service
postgresql.service
    ↓
can-processor.service
soil-sensor-service.service
weather-service.service
    ↓
cea-backend.service
automation-service.service
```

---

## Setup Guide

Complete setup guide for Redis, TimescaleDB, CAN Processor, and Grafana integration.

### Prerequisites

- Raspberry Pi running Linux
- Python 3.8+
- CAN bus interface configured (`can0`)
- Existing SQLite database with sensor data (for migration)

### Installation Order

#### 1. Install Redis

```bash
cd "/home/antoine/Project CEA/Infrastructure"
sudo ./setup_redis.sh
```

Verify:
```bash
redis-cli ping
# Should return: PONG
```

#### 2. Install TimescaleDB

```bash
cd "/home/antoine/Project CEA/Infrastructure"
sudo ./setup_timescaledb.sh
```

**Important**: Change the default password after installation!

```bash
sudo -u postgres psql -c "ALTER USER cea_user WITH PASSWORD 'your_secure_password';"
```

#### 3. Create TimescaleDB Schema

```bash
psql -h localhost -U cea_user -d cea_sensors -f "/home/antoine/Project CEA/Database/timescaledb_setup.sql"
```

#### 4. Migrate SQLite Data (if needed)

**Note:** Migration from SQLite to TimescaleDB has been completed. If you need to migrate additional data, you would need to create a custom migration script.

#### 5. Install Python Dependencies

```bash
# Backend dependencies
cd "/home/antoine/Project CEA/Infrastructure/backend"
pip3 install -r requirements.txt

# CAN processor dependencies
cd "/home/antoine/Project CEA/Infrastructure/can-processor-service"
pip3 install -r requirements.txt

# Soil sensor service dependencies
cd "/home/antoine/Project CEA/Infrastructure/soil-sensor-service"
pip3 install -r requirements.txt

# Automation service dependencies
cd "/home/antoine/Project CEA/Infrastructure/automation-service"
pip3 install -r requirements.txt
```

#### 6. Install Systemd Services

```bash
# CAN Processor service
sudo cp "/home/antoine/Project CEA/Infrastructure/can-processor-service.service" /etc/systemd/system/

# Soil sensor service
sudo cp "/home/antoine/Project CEA/Infrastructure/soil-sensor-service.service" /etc/systemd/system/

# Automation service
sudo cp "/home/antoine/Project CEA/Infrastructure/automation-service.service" /etc/systemd/system/

# Backend service
sudo cp "/home/antoine/Project CEA/Infrastructure/cea-backend.service" /etc/systemd/system/

# Error handler service
sudo cp "/home/antoine/Project CEA/Infrastructure/service-error-handler@.service" /etc/systemd/system/
sudo systemctl daemon-reload
```

#### 7. Update Service Environment Variables

Edit service files to set correct passwords:

```bash
sudo nano /etc/systemd/system/can-processor.service
sudo nano /etc/systemd/system/cea-backend.service
```

Update:
- `POSTGRES_PASSWORD=your_secure_password`

#### 8. Enable and Start Services

**Start in order:**

```bash
# 1. PostgreSQL (if not already running)
sudo systemctl enable postgresql
sudo systemctl start postgresql

# 2. Redis (if not already running)
sudo systemctl enable redis-server
sudo systemctl start redis-server

# 3. CAN Setup
sudo systemctl enable can-setup
sudo systemctl start can-setup

# 4. CAN Processor
sudo systemctl enable can-processor
sudo systemctl start can-processor

# 5. Soil Sensor Service
sudo systemctl enable soil-sensor-service
sudo systemctl start soil-sensor-service

# 6. Backend
sudo systemctl enable cea-backend
sudo systemctl start cea-backend

# 7. Automation Service
sudo systemctl enable automation-service
sudo systemctl start automation-service

# 8. Frontend (optional)
# Optional: Enable Grafana (if installed)
sudo systemctl enable grafana-server
sudo systemctl start grafana-server
```

#### 9. Verify Services

```bash
# Check all services
systemctl status redis-server postgresql can-setup can-processor soil-sensor-service cea-backend automation-service

# Check logs
journalctl -u can-processor -f
journalctl -u soil-sensor-service -f
journalctl -u cea-backend -f
journalctl -u automation-service -f
```

#### 10. Test Redis Stream

```bash
# Check stream length
redis-cli XLEN sensor:raw

# View latest entries
redis-cli XREVRANGE sensor:raw + - COUNT 5

# Check sensor values
redis-cli MGET sensor:dry_bulb_b sensor:co2_f sensor:rh_v
```

#### 11. Test TimescaleDB

```bash
# Connect and query
psql -h localhost -U cea_user -d cea_sensors

# Check recent data
SELECT COUNT(*) FROM measurement;
SELECT * FROM measurement ORDER BY time DESC LIMIT 5;
```

#### 12. Test Backend API

```bash
# Health check
curl http://localhost:8000/health

# Live sensor data
curl http://localhost:8000/api/sensors/Flower%20Room/back/live

# Historical data
curl "http://localhost:8000/api/sensors/Flower%20Room/back?time_range=1%20Hour"
```

### Service Startup Order

Services must start in this order:

1. `postgresql.service` - TimescaleDB database server
2. `redis-server.service` - Redis cache and real-time data
3. `can-setup.service` - CAN interface initialization
4. `can-processor.service` - CAN message processing
5. `soil-sensor-service.service` - Soil sensor monitoring
6. `cea-backend.service` - Main backend API (port 8000)
7. `automation-service.service` - Automation service (port 8001)
8. `grafana-server.service` (optional, for Grafana dashboard)

Dependencies are configured in service files to ensure correct startup order.

---

## Quick Reference

### Service Management

#### Start All Services
```bash
sudo systemctl start postgresql redis-server can-setup can-processor soil-sensor-service cea-backend automation-service grafana-server
```

#### Stop All Services
```bash
sudo systemctl stop grafana-server automation-service cea-backend soil-sensor-service can-processor can-setup redis-server postgresql
```

#### Check Service Status
```bash
systemctl status postgresql redis-server can-setup can-processor soil-sensor-service cea-backend automation-service
```

#### View Logs
```bash
# CAN Processor
journalctl -u can-processor -f

# Soil Sensor Service
journalctl -u soil-sensor-service -f

# Backend
journalctl -u cea-backend -f

# Automation Service
journalctl -u automation-service -f

# All services
journalctl -u can-processor -u soil-sensor-service -u cea-backend -u automation-service -f
```

### Redis Commands

#### Check Stream
```bash
# Stream length (unified stream for all sensors)
redis-cli XLEN sensor:raw

# Latest entries
redis-cli XREVRANGE sensor:raw + - COUNT 10

# Filter by type (can, soil, automation)
redis-cli XREVRANGE sensor:raw + - COUNT 10 | grep -A 5 "type"

# Monitor all commands
redis-cli MONITOR
```

#### Check Sensor Values
```bash
# Single sensor
redis-cli GET sensor:dry_bulb_b

# Multiple sensors
redis-cli MGET sensor:dry_bulb_b sensor:co2_f sensor:rh_v

# All sensor keys
redis-cli KEYS sensor:*

# Sensor with timestamp
redis-cli GET sensor:dry_bulb_b:ts
```

### TimescaleDB Commands

#### Connect
```bash
psql -h localhost -U cea_user -d cea_sensors
```

#### Common Queries
```sql
-- Row count
SELECT COUNT(*) FROM measurement;

-- Recent data
SELECT * FROM measurement ORDER BY time DESC LIMIT 10;

-- Data by sensor
SELECT COUNT(*) FROM measurement WHERE sensor_id = 1;

-- Time range query
SELECT * FROM measurement 
WHERE time > NOW() - INTERVAL '1 hour'
ORDER BY time;
```

### API Endpoints

#### Health Check
```bash
# Main Backend
curl http://localhost:8000/health

# Automation Service
curl http://localhost:8001/health
```

#### Live Sensor Data (Redis)
```bash
curl http://localhost:8000/api/sensors/Flower%20Room/back/live
```

#### Historical Data (TimescaleDB)
```bash
curl "http://localhost:8000/api/sensors/Flower%20Room/back?time_range=1%20Hour"
```

#### Statistics
```bash
curl "http://localhost:8000/api/statistics/dry_bulb_b/Flower%20Room/back?time_range=24%20Hours"
```

### Monitoring Scripts

#### CAN Processor Monitor
```bash
"/home/antoine/Project CEA/monitor_can_processor.sh"
```
Monitors CAN processor service, CAN bus interface, Redis stream, database writes, and recent CAN messages.

#### Redis Stream Monitor
```bash
"/home/antoine/Project CEA/monitor_redis_stream.sh"
```
Monitors Redis stream (`sensor:raw`) and displays live sensor values.

### Troubleshooting

#### Service Won't Start
1. Check error handler terminal (opens automatically)
2. Check logs: `journalctl -u <service> -n 50`
3. Check dependencies: `systemctl list-dependencies <service>`

#### No Data in Redis
- Verify CAN processor: `systemctl status can-processor`
- Check Redis: `redis-cli ping`
- Check stream: `redis-cli XLEN sensor:raw`

#### No Data in TimescaleDB
- Verify CAN processor: `systemctl status can-processor`
- Check database: `psql -d cea_sensors -c "SELECT COUNT(*) FROM measurement;"`
- Check processor logs: `journalctl -u can-processor -f`

#### Backend Not Responding
- Check backend: `systemctl status cea-backend`
- Check Redis connection: Backend needs Redis for live data
- Check TimescaleDB connection: Backend needs DB for history
- Test API: `curl http://localhost:8000/health`

### File Locations

- **CAN Processor**: `/home/antoine/Project CEA/Infrastructure/can-processor-service/`
- **Backend**: `/home/antoine/Project CEA/Infrastructure/backend/` (port 8000)
- **Automation Service**: `/home/antoine/Project CEA/Infrastructure/automation-service/` (port 8001)
- **Database**: TimescaleDB `cea_sensors` database
- **Redis Config**: `/etc/redis/redis.conf`
- **Service Files**: `/etc/systemd/system/`

### Environment Variables

Set in service files or `.env`:

```bash
REDIS_URL=redis://localhost:6379
POSTGRES_HOST=localhost
POSTGRES_DB=cea_sensors
POSTGRES_USER=cea_user
POSTGRES_PASSWORD=your_password
```

---

## Data Storage Pattern

This document describes the unified data storage and querying pattern used across all CEA services. **All new services must follow this pattern.**

### Overview

All services that generate or process sensor/automation data must implement a **three-tier storage strategy**:

1. **Redis Stream (`sensor:raw`)** - Recent history buffer (100,000 messages, MAXLEN)
2. **TimescaleDB** - Full historical data (hypertables for time-series)
3. **Redis State Keys** - Live values for frontend (TTL: 10 seconds)

### Architecture

```
Service (CAN/Soil/Automation)
    ├─→ Redis Stream (sensor:raw) - Recent history (100K messages)
    ├─→ TimescaleDB - Full history (hypertables)
    └─→ Redis State Keys (sensor:* or automation:*) - Live values (TTL 10s)
            ↓
        Backend Service (reads from Redis state + Stream/DB)
            ↓
        Frontend (Grafana)
```

### Storage Requirements

#### 1. Redis Stream (`sensor:raw`)

**Purpose**: Recent history buffer for efficient querying without hitting the database.

**Format**:
- **Stream Name**: `sensor:raw` (unified for all services)
- **MAXLEN**: 100,000 messages (automatic trimming)
- **Type Marker**: Each entry must include `type` field:
  - `type=can` for CAN sensor data
  - `type=soil` for soil sensor data
  - `type=automation` for automation state data

**Entry Format**:
```python
stream_data = {
    b'id': "...",           # Unique entry ID
    b'ts': "1234567890",    # Timestamp in milliseconds
    b'type': b'can',        # Service type marker
    # ... service-specific fields
}
```

**Implementation**:
```python
# Write to stream
redis_client.xadd('sensor:raw', stream_data, maxlen=100000, approximate=True)
```

#### 2. TimescaleDB

**Purpose**: Full historical data storage for long-term analysis.

**Requirements**:
- Use hypertables for time-series data
- Include proper indexes on time columns
- Use batch inserts for performance
- Handle connection errors gracefully

**Example**:
```python
# Insert measurements
await conn.execute("""
    INSERT INTO measurement (time, sensor_id, value, status)
    VALUES ($1, $2, $3, $4)
    ON CONFLICT (time, sensor_id) DO UPDATE
    SET value = EXCLUDED.value, status = EXCLUDED.status
""", timestamp, sensor_id, value, status)
```

#### 3. Redis State Keys

**Purpose**: Live values for frontend real-time display.

**Format**:
- **Key Pattern**: `sensor:<sensor_name>` or `automation:<location>:<cluster>:<device_name>`
- **TTL**: 10 seconds (consistent across all services)
- **Value**: JSON string or simple value string

**Implementation**:
```python
# Write state with TTL
pipe = redis_client.pipeline()
pipe.setex(f"sensor:{sensor_name}", 10, str(value))
pipe.setex(f"sensor:{sensor_name}:ts", 10, str(timestamp_ms))
pipe.execute()
```

### Query Strategy (Backend Services)

When querying data, follow this priority:

1. **Live Data**: Read from Redis state keys (`sensor:*` or `automation:*`)
2. **Recent History** (within 6 hours): Query Redis Stream (`sensor:raw`) first, filter by `type`
3. **Older History**: Query TimescaleDB

**Implementation**:
```python
# 1. Check Redis Stream for recent data
if duration_hours <= 6:
    stream_entries = stream_reader.read_by_time_range(
        start_time, end_time, sensor_type="can", max_count=20000
    )
    if stream_entries:
        # Process stream entries
        return process_stream_entries(stream_entries)

# 2. Fall back to TimescaleDB
return await db.get_sensor_data(start_time, end_time)
```

### Service Implementation Checklist

When creating a new service, ensure:

- [ ] **Redis Stream writes**: Write all data to `sensor:raw` with appropriate `type` marker
- [ ] **TimescaleDB writes**: Store full history in hypertables
- [ ] **Redis state keys**: Write live values with 10-second TTL
- [ ] **Error handling**: Gracefully handle Redis/DB connection failures
- [ ] **Stream format**: Include `type`, `ts`, and service-specific fields
- [ ] **State keys**: Use consistent naming pattern (`sensor:*` or `automation:*`)

### Shared Utilities

#### Redis Stream Reader

Location: `Infrastructure/backend/app/redis_stream_reader.py`

Use this utility to read from `sensor:raw` stream:

```python
from app.redis_stream_reader import RedisStreamReader

stream_reader = RedisStreamReader(stream_name="sensor:raw")
stream_reader.connect()
entries = stream_reader.read_by_time_range(
    start_time, end_time, sensor_type="can", max_count=20000
)
```

#### Stream Processor

Location: `Infrastructure/backend/app/stream_processor.py`

Use this to process stream entries into sensor data points:

```python
from app.stream_processor import process_stream_entries_to_sensor_data

sensor_data = process_stream_entries_to_sensor_data(
    stream_entries, location, cluster
)
```

### Benefits

- ✅ **Reduced DB load**: Stream-first querying for recent data
- ✅ **Real-time updates**: Redis state keys for live values
- ✅ **Full history**: TimescaleDB for long-term storage
- ✅ **Unified pattern**: Consistent across all services
- ✅ **Scalable**: Stream trimming prevents unbounded growth

### Migration Notes

- Old services using `can:raw` stream should migrate to `sensor:raw`
- Old services using SQLite should migrate to TimescaleDB
- Old services using long TTLs should migrate to 10-second TTL
- All services should add `type` marker to stream entries

### Questions?

Refer to existing implementations:
- CAN Processor: `Infrastructure/can-processor-service/app/writer.py`
- Soil Sensor Service: `Infrastructure/soil-sensor-service/app/redis_client.py`
- Automation Service: `Infrastructure/automation-service/app/redis_client.py`
- Backend: `Infrastructure/backend/app/routes/sensors.py`

---

## Redis Setup and Troubleshooting

### Redis Automatic AOF Fix Setup

This setup automatically checks and fixes Redis AOF (Append-Only File) corruption on boot and when Redis fails to start.

#### Components

1. **AOF Fix Script**
   - **Location**: `/usr/local/bin/redis-aof-fix.sh`
   - **Purpose**: Checks and fixes corrupted AOF files
   - **Logs**: `/var/log/redis/aof-fix.log`

2. **Redis AOF Check Service**
   - **Service**: `redis-aof-check.service`
   - **Purpose**: Runs before Redis starts to check/fix AOF files
   - **Location**: `/etc/systemd/system/redis-aof-check.service`

3. **Redis Service Override**
   - **Location**: `/etc/systemd/system/redis-server.service.d/on-failure-fix.conf`
   - **Purpose**: Triggers AOF fix when Redis fails

#### How It Works

**On Boot:**
1. `redis-aof-check.service` runs **before** `redis-server.service`
2. It checks all AOF files for corruption
3. If corruption is found, it automatically fixes it
4. Then Redis starts normally

**On Failure:**
1. If Redis fails to start (e.g., due to AOF corruption)
2. `OnFailure` triggers `redis-aof-check.service`
3. The service fixes the AOF files
4. Then attempts to restart Redis automatically

#### Verification

Check that services are enabled and configured correctly:

```bash
# Check AOF check service is enabled
systemctl is-enabled redis-aof-check.service

# Check Redis service configuration
systemctl show redis-server.service | grep -E "(OnFailure|Restart)"

# Check service dependencies
systemctl list-dependencies redis-server.service --reverse | grep redis-aof

# Test the AOF fix script manually
sudo /usr/local/bin/redis-aof-fix.sh
cat /var/log/redis/aof-fix.log
```

#### Manual Operations

**Run AOF check manually:**
```bash
sudo systemctl start redis-aof-check.service
```

**Check AOF fix logs:**
```bash
cat /var/log/redis/aof-fix.log
```

**Test failure recovery:**
```bash
# Stop Redis
sudo systemctl stop redis-server.service

# Manually trigger the check service (simulates OnFailure)
sudo systemctl start redis-aof-check.service

# Redis should automatically restart
systemctl status redis-server.service
```

#### Troubleshooting

If Redis still fails to start after the fix:

1. **Check AOF fix log:**
   ```bash
   cat /var/log/redis/aof-fix.log
   ```

2. **Check Redis logs:**
   ```bash
   sudo journalctl -u redis-server.service -n 50
   sudo cat /var/log/redis/redis-server.log | tail -20
   ```

3. **Manually fix AOF:**
   ```bash
   cd /var/lib/redis/appendonlydir
   echo "y" | sudo redis-check-aof --fix appendonly.aof.8.incr.aof
   ```

4. **Restart Redis:**
   ```bash
   sudo systemctl start redis-server.service
   ```

### Redis Boot Failure Fix

#### Problem

On boot, Redis may fail to start if the AOF (Append-Only File) is corrupted. This prevents dependent services (can-processor, cea-backend, automation-service) from starting.

#### Symptoms

- Redis service fails to start on boot
- Error in logs: `Bad file format reading the append only file`
- Dependent services show "Dependency failed" errors

#### Solution

**Manual Fix (When Redis Fails to Start):**

1. **Check Redis status:**
   ```bash
   sudo systemctl status redis-server
   ```

2. **Check Redis logs:**
   ```bash
   sudo journalctl -u redis-server -n 50
   sudo cat /var/log/redis/redis-server.log | tail -20
   ```

3. **Fix corrupted AOF file:**
   ```bash
   # Navigate to AOF directory
   cd /var/lib/redis/appendonlydir
   
   # Check which file is corrupted (usually appendonly.aof.8.incr.aof)
   # Fix it automatically
   echo "y" | sudo redis-check-aof --fix appendonly.aof.8.incr.aof
   ```

4. **Start Redis:**
   ```bash
   sudo systemctl start redis-server
   sudo systemctl status redis-server
   ```

5. **Start dependent services:**
   ```bash
   sudo systemctl start can-processor
   sudo systemctl start cea-backend
   sudo systemctl start automation-service
   ```

**Automated Fix Script:**

A script is available at `/usr/local/bin/redis-aof-fix.sh` that can check and fix AOF corruption.

**Manual execution:**
```bash
sudo /usr/local/bin/redis-aof-fix.sh
```

**Check fix log:**
```bash
cat /var/log/redis/aof-fix.log
```

#### Prevention

The AOF file corruption typically occurs due to:
- Unexpected system shutdown/power loss
- Disk I/O errors
- Filesystem issues

To minimize corruption:
- Ensure proper system shutdown procedures
- Monitor disk health
- Consider using UPS for power protection

#### Verification

After fixing, verify all services are running:

```bash
systemctl status redis-server postgresql can-processor soil-sensor-service cea-backend automation-service
```

All services should show `active (running)`.

---

## TimescaleDB Installation

### Quick Start

Run the installation scripts:

```bash
# Install Redis
sudo /home/antoine/Project\ CEA/Infrastructure/setup_redis.sh

# Install TimescaleDB
sudo /home/antoine/Project\ CEA/Infrastructure/setup_timescaledb.sh
```

### Manual Installation

#### Redis Installation

```bash
sudo apt update
sudo apt install redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Verify
redis-cli ping
# Should return: PONG
```

#### Redis Configuration (AOF + RDB)

Edit `/etc/redis/redis.conf`:

```conf
# Enable AOF
appendonly yes
appendfsync everysec

# Enable RDB snapshots
save 900 1
save 300 10
save 60 10000

# Bind to localhost only
bind 127.0.0.1
```

Restart Redis:
```bash
sudo systemctl restart redis-server
```

#### TimescaleDB Installation

```bash
# Install PostgreSQL
sudo apt install postgresql postgresql-contrib

# Add TimescaleDB repository
sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/debian/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo apt-key add -

# Install TimescaleDB
sudo apt update
sudo apt install timescaledb-2-postgresql-$(psql --version | grep -oP '\d+' | head -1)

# Tune TimescaleDB
sudo timescaledb-tune --quiet --yes

# Restart PostgreSQL
sudo systemctl restart postgresql
```

#### Create Database and User

```bash
sudo -u postgres psql <<EOF
CREATE DATABASE cea_sensors;
CREATE USER cea_user WITH PASSWORD 'cea_password_change_me';
GRANT ALL PRIVILEGES ON DATABASE cea_sensors TO cea_user;
\c cea_sensors
CREATE EXTENSION IF NOT EXISTS timescaledb;
GRANT ALL ON SCHEMA public TO cea_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO cea_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO cea_user;
EOF
```

#### Create Schema and Hypertable

```bash
psql -h localhost -U cea_user -d cea_sensors -f /home/antoine/Project\ CEA/Database/timescaledb_setup.sql
```

#### Migrate Data from SQLite (if needed)

**Note:** Migration from SQLite to TimescaleDB has been completed. If you need to migrate additional data, you would need to create a custom migration script.

### Verify Installation

#### Redis

```bash
# Test connection
redis-cli ping

# Check stream
redis-cli XLEN sensor:raw

# Monitor commands
redis-cli MONITOR
```

#### TimescaleDB

```bash
# Test connection
psql -h localhost -U cea_user -d cea_sensors

# Check table exists
psql -h localhost -U cea_user -d cea_sensors -c "\d measurement"

# Check hypertable
psql -h localhost -U cea_user -d cea_sensors -c "SELECT * FROM timescaledb_information.hypertables;"

# Count rows
psql -h localhost -U cea_user -d cea_sensors -c "SELECT COUNT(*) FROM measurement;"
```

### Configuration

#### Environment Variables

Set these in systemd service files or `.env`:

```bash
export REDIS_URL="redis://localhost:6379"
export POSTGRES_HOST="localhost"
export POSTGRES_DB="cea_sensors"
export POSTGRES_USER="cea_user"
export POSTGRES_PASSWORD="your_password_here"
```

#### Change Database Password

```bash
sudo -u postgres psql -c "ALTER USER cea_user WITH PASSWORD 'your_new_password';"
```

Then update:
- Service files
- Migration script
- Grafana data source
- Any other configuration files

### Troubleshooting

#### Redis not starting

```bash
# Check logs
sudo journalctl -u redis-server -n 50

# Check config
sudo redis-cli CONFIG GET appendonly
```

#### TimescaleDB connection failed

```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Check connection
psql -h localhost -U cea_user -d cea_sensors

# Check pg_hba.conf if connection denied
sudo cat /etc/postgresql/*/main/pg_hba.conf
```

#### Migration fails

- Verify SQLite database exists and is readable
- Check TimescaleDB credentials are correct
- Ensure database and user exist
- Check disk space

---

## Deployment Checklist

This checklist guides you through deploying the complete Redis-based pipeline system.

### Prerequisites

- [ ] Raspberry Pi running Linux (tested on Raspberry Pi OS)
- [ ] Python 3.8+ installed
- [ ] CAN bus hardware configured and working
- [ ] Root/sudo access for system configuration
- [ ] Existing CAN scanner working (for migration)

### Phase 1: Infrastructure Setup

#### 1.1 Install Redis

- [ ] Run installation script:
  ```bash
  cd "/home/antoine/Project CEA/Infrastructure"
  sudo ./setup_redis.sh
  ```

- [ ] Verify Redis is running:
  ```bash
  sudo systemctl status redis-server
  redis-cli ping  # Should return "PONG"
  ```

#### 1.2 Install TimescaleDB

- [ ] Run installation script:
  ```bash
  cd "/home/antoine/Project CEA/Infrastructure"
  sudo ./setup_timescaledb.sh
  ```

- [ ] Verify PostgreSQL/TimescaleDB is running:
  ```bash
  sudo systemctl status postgresql
  psql -U cea_user -d cea_sensors -c "SELECT version();"
  ```

#### 1.3 Create TimescaleDB Schema

- [ ] Run schema setup:
  ```bash
  cd "/home/antoine/Project CEA/Infrastructure/database"
  psql -U cea_user -d cea_sensors -f cea_schema.sql
  ```

#### 1.4 Migrate Data from SQLite

- [ ] **BACKUP SQLite database first**
- [ ] Update migration script with correct password
- [ ] Run migration
- [ ] Verify data integrity

### Phase 2: Install Dependencies

- [ ] Install CAN processor dependencies
- [ ] Install backend dependencies
- [ ] Install soil sensor service dependencies
- [ ] Install automation service dependencies

### Phase 3: Systemd Services Setup

- [ ] Install service files
- [ ] Update existing services
- [ ] Configure service environment variables

### Phase 4: Start Services

- [ ] Start PostgreSQL
- [ ] Start Redis
- [ ] Start CAN setup
- [ ] Start CAN processor
- [ ] Start soil sensor service
- [ ] Start backend
- [ ] Start automation service
- [ ] Verify service status

### Phase 5: Verify Data Flow

- [ ] Check Redis Stream
- [ ] Check Redis State Keys
- [ ] Check TimescaleDB
- [ ] Check Backend API

### Phase 6: Final Verification

- [ ] Run comprehensive test suite
- [ ] Monitor Redis Stream length
- [ ] Check service resource usage
- [ ] Compare data between Redis and TimescaleDB

---

## Service Management

### Monitoring Scripts

#### CAN Processor Monitor
```bash
"/home/antoine/Project CEA/monitor_can_processor.sh"
```
Monitors CAN processor service, CAN bus interface, Redis stream, database writes, and recent CAN messages.

#### Redis Stream Monitor
```bash
"/home/antoine/Project CEA/monitor_redis_stream.sh"
```
Monitors Redis stream (`sensor:raw`) and displays live sensor values.

### Service Management Scripts

Located in root folder:

- `service_error_handler.sh` - Error handler script
- `start_all_services.sh` - Starts all services in correct dependency order
- `restart_all_services.sh` - Restarts all services
- `enable_autostart.sh` - Enables all services for boot autostart

### Grafana Setup (Optional)

See `Infrastructure/frontend/grafana/README.md` for detailed Grafana frontend setup instructions.

Quick setup:
```bash
# Install Grafana
sudo apt install grafana
sudo systemctl enable grafana-server
sudo systemctl start grafana-server

# Access at http://localhost:3000
# Default login: admin / admin
```

---

## Next Steps

- Configure Grafana dashboards
- Set up alerts (optional)
- Customize monitoring scripts
- Review and adjust Redis TTL values
- Optimize TimescaleDB queries if needed

---

## Secrets & Key Management (IMPORTANT)

### Never commit secrets

This repository must **never** contain:

- SSH private keys (e.g., `id_ed25519`, `id_rsa`, `-----BEGIN OPENSSH PRIVATE KEY-----`)
- `.env` files containing credentials
- API tokens / passwords / private certificates

If any secret is committed (even in a private repo), treat it as **compromised**.

### If a secret was committed (incident response)

1. **Rotate / revoke** the secret immediately:
   - SSH keys: remove the public key from any `~/.ssh/authorized_keys` and regenerate a new keypair.
   - Tokens/passwords: revoke and re-issue.
2. **Remove the secret file from the repo** and add ignore rules so it cannot be re-added.
3. **Purge it from git history** (recommended):
   - Use `git filter-repo` (or BFG) to remove the file from all commits.
   - Force-push rewritten history.
4. **Fix clones**:
   - Any other clone must re-clone, or run a hard reset to the new history.

### Operational rule

- Store secrets only in:
  - systemd service environment overrides (`Environment=...` in `/etc/systemd/system/*.service`), or
  - a local `.env` file that is **gitignored** and never shared.
