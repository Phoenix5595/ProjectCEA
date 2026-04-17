/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Single entrypoint (Caddy reverse proxy) base URL. Preferred. */
  readonly VITE_API_BASE_URL?: string
  /** Per-service escape-hatch overrides (unset by default). */
  readonly VITE_BACKEND_API_URL?: string
  readonly VITE_AUTOMATION_API_URL?: string
  readonly VITE_WEATHER_API_URL?: string
  readonly VITE_WEBSOCKET_URL?: string
  /** Build-time API key; sent as X-API-Key header + ?token=<key> on WS. */
  readonly VITE_CEA_API_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

