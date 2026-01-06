# PROJECT CEA - Greenhouse Automation System

**Generated:** 2025-01-05

## OVERVIEW
Controlled Environment Agriculture system for greenhouse automation with multi-service microservices architecture, ESP32 sensor nodes, and React frontend.

## STRUCTURE

```
Project CEA/
├── Infrastructure/          # 6 Python microservices + React frontend
│   ├── backend/           # Sensor data API (port 8000)
│   ├── automation-service/ # Control & configuration API (port 8001)
│   ├── frontend/          # React dashboard
│   ├── can-processor-service/     # CAN bus data processing
│   ├── soil-sensor-service/      # RS485 soil sensors
│   ├── weather-service/          # Weather API integration
│   └── database/         # Schema & documentation
├── Sensor Nodes/          # ESP32 Arduino firmware
├── Boot Initialisation Services/  # System boot scripts
└── Test Scripts/         # Testing & monitoring utilities
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Service development | `Infrastructure/*/` | Each service in own dir |
| Frontend UI | `Infrastructure/frontend/` | React + TypeScript |
| Database schema | `Infrastructure/database/` | TimescaleDB setup |
| Sensor firmware | `Sensor Nodes/ESP32/` | Arduino IDE projects |
| Service logs | `journalctl -u <service>` | systemd journal |
| Monitoring | Root scripts | `monitor_can_processor.py`, `monitor_redis_stream.py` |

## CONVENTIONS

### Python Services
- **Framework**: FastAPI
- **Structure**: `app/` directory with `main.py` entry point
- **Dependencies**: `requirements.txt` per service
- **Ports**: backend=8000, automation=8001
- **State management**: Redis keys with 10-second TTL (`sensor:*`, `automation:*`)

### Frontend
- **Stack**: React 18 + TypeScript + Vite + Tailwind CSS
- **Build**: `npm run build` → `dist/` served by automation-service
- **APIs**: backend:8000 (sensors), automation:8001 (control)

### Data Flow
- **Services write to**: Redis Stream `sensor:raw` (MAXLEN 100K) + TimescaleDB
- **Services read from**: Redis Stream (recent <6h) → TimescaleDB (older)
- **Real-time**: Redis state keys for live display

### Systemd
- Service files in `Infrastructure/*.service`
- Copy to `/etc/systemd/system/` then `daemon-reload`
- Startup order: postgresql → redis → can-setup → can-processor → soil-sensor → backend → automation

## COMMANDS

```bash
# Start all services
./restart_all_services.sh

# Enable autostart
./enable_autostart.sh

# Service management
systemctl start can-processor
systemctl start soil-sensor-service
systemctl start cea-backend
systemctl start automation-service

# Development - backend
cd Infrastructure/backend && uvicorn app.main:app --reload

# Development - frontend
cd Infrastructure/frontend && npm run dev

# Development - automation
cd Infrastructure/automation-service && uvicorn app.main:app --reload
```

## ANTI-PATTERNS (THIS PROJECT)

- **Never**: Commit secrets to repo (`.env`, passwords, API tokens)
- **Never**: Use long TTL for Redis state keys (must be 10s)
- **Never**: Skip TimescaleDB writes (all data must persist to DB)
- **Never**: Direct DB access from frontend (always go through backend APIs)
- **Never**: Modify service files without testing startup order

## NOTES

- **Hardware**: CAN bus for sensors, RS485 for soil sensors
- **Monitoring**: Grafana dashboard (optional) at port 3000
- **Redis AOF corruption**: Auto-fix via `redis-aof-check.service`
- **Boot dependencies**: Service startup order is critical
- **Zones**: Flower Room (front/back), Veg Room (main), Lab (main)
