/**
 * Event-log browser harness config (test-only).
 *
 * Reuses the monitoring preview server (which now includes event-log fixtures)
 * to run Playwright specs against the event-log UI at 1920x1080 and 1280x1440. The
 * preview serves deterministic REST + SSE fixtures on exactly
 * `http://127.0.0.1:4173`, so no production service is contacted.
 * Set `BASE_URL` for a live read-only run; fixture runs start the local preview server below.
 */
import { defineConfig, devices } from '@playwright/test'

const PORT = 4173
const BASE_URL = process.env.BASE_URL ?? `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './tests/event-log',
  timeout: 45_000,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium-event-log-1920x1080',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } },
      fullyParallel: false,
    },
    {
      name: 'chromium-event-log-1280x1440',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 1440 } },
      fullyParallel: false,
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
      },
    },
})
