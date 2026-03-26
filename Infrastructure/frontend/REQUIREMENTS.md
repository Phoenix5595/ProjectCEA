# Frontend Requirements (CEA)

- Use Node.js 18+ and npm; run `npm run build` for production.
- After every production build, restart `automation-service.service` to serve updated `dist/`.
- Timeline rendering (`ClimatePeriodTimeline`):
  - 00:00 → 24:00 fixed axis; hour grid; **now** marker.
  - **Climate setpoints** come from **`climate_periods`** (named periods, `ramp_minutes` per period). No fixed PRE_DAY / PRE_NIGHT / DAY / NIGHT ladder in the UI.
  - **Sun/moon** overlays from photoperiod (`lightDayStart` / `lightDayEnd`): sun yellow, moon purple; split segments if the window crosses midnight; edge case same start/end = 24h moon.
  - **Climate curves** (heating / cooling / VPD): sample per minute via `sampleMetricSeries` in `src/utils/climatePeriodTimeline.ts` — ramp from **previous** period’s values over each period’s `ramp_minutes`, then hold; polylines for heat/cool/VPD. Overlay uses live picker values while editing, persisted values after save.
  - **Fixed Y-axes**: temperature **15 / 20 / 25 / 30 °C**; VPD **0.5 / 1.0 / 1.5 / 2.0 kPa**; full 24h horizontal extent including 24:00 edge.
- Environment variables (`.env`): `VITE_BACKEND_API_URL`, `VITE_AUTOMATION_API_URL`, `VITE_WEBSOCKET_URL`.
- Keep ZoneConfig **climate periods** (`ClimatePeriodsTable` + `saveClimatePeriods`) in sync with `climate_periods`; automation resolves effective setpoints via `ClimatePeriodResolver` (`ramp_in_duration` bridge).
- ZoneConfig SAVE: (1) `PUT` mode parameters, (2) `POST` room-schedule for photoperiod + **light** ramps, (3) `POST` `/api/climate-periods/{location}/{cluster}` for period rows.
- **Ramp field split (critical)**: Only **`light_ramp_up_minutes` / `light_ramp_down_minutes`** feed **`POST /api/room-schedule`** as `ramp_up_duration` / `ramp_down_duration`. Do **not** send `ramp_up_minutes` / `ramp_down_minutes` for that POST. Climate ramps are **`climate_periods.ramp_minutes`** per period.
- **Automation dashboard**: Shows weather data from the weather service (Quebec City, CYQB) in the **top-right** of the sticky header; label "Quebec City". System stats (CPU, memory, disk, uptime, load avg, process count, service health, Pi temp/throttle) use real data from automation-service `/api/status` when available; show "—" or "Unavailable" when data is missing or API fails (no mock/placeholder numbers).
- ZoneConfig **light intensity** UI is the `LightIntensity` component (`src/components/LightIntensity.tsx`); section label **Light intensity** (not the old generic "Lights" only).

