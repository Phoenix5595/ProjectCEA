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
 *
 * Fixture and live runs use only 1920x1080 and 1280x1440. Set `BASE_URL` for a
 * live read-only run; fixture runs start the local preview server below.
 */
import { defineConfig, devices } from '@playwright/test'

const PORT = 4173
const BASE_URL = process.env.BASE_URL ?? `http://127.0.0.1:${PORT}`

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
      name: 'chromium-functional-1920x1080',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } },
      testIgnore: '**/performance.spec.ts',
      fullyParallel: true,
    },
    {
      name: 'chromium-functional-1280x1440',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 1440 } },
      testIgnore: '**/performance.spec.ts',
      fullyParallel: true,
    },
    {
      name: 'chromium-performance-1920x1080',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } },
      testMatch: '**/performance.spec.ts',
      dependencies: ['chromium-functional-1920x1080'],
      workers: 1,
    },
    {
      name: 'chromium-performance-1280x1440',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 1440 } },
      testMatch: '**/performance.spec.ts',
      dependencies: ['chromium-functional-1280x1440'],
      workers: 1,
    },
    {
      name: 'firefox-lifecycle-1920x1080',
      use: { ...devices['Desktop Firefox'], viewport: { width: 1920, height: 1080 } },
      testMatch: '**/chart-lifecycle.spec.ts',
    },
    {
      name: 'firefox-lifecycle-1280x1440',
      use: { ...devices['Desktop Firefox'], viewport: { width: 1280, height: 1440 } },
      testMatch: '**/chart-lifecycle.spec.ts',
    },
  ],
  webServer: process.env.BASE_URL ? undefined : {
      command: `npx vite build --config vite.monitoring.config.ts && npx vite preview --config vite.monitoring.config.ts --host 127.0.0.1 --port ${PORT} --strictPort`,
      url: BASE_URL,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: '1',
        VITE_API_BASE_URL: BASE_URL,
        VITE_MONITORING_DEBUG: '1',
        VITE_MONITORING_PERF_MARKS: '1',
      },
    },
})
