# CAN Processor Service

Unified service that reads CAN frames from the `can0` socketcan interface, decodes them once, and writes sensor data to Redis, TimescaleDB, and Redis Streams.

## What it does

- Reads directly from the CAN bus via `python-can`.
- Decodes frames using `app/decoder.py`.
- Extracts and calculates sensor values in `app/processor.py` (RH/VPD from dry/wet bulb temperatures).
- Writes to:
  - Redis state keys (`sensor:*`) for live values
  - Redis Stream `sensor:raw` for recent history
  - TimescaleDB `measurement` table for long-term history

## Setup

```bash
cd Infrastructure/can-processor-service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Environment variables:

- `REDIS_URL` (default `redis://localhost:6379`)
- `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`
- Postgres password is read from a systemd credential file or `POSTGRES_PASSWORD` (`shared/db_credentials.py`)

The CAN interface is expected to be `can0` at 250 kbps. Bring it up with:

```bash
sudo ip link set can0 up type can bitrate 250000
```

## Running

```bash
python3 -m app.main
```

Production runs under `can-processor.service`.

## Data flow

```
ESP32 fullV6 nodes (CAN 250 kbps)
    -> can0 -> CAN Processor
        -> Redis state keys
        -> Redis Stream (sensor:raw, maxlen 100,000)
        -> TimescaleDB (batched every 50 messages or 100 ms)
```

## Node mapping

| Node ID | Location | Cluster |
|---------|----------|---------|
| 1 | Flower Room | back |
| 2 | Flower Room | front |
| 3 | Veg Room | main |

## Monitoring

```bash
journalctl -u can-processor.service -f
```

The service logs statistics periodically and suppressed repeated Redis stream errors after the first few.
