# Separate Microservice Architecture (Integrated Backend)

## Architecture Overview

You want a **separate microservice** for flexibility (different languages) but still **integrated into the backend**. Here's how we can achieve both:

## Option A: Separate Service with API Gateway Pattern (Recommended)

### Structure
```
Infrastructure/
├── backend/                    # Main FastAPI service (sensor data, alarms)
│   └── app/
│       ├── main.py
│       └── routes/
│           ├── sensors.py
│           ├── statistics.py
│           └── automation_proxy.py  # Proxy to automation service
│
└── automation-service/         # Separate automation microservice
    ├── app/
    │   ├── main.py            # Can be FastAPI, Flask, or other language
    │   ├── hardware/
    │   ├── control/
    │   └── routes/
    │       └── devices.py
    ├── requirements.txt       # Or package.json, Cargo.toml, etc.
    └── automation_config.yaml
```

### How It Works

1. **Automation Service** runs on port 8001 (or configurable)
2. **Main Backend** runs on port 8000
3. **Main Backend** can proxy automation requests OR frontend calls both directly

### Communication Options

#### Option A1: Frontend Calls Both Services Directly
```
Frontend (port 3000)
├── http://localhost:8000/api/sensors/*     → Main backend
├── http://localhost:8000/api/statistics/*  → Main backend
└── http://localhost:8001/api/devices/*    → Automation service
```

**Pros:**
- Complete separation
- Frontend has direct access to both
- Easy to scale independently

**Cons:**
- Frontend needs to know two URLs
- CORS configuration for both services

#### Option A2: Main Backend Proxies Automation Requests
```
Frontend (port 3000)
└── http://localhost:8000/api/*
    ├── /api/sensors/*        → Handled by main backend
    ├── /api/statistics/*     → Handled by main backend
    └── /api/automation/*     → Proxied to automation service (port 8001)
```

**Pros:**
- Frontend sees single API endpoint
- Unified CORS configuration
- Can add authentication/rate limiting in one place

**Cons:**
- Slight latency overhead (extra hop)
- Main backend needs proxy code

### Shared Resources

Both services share:
- **Database**: `can_messages.db` (SQLite allows multiple readers)
- **Config**: Can share YAML configs or use separate ones
- **WebSocket**: Can use separate WebSocket endpoints OR main backend broadcasts both sensor + device updates

### Implementation

**Main Backend** (`Infrastructure/backend/app/routes/automation_proxy.py`):
```python
from fastapi import APIRouter
import httpx

router = APIRouter(prefix="/api/automation", tags=["automation"])

AUTOMATION_SERVICE_URL = "http://localhost:8001"

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_to_automation(request: Request, path: str):
    """Proxy requests to automation service"""
    async with httpx.AsyncClient() as client:
        url = f"{AUTOMATION_SERVICE_URL}/api/{path}"
        response = await client.request(
            method=request.method,
            url=url,
            params=request.query_params,
            content=await request.body(),
            headers={k: v for k, v in request.headers.items() 
                    if k.lower() not in ["host", "content-length"]}
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )
```

**Automation Service** (can be Python, Node.js, Rust, Go, etc.):
- Implements all device control, scheduling, rules
- Reads sensor data from shared database
- Writes device state to shared database
- Exposes REST API on port 8001

---

## Option B: Separate Service with Message Queue

For even more decoupling, use a message queue (Redis, RabbitMQ, or simple file-based):

```
Main Backend          Automation Service
     |                      |
     |-- sensor data -->    |
     |                      |
     |<-- device state --   |
     |                      |
     |-- control cmd -->    |
```

**Pros:**
- Complete decoupling
- Can handle service restarts gracefully
- Can scale automation service horizontally

**Cons:**
- More complex
- Additional infrastructure (Redis/RabbitMQ)
- Overkill for single-device setup

---

## Option C: Plugin Architecture (Python Only)

If you want separation but stay in Python, use a plugin/module approach:

```
Infrastructure/backend/
├── app/
│   ├── main.py
│   └── plugins/
│       └── automation/        # Separate module, can be swapped
│           ├── __init__.py
│           ├── hardware/
│           └── control/
```

**Pros:**
- Separate codebase
- Easy to swap implementations
- Shared Python ecosystem

**Cons:**
- Still Python only
- Runs in same process (less isolation)

---

## My Recommendation: Option A1 (Direct Frontend Access)

**Why:**
1. **True separation**: Automation service can be any language
2. **Simple**: No proxy complexity
3. **Flexible**: Easy to move automation service to different machine later
4. **Shared database**: Both services read/write to `can_messages.db`

### Implementation Steps

1. **Create automation service** in `Infrastructure/automation-service/`
   - Can start with Python/FastAPI (easy migration from Test Scripts)
   - Or start with your preferred language
   - Exposes REST API on port 8001

2. **Shared database access**:
   - Both services connect to `can_messages.db`
   - SQLite supports multiple readers (one writer)
   - Use file locking or separate tables for writes

3. **Frontend configuration**:
   - Add `VITE_AUTOMATION_API_URL=http://localhost:8001` to frontend
   - Frontend calls both APIs

4. **Deployment**:
   - Both services in same `start_infrastructure.sh` script
   - Or separate systemd services
   - Can deploy to different machines later if needed

### Database Sharing Strategy

**Option 1: Separate Tables**
- Main backend: `can_messages` table
- Automation service: `device_states`, `control_history`, etc.
- Both in same database file

**Option 2: Separate Databases**
- Main backend: `can_messages.db`
- Automation service: `automation.db`
- Automation service reads from `can_messages.db` (sensor data)
- Automation service writes to `automation.db` (device state)

I recommend **Option 1** (same database, different tables) for simplicity.

---

## Language Options for Automation Service

### Python (FastAPI)
- **Pros**: Easy migration from Test Scripts, same ecosystem, async support
- **Cons**: Still Python (but separate service)

### Node.js (Express/Fastify)
- **Pros**: Great for real-time, easy WebSocket, large ecosystem
- **Cons**: Different language, need to rewrite hardware drivers

### Rust
- **Pros**: Fast, safe, great for hardware control
- **Cons**: Steeper learning curve, smaller ecosystem

### Go
- **Pros**: Simple, fast, good concurrency
- **Cons**: Need to rewrite hardware drivers

---

## Questions to Finalize

1. **Which language** do you want for the automation service?
   - Python (easiest, can reuse Test Scripts code)
   - Other (specify)

2. **How should frontend access it?**
   - Direct calls to both services (A1)
   - Main backend proxies (A2)
   - Your preference

3. **Database sharing preference?**
   - Same database, different tables (simpler)
   - Separate databases (more isolation)

4. **WebSocket strategy?**
   - Separate WebSocket endpoints (main: port 8000, automation: port 8001)
   - Main backend broadcasts both sensor + device updates
   - Automation service broadcasts device updates only

Once you decide, I'll create the detailed implementation plan!


