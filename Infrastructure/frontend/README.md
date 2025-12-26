# CEA Automation Frontend

React + TypeScript frontend for the CEA (Controlled Environment Agriculture) greenhouse automation system.

## Features

- **Real-time Dashboard**: View sensor data (temperature, humidity, CO₂, VPD) and device states via WebSocket
- **Zone-based Configuration**: Edit setpoints, PID parameters, and schedules per zone
- **Mode-aware Setpoints**: Configure different setpoints for DAY/NIGHT/TRANSITION modes
- **VPD Control**: Set VPD setpoints that control dehumidifying devices (fans, dehumidifiers)
- **Schedule Management**: Create, edit, and delete schedules with conflict detection
- **PID Parameter Editing**: Configure PID parameters for heaters and CO₂ systems

## Setup

### Prerequisites

- Node.js 18+ and npm
- Backend automation service running on port 8001

### Installation

```bash
cd Infrastructure/frontend
npm install
```

### Development

```bash
npm run dev
```

Frontend will be available at http://localhost:3001

### Production Build

```bash
npm run build
```

Build output will be in `dist/` directory. The backend serves these files automatically.

## Configuration

### Zones

Zones are hardcoded in `src/config/zones.ts`:
- Flower Room (front, back)
- Veg Room (main)
- Lab (main)

### API URLs (Environment Variables)

The frontend uses environment variables to configure backend URLs. Create a `.env` file in the frontend directory:

```bash
# Backend API URL (sensor data service - port 8000)
VITE_BACKEND_API_URL=http://localhost:8000

# Automation API URL (configuration service - port 8001)
VITE_AUTOMATION_API_URL=http://localhost:8001

# WebSocket URL (real-time updates - port 8001)
VITE_WEBSOCKET_URL=ws://localhost:8001/ws
```

### Tailscale Configuration

To access the frontend via Tailscale, set the environment variables to your Tailscale IP:

```bash
# Replace 100.x.x.x with your Raspberry Pi's Tailscale IP
VITE_BACKEND_API_URL=http://100.x.x.x:8000
VITE_AUTOMATION_API_URL=http://100.x.x.x:8001
VITE_WEBSOCKET_URL=ws://100.x.x.x:8001/ws
```

**Note**: The backend services bind to `0.0.0.0`, so they're accessible on all network interfaces including Tailscale. Make sure:
1. Your Tailscale IP is configured correctly
2. Ports 8000, 8001 are not blocked by firewall
3. You rebuild the frontend after changing `.env` variables (run `npm run build`)

## Project Structure

```
src/
├── pages/          # Main page components
├── components/     # Reusable components
├── services/       # API and WebSocket clients
├── config/         # Configuration (zones)
├── types/          # TypeScript type definitions
├── utils/          # Utility functions (validation, formatting)
└── styles/         # CSS styles
```

## API Endpoints Used

- `GET /api/setpoints/{location}/{cluster}?mode={mode}` - Get setpoints
- `POST /api/setpoints/{location}/{cluster}` - Update setpoints
- `GET /api/setpoints/{location}/{cluster}/all-modes` - Get all mode setpoints
- `GET /api/sensors/{location}/{cluster}/live` - Get live sensor data
- `GET /api/devices` - Get all devices
- `GET /api/pid/parameters/{device_type}` - Get PID parameters
- `POST /api/pid/parameters/{device_type}` - Update PID parameters
- `GET /api/schedules` - List schedules
- `POST /api/schedules` - Create schedule
- `PUT /api/schedules/{id}` - Update schedule
- `DELETE /api/schedules/{id}` - Delete schedule
- `GET /api/mode/{location}/{cluster}` - Get mode
- `WebSocket /ws` - Real-time updates

## Validation

All inputs are validated client-side:
- Temperature: 10.0 - 35.0 °C
- Humidity: 30.0 - 90.0 %
- CO₂: 400.0 - 2000.0 ppm
- VPD: 0.0 - 5.0 kPa
- PID parameters: Device-specific ranges

## Schedule Conflict Detection

The frontend detects schedule conflicts before submission:
- Same location/cluster
- Overlapping time ranges
- Same day of week (or one/both daily)
- Same mode (if mode-based)

