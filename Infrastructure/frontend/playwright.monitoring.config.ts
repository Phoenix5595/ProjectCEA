/**
 * Monitoring browser harness config (test-only).
 *
 * Builds the production `dist` with the default Vite config, then serves it
 * through the monitoring preview config (`vite.monitoring.config.ts`) on
 * exactly `http://127.0.0.1:${MONITORING_FIXTURE_PORT}`. The preview middleware serves deterministic
 * REST / WebSocket / Grafana-placeholder / SPA-fallback fixtures and injects a
 * restrictive CSP, so a correctly-built page never touches a production
 * service.
 *
 * No browser is installed or downloaded here: `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD`
 * is set for the webServer command and the config relies on an already-present
 * local Chromium. `reuseExistingServer: false` guarantees a fresh preview for
 * every run.
 *
 * Monitoring functional projects remain 1920x1080 and 1280x1440. Two focused
 * dashboard fixture projects add 960x1080 and a custom Pixel 9 Pro XL profile.
 * Set `BASE_URL` only for existing live read-only monitoring runs.
 */
import { defineConfig, devices } from '@playwright/test'

const PORT = Number(process.env.MONITORING_FIXTURE_PORT ?? 4173)
const FIXTURE_ORIGIN = `http://127.0.0.1:${PORT}`
const FIXTURE_WS_ORIGIN = `ws://127.0.0.1:${PORT}`
const BASE_URL = process.env.BASE_URL ?? FIXTURE_ORIGIN

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
      name: 'chromium-functional-960x1080',
      use: { ...devices['Desktop Chrome'], viewport: { width: 960, height: 1080 } },
      testMatch: '**/dashboard-layout.spec.ts',
      fullyParallel: true,
    },
    {
      name: 'chromium-functional-pixel-9-pro-xl',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 448, height: 997 },
        deviceScaleFactor: 3,
        isMobile: true,
        hasTouch: true,
      },
      testMatch: '**/dashboard-layout.spec.ts',
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
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: `npx vite build --config vite.monitoring.config.ts && npx vite preview --config vite.monitoring.config.ts --host 127.0.0.1 --port ${PORT} --strictPort`,
        url: BASE_URL,
        reuseExistingServer: false,
        timeout: 120_000,
        env: {
          PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: '1',
          MONITORING_FIXTURE_PORT: String(PORT),
          VITE_API_BASE_URL: FIXTURE_ORIGIN,
          VITE_BACKEND_API_URL: FIXTURE_ORIGIN,
          VITE_AUTOMATION_API_URL: FIXTURE_ORIGIN,
          VITE_WEATHER_API_URL: FIXTURE_ORIGIN,
          VITE_MONITORING_API_URL: FIXTURE_ORIGIN,
          VITE_WEBSOCKET_URL: `${FIXTURE_WS_ORIGIN}/ws`,
          VITE_MONITORING_DEBUG: '1',
          VITE_MONITORING_PERF_MARKS: '1',
        },
      },
})
