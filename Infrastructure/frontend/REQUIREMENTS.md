# Frontend Requirements (CEA)

- Use Node.js 18+ and npm; run `npm run build` for production.
- After every production build, restart `automation-service.service` to serve updated `dist/`.
- Timeline rendering:
  - 00:00 → 24:00 fixed axis.
  - **Climate setpoints** come from the **`climate_periods`** API/DB (named periods with `start_time`, `end_time`, `ramp_minutes`, and setpoints). There is **no** fixed PRE_DAY / PRE_NIGHT / DAY / NIGHT mode ladder in the UI; periods are user-defined per mode/submode.
  - When drawing period transitions, ramp-in is a diagonal from the **previous period’s** values to the **current period’s** values across that period’s `ramp_minutes` (not legacy `ramp_in_duration` per fixed mode).
  - In ZoneConfig climate timeline, overlay photoperiod blocks as:
    - sun period: yellow
    - moon period: purple
  - If a sun or moon period crosses midnight, split overlay rendering into two segments (`start→24:00` and `00:00→end`).
  - Timeline overlay uses live CircularTimePicker values while editing, and shows persisted DB-backed values after save/reload.
  - Edge case: identical day/night boundary times render as 24h moon (purple).
  - Sun/moon overlays must align exactly with the timeline axis (00:00 and 24:00 edges) with no horizontal offset and span the full plotting height.
  - **Climate setpoint curves** on the same 24h axis: plot **heating**, **cooling**, and **VPD** as line series derived from the current `climatePeriods` list (any count up to backend max). For each minute, resolve the active period (no overlaps); at the start of each period, ramp for `ramp_minutes` from the **previous** period’s setpoints (previous = last period before wrap when crossing midnight) to the current period’s setpoints; after the ramp, hold stable until the period end.
  - **Fixed Y-axes (non-dynamic)**: temperature axis must always be **15 / 20 / 25 / 30 °C** and VPD axis must always be **0.5 / 1.0 / 1.5 / 2.0 kPa**.
  - Timeline setpoint lines must render deterministically from current periods and reach full 24h horizontal extent (including the 24:00 edge).
- Environment variables (`.env`): `VITE_BACKEND_API_URL`, `VITE_AUTOMATION_API_URL`, `VITE_WEBSOCKET_URL`.
- Keep ZoneConfig **climate periods** (`ClimatePeriodsTable` + `saveClimatePeriods`) in sync with the `climate_periods` table; legacy mode-row setpoints (`setpoints` table by DAY/NIGHT/PRE_*) are not the primary climate path.
- ZoneConfig SAVE must update both mode_parameters and room schedule (POST room-schedule) so the control loop has correct per-device light schedules for the photoperiod.
- **Ramp field split (critical)**: **`light_ramp_up_minutes` / `light_ramp_down_minutes`** in `mode_parameters` are the only fields that must feed **`POST /api/room-schedule/...`** as `ramp_up_duration` / `ramp_down_duration` (light sun/moon intensity ramps). Do **not** send `ramp_up_minutes` / `ramp_down_minutes` for that POST — those are legacy mode_parameters fields and are **not** the climate-period ramp model (climate ramps are **`climate_periods.ramp_minutes`** per period).
- **Automation dashboard**: Shows weather data from the weather service (Quebec City, CYQB) in the **top-right** of the sticky header; label "Quebec City". System stats (CPU, memory, disk, uptime, load avg, process count, service health, Pi temp/throttle) use real data from automation-service `/api/status` when available; show "—" or "Unavailable" when data is missing or API fails (no mock/placeholder numbers).

