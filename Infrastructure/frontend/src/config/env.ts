/**
 * Central environment / endpoint configuration (Phase 3.4b).
 *
 * Goal: a single place that decides how the SPA reaches the backend.
 *
 * After Phase 3.4a, every service is reachable through Caddy on :8080:
 *   GET  /api/sensors*        -> cea-backend (8000)
 *   GET  /api/sensor-data     -> cea-backend (8000)
 *   *    /weather/*           -> weather-service (8003)
 *   *    /ws/<location>       -> cea-backend (8000)
 *   *    /ws                  -> automation-service (8001)
 *   *    <everything else>    -> automation-service (8001) (SPA static + API)
 *
 * So the SPA only needs ONE base URL now. We still honor the legacy
 * per-service env overrides so an operator can peel one client back to a
 * direct port in an emergency without a full rebuild.
 *
 * X-API-Key wiring:
 *   If `VITE_CEA_API_KEY` is defined at build time, every REST client sends it
 *   as `X-API-Key`, and the WebSocket URL gets `?token=<key>` appended.
 *   Enforcement is a server-side flag (`CEA_API_KEY_REQUIRE=true`, Phase 3.4c),
 *   so shipping the key early is harmless until the gate flips.
 */

function currentOrigin(port: number): string {
  if (typeof window === 'undefined') return `http://localhost:${port}`;
  const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
  return `${protocol}//${window.location.hostname}:${port}`;
}

/**
 * Single entrypoint base URL (Caddy).
 * If the SPA is served from :8080 itself, `window.location.origin` is already
 * the Caddy URL, which is the cleanest option (same-origin, no CORS).
 */
function caddyBase(): string {
  const explicit = import.meta.env.VITE_API_BASE_URL;
  if (explicit) return explicit.replace(/\/$/, '');
  if (typeof window !== 'undefined' && window.location.port === '8080') {
    return window.location.origin; // same-origin: no CORS, no mixed-content.
  }
  return currentOrigin(8080);
}

const CADDY_BASE = caddyBase();

// Per-service overrides let an operator point one client at a direct port
// (e.g. 8000) without a rebuild. Unset by default.
export const BACKEND_API_URL =
  import.meta.env.VITE_BACKEND_API_URL?.replace(/\/$/, '') ?? CADDY_BASE;
export const AUTOMATION_API_URL =
  import.meta.env.VITE_AUTOMATION_API_URL?.replace(/\/$/, '') ?? CADDY_BASE;
export const WEATHER_API_URL =
  import.meta.env.VITE_WEATHER_API_URL?.replace(/\/$/, '') ?? CADDY_BASE;

/** Build the automation-service WebSocket URL (exact /ws). */
export function buildWebSocketUrl(): string {
  const explicit = import.meta.env.VITE_WEBSOCKET_URL;
  if (explicit) return appendToken(explicit);

  if (typeof window === 'undefined') {
    return appendToken('ws://localhost:8080/ws');
  }
  const wsProto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const host = window.location.hostname;
  const port = window.location.port === '8080' ? '8080' : '8080';
  return appendToken(`${wsProto}://${host}:${port}/ws`);
}

/** Build-time API key. Never logged. */
export const CEA_API_KEY: string =
  (import.meta.env.VITE_CEA_API_KEY as string | undefined) ?? '';

/**
 * Grafana base URL used by `<GrafanaPanel>` to build the iframe `src`
 * for embedded dashboards (`/d/<uid>`) and panels (`/d-solo/<uid>`).
 *
 * Phase 5b moved Grafana off `iskradocker:3000` and onto
 * `iskraprojectcea:3001` — the same VM as the DB replica, fewer
 * network hops, and the iskradocker monitoring stack can shed its
 * CEA-specific bits in 5c.
 *
 * Phase 7.4: the env is required at *build time* (it ends up baked into
 * the static bundle). We warn loudly at bundle boot if it's missing so
 * an operator notices the silent fallback before iframes start 404-ing
 * against a wrong host. There is no Caddy `/grafana` reverse-proxy
 * entry (see Infrastructure/caddy/Caddyfile), so the iframe continues
 * to point at the Grafana host directly.
 */
const GRAFANA_BASE_URL_DEFAULT = 'http://iskraprojectcea:3001';
const GRAFANA_BASE_URL_ENV = import.meta.env.VITE_GRAFANA_BASE_URL as
  | string
  | undefined;

if (!GRAFANA_BASE_URL_ENV && typeof window !== 'undefined') {
  // Build-time var missing — bundle will still work against the default,
  // but this is a deploy-config smell worth surfacing.
  // eslint-disable-next-line no-console
  console.warn(
    '[config/env] VITE_GRAFANA_BASE_URL not set at build time; ' +
      `falling back to ${GRAFANA_BASE_URL_DEFAULT}. Set it in the deploy env ` +
      'to silence this warning (Phase 7.4).',
  );
}

export const GRAFANA_BASE_URL: string = (
  GRAFANA_BASE_URL_ENV ?? GRAFANA_BASE_URL_DEFAULT
).replace(/\/$/, '');

function appendToken(url: string): string {
  if (!CEA_API_KEY) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(CEA_API_KEY)}`;
}
