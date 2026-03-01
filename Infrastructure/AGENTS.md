# CEA INFRASTRUCTURE

## OVERVIEW

6 Python FastAPI microservices + React frontend. Unified data pattern: CAN/Modbus → Redis Stream + State → TimescaleDB.

## STRUCTURE

```
Infrastructure/
├── automation-service/    # Control logic, PID, schedules (8001)
├── backend/               # Sensor data API (8000)
├── frontend/              # React + Vite + Tailwind
├── can-processor-service/ # CAN bus → Redis/DB
├── soil-sensor-service/   # RS485 Modbus (8002)
├── weather-service/       # YUL weather data (8003)
├── database/              # TimescaleDB schema + docs
├── docs/                  # Architecture documentation
└── *.service              # systemd unit files
```

## DEEP DIVE DOCS

| Topic | Document |
|-------|----------|
| Full setup guide | `README.md` |
| TODO tracking | `TODO_TRACKING.md` |
| Control refactor | `docs/control_engine_refactoring.md` |

## DATA FLOW

```
Sensors → CAN/Modbus → [can-processor/soil-sensor] 
    → Redis Stream (sensor:raw, MAXLEN 100K)
    → Redis State Keys (sensor:*, TTL 10s)
    → TimescaleDB (measurement hypertable)
    → Backend API → WebSocket → Frontend
```

## SHARED CODE

| Component | Location | Used By |
|-----------|----------|---------|
| `shared/infra_logging.py` | `automation-service/shared/` | All Python services |
| Pydantic models | Each `app/models.py` | Routes, database |
| Config loader | Each `app/config.py` | Service startup |

## EVENT BUS (Cross-Service)

Config changes propagate across services via Redis Streams:

```
Automation Service → Redis Stream (cea:events:config) → Backend Service → WebSocket → Frontend
```

| Component | Location | Purpose |
|-----------|----------|---------|
| ConfigEventBus | `automation-service/app/events/__init__.py` | Dual-publish (memory + Redis) |
| RedisStreamPublisher | `app/events/redis_streams.py` | Publish to Redis Streams |
| RedisEventConsumer | `app/events/consumer.py` | Read from stream |

## SERVICE DEPENDENCIES

```
postgresql.service
redis-server.service
    ↓
can-setup.service (oneshot)
    ↓
can-processor.service
soil-sensor-service.service
weather-service.service
    ↓
cea-backend.service
automation-service.service
```

## ANTI-PATTERNS (Infrastructure-specific)

| Never | Reason |
|-------|--------|
| Skip Redis Stream writes | Recent data queries fail |
| Use different TTL per service | 10s standard, except schedule state (no TTL) |
| Start services out of order | Dependencies will fail |
| Edit `.service` without daemon-reload | Config won't apply |

## COMMANDS

```bash
# Install service files
sudo cp *.service /etc/systemd/system/ && sudo systemctl daemon-reload

# Start all in order
sudo systemctl start postgresql redis-server can-setup can-processor soil-sensor-service cea-backend automation-service

# View all logs
journalctl -u can-processor -u cea-backend -u automation-service -f
```
