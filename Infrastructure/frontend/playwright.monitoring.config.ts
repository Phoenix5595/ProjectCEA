/**
 * Monitoring browser harness config (test-only).
 *
 * Builds the production `dist` with the default Vite config, then serves it
 * through the monitoring preview config (`vite.monitoring.config.ts`) on
 * exactly `http://127.0.0.1:4173`. The preview middleware serves deterministic
 * REST / WebSocket / Grafana-placeholder / SPA-fallback fixtures and injects a
 * restrictive CSP, so a correctly-built page never touches a production
 * service.
 *
 * No browser is installed or downloaded here: `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD`
 * is set for the webServer command and the config relies on an already-present
 * local Chromium. `reuseExistingServer: false` guarantees a fresh preview for
 * every run.
 */
import { defineConfig, devices } from '@playwright/test'

const PORT = 4173
const BASE_URL = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './tests/monitoring',
  timeout: 30_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium-functional',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: '**/performance.spec.ts',
      fullyParallel: true,
    },
    {
      name: 'chromium-performance',
      use: { ...devices['Desktop Chrome'] },
      testMatch: '**/performance.spec.ts',
      dependencies: ['chromium-functional'],
      workers: 1,
    },
  ],
  webServer: {
    command: `npx vite build --config vite.monitoring.config.ts && npx vite preview --config vite.monitoring.config.ts --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: '1',
      VITE_API_BASE_URL: BASE_URL,
    },
  },
})
