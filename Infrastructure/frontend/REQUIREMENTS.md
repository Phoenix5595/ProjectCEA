# Frontend Requirements (CEA)

- Use Node.js 18+ and npm; run `npm run build` for production.
- After every production build, restart `automation-service.service` to serve updated `dist/`.
- Timeline rendering:
  - 00:00 → 24:00 fixed axis.
  - PRE_DAY and PRE_NIGHT setpoints/ramp-in override DAY during their periods; DAY lines must not render inside those ranges.
  - Ramp-in is shown as a diagonal from the previous period value to the new value across the configured minutes.
- Environment variables (`.env`): `VITE_BACKEND_API_URL`, `VITE_AUTOMATION_API_URL`, `VITE_WEBSOCKET_URL`.
- Keep `setpoints` and schedules in sync with backend schema (modes: DAY, NIGHT, PRE_DAY, PRE_NIGHT; includes `ramp_in_duration`).

