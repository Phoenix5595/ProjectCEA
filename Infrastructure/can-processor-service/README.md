# CAN Processor - Unified CAN Bus Service

Unified service that reads CAN bus messages, decodes them, processes sensor data, and writes to:
- Redis Stream (`sensor:raw`) - recent history buffer (100,000 messages)
- TimescaleDB (`measurement` table) - full historical data
- Redis state keys (`sensor:*`) - live values for frontend (TTL: 10 seconds)

## Architecture

This service replaces the previous two-service architecture (can-scanner + can-worker) with a single unified service that:
- Reads directly from CAN bus (no intermediate Redis Stream reading)
- Decodes messages once (no duplicate decoding)
- Processes and validates data
- Writes to all three destinations simultaneously

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables (optional):
```bash
export REDIS_URL="redis://localhost:6379"
export POSTGRES_HOST="localhost"
export POSTGRES_DB="cea_sensors"
export POSTGRES_USER="cea_user"
export POSTGRES_PASSWORD="your_password"
```

## Usage

Run the processor:
```bash
python3 -m app.main
```

Or run directly:
```bash
python3 app/main.py
```

## Configuration

The processor reads configuration from environment variables:

- `REDIS_URL`: Redis connection URL (default: `redis://localhost:6379`)
- `POSTGRES_HOST`: TimescaleDB host (default: `localhost`)
- `POSTGRES_DB`: Database name (default: `cea_sensors`)
- `POSTGRES_USER`: Database user (default: `cea_user`)
- `POSTGRES_PASSWORD`: Database password

## Service Setup

See `can-processor.service` for systemd service configuration.

## Data Flow

```
ESP32 Nodes (CAN Sensor Apparatus)
    ↓ (CAN Bus messages)
CAN Processor
    ├─→ Redis Stream (sensor:raw) - recent history
    ├─→ TimescaleDB (measurement) - full history
    └─→ Redis State Keys (sensor:*) - live values
            ↓
        Backend Service (reads from Redis state + Stream/DB)
            ↓
        Frontend (Grafana)
```

## Monitoring

The processor logs:
- Connection status
- Processing statistics (every 100 messages)
- Errors and warnings

Check logs with:
```bash
journalctl -u can-processor.service -f
```

## Differences from Previous Architecture

- **Single service** instead of two (scanner + worker)
- **No duplicate decoding** (decodes once, not twice)
- **Direct CAN bus reading** (no Redis Stream round-trip for processing)
- **Unified stream** (`sensor:raw` instead of `can:raw`)
- **No SQLite** (removed completely)

