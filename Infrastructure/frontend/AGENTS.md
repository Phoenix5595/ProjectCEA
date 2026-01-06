# CEA FRONTEND

**Generated:** 2025-01-05

## OVERVIEW
React + TypeScript dashboard for real-time monitoring and control of greenhouse system. WebSocket for live updates, Recharts for visualization.

## STRUCTURE

```
frontend/
├── src/
│   ├── pages/          # Main page components (ZoneDashboards, Schedules)
│   ├── components/     # Reusable UI components
│   ├── services/       # API client, WebSocket client
│   ├── config/         # Zone definitions
│   ├── types/          # TypeScript interfaces
│   ├── utils/          # Validation, formatting
│   └── styles/        # CSS/Tailwind
├── public/            # Static assets
├── grafana/           # Grafana dashboards (optional)
└── dist/              # Build output (served by automation-service)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Dashboard UI | `src/pages/` | Zone-specific dashboards |
| Reusable components | `src/components/` | Common UI elements |
| API calls | `src/services/api.ts` | Axios to backend/automation |
| WebSocket | `src/services/websocket.ts` | Real-time updates |
| Zone config | `src/config/zones.ts` | Hardcoded zone list |
| Type definitions | `src/types/` | Interfaces for data structures |
| Validation | `src/utils/validation.ts` | Input validation |
| Styling | `src/styles/` | CSS, Tailwind config |

## CONVENTIONS

### Architecture
- **State**: React hooks (useState, useEffect)
- **Data fetching**: Axios to backend:8000 (sensors) + automation:8001 (control)
- **Real-time**: WebSocket to automation-service:8001/ws
- **Routing**: React Router DOM for page navigation
- **Charts**: Recharts for time-series visualization

### Environment Variables (`.env`)
- `VITE_BACKEND_API_URL` - Sensor data API (default: http://localhost:8000)
- `VITE_AUTOMATION_API_URL` - Control API (default: http://localhost:8001)
- `VITE_WEBSOCKET_URL` - WebSocket (default: ws://localhost:8001/ws)

### Build & Serve
- **Dev**: `npm run dev` → Vite dev server (port 3001)
- **Build**: `npm run build` → `dist/` directory
- **Production**: `dist/` served by automation-service at root path

### Validation
- Temperature: 10.0 - 35.0 °C
- Humidity: 30.0 - 90.0 %
- CO₂: 400.0 - 2000.0 ppm
- VPD: 0.0 - 5.0 kPa

## COMMANDS

```bash
# Development
npm run dev        # Port 3001

# Build
npm run build      # Creates dist/

# Preview build
npm run preview    # Preview dist/

# Install dependencies
npm install
```

## ANTI-PATTERNS (THIS PROJECT)

- **Never**: Direct database access (always use APIs)
- **Never**: Hardcode URLs (use environment variables)
- **Never**: Skip input validation (ranges enforced)
- **Never**: Commit `.env` files (gitignored)
- **Never**: Modify `src/config/zones.ts` at runtime (requires rebuild)
- **Never**: Use different port than 3001 in dev (causes conflicts)

## NOTES

- **Tailscale access**: Set `VITE_*` vars to Tailscale IP for remote access
- **Schedule conflicts**: Detected client-side before submission
- **Setpoint timeline**: Fixed 00:00-24:00, PRE_DAY/PRE_NIGHT override DAY periods
- **Mode-aware**: Setpoints change based on current mode (DAY/NIGHT/TRANSITION)
- **Build output**: Served by automation-service, not Vite in production
