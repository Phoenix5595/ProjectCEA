/**
 * Monitoring failure/recovery browser coverage.
 *
 * Runs against the fixture preview on exactly `http://127.0.0.1:4173`. Each
 * scenario is selected via the `?scenario=` query param (threaded to the API
 * requests by the page). Asserts the expected status/banner appears, last-good
 * panels still render (never blanked), and transient failures clear on retry.
 * Also asserts no `/grafana/*` or external-origin request is ever made.
 */
import { test, expect } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

interface RequestEvidence {
  readonly requests: string[]
  readonly violations: string[]
  readonly consoleErrors: string[]
}

function trackRequests(page: import('@playwright/test').Page): RequestEvidence {
  const requests: string[] = []
  const violations: string[] = []
  const consoleErrors: string[] = []
  page.on('request', (req) => {
    const url = req.url()
    requests.push(url)
    if (url.includes('/grafana/')) violations.push(`grafana: ${url}`)
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  return { requests, violations, consoleErrors }
}

function trackViolations(page: import('@playwright/test').Page): string[] {
  return trackRequests(page).violations
}

async function assertPanelsRender(page: import('@playwright/test').Page): Promise<void> {
  await expect(
    page.getByRole('heading', { name: 'Flower climate conditions' }),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Flower atmosphere & equipment' })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Back Cluster' })).toBeVisible()
  await expect(
    page.getByRole('table', { name: 'Statistics - All Available Sensors' }),
  ).toBeVisible()
}

test('backend-down shows error, keeps panels, and recovers on retry', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=backend-down', testInfo))

  await expect(page.getByRole('alert').first()).toBeVisible()
  await assertPanelsRender(page)

  // The fixture serves 503 to the first three sensor requests per session and
  // recovers after. Aborted requests still consume slots without emitting a
  // page-visible response, so observing request counts cannot prove the
  // counter has drained. Pause polling, then probe the fixture with the
  // page's own fixtureSession until it answers 200 — that directly proves
  // Retry will succeed on its range reload.
  await page.getByRole('button', { name: 'Pause' }).click()
  await expect
    .poll(
      () =>
        page.evaluate(async () => {
          const response = await fetch(
            `/api/sensors/monitoring/range/Flower%20Room?start=2026-01-01T00:00:00.000Z&end=2026-01-01T01:00:00.000Z&max_points=10&${new URLSearchParams(location.search)}`,
          )
          return response.status
        }),
      { timeout: 15_000 },
    )
    .toBe(200)

  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(violations).toEqual([])
})

test('range 503 retains sensor data and recovers on retry', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  const failure = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return url.pathname.startsWith('/api/sensors/monitoring/range/') && response.status() === 503
  })
  await page.goto(fixtureUrl('/flower/monitoring?scenario=range-503-after-good', testInfo))
  await failure
  await expect(page.getByRole('table', { name: 'Back Cluster' }).getByText('24.6°C')).toBeVisible()
  await expect(page.getByRole('alert').first()).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'Data stale.' }).first()).toBeVisible()

  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByRole('table', { name: 'Back Cluster' }).getByText('24.6°C')).toBeVisible()

  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(violations).toEqual([])
})

test('automation-down shows error, keeps panels, and recovers on retry', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=automation-down', testInfo))

  await expect(page.getByRole('alert').first()).toBeVisible()
  await assertPanelsRender(page)

  // Same counter-drain probe as backend-down, on the control endpoint: the
  // fixture serves 503 to the first three control requests per session, and
  // aborted requests make an observable count unreliable.
  await page.getByRole('button', { name: 'Pause' }).click()
  await expect
    .poll(
      () =>
        page.evaluate(async () => {
          const response = await fetch(
            `/api/monitoring/control/Flower%20Room/history?start=2026-01-01T00:00:00.000Z&end=2026-01-01T01:00:00.000Z&max_points=10&${new URLSearchParams(location.search)}`,
          )
          return response.status
        }),
      { timeout: 15_000 },
    )
    .toBe(200)

  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(violations).toEqual([])
})

test('malformed-sensor shows error, keeps panels, and recovers on retry', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=malformed-sensor', testInfo))

  await expect(page.getByRole('alert').first()).toBeVisible()
  await assertPanelsRender(page)

  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(violations).toEqual([])
})

test('stale-live marks live values stale without blanking panels', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=stale-live', testInfo))

  await expect(page.getByLabel('Dry Bulb stale').first()).toBeVisible({ timeout: 15000 })
  await assertPanelsRender(page)
  expect(violations).toEqual([])
})

test('missing-projection keeps panels rendered without an error alert', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=missing-projection', testInfo))

  await expect(page.getByRole('alert')).toHaveCount(0)
  await assertPanelsRender(page)
  expect(violations).toEqual([])
})

test('unknown-photoperiod renders without blanking panels', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=unknown-photoperiod', testInfo))

  await assertPanelsRender(page)
  expect(violations).toEqual([])
})

test('transient control history failure recovers autonomously on the same source', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  const { requests, violations } = trackRequests(page)
  await page.goto(fixtureUrl('/flower/monitoring', testInfo, 'transient', 'control-history-transient'))

  await expect(page.getByRole('alert').first()).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'Data stale.' }).first()).toBeVisible()
  await expect(page.getByRole('table', { name: 'Back Cluster' }).getByText('24.6°C')).toBeVisible()

  await expect(page.getByRole('alert')).toHaveCount(0, { timeout: 75_000 })
  await expect(page.getByRole('status').filter({ hasText: 'Data stale.' })).toHaveCount(0)

  const controlHistoryRequests = requests.filter((url) => new URL(url).pathname.endsWith('/history'))
  const controlTailRequests = requests.filter((url) => new URL(url).pathname.endsWith('/tail'))
  expect(controlHistoryRequests.length).toBeGreaterThanOrEqual(2)
  expect(controlTailRequests.length).toBeGreaterThan(0)
  expect(controlHistoryRequests.length).toBeLessThanOrEqual(4)
  expect(violations).toEqual([])
})

test('persistent control history failure remains visible while sensors succeed', async ({ page }, testInfo) => {
  const { requests, violations } = trackRequests(page)
  await page.goto(fixtureUrl('/flower/monitoring', testInfo, 'persistent', 'control-history-persistent'))

  await expect(page.getByRole('alert').first()).toBeVisible()
  await assertPanelsRender(page)
  await expect(page.getByRole('table', { name: 'Back Cluster' }).getByText('24.6°C')).toBeVisible()

  await expect.poll(
    () => requests.filter((url) => url.includes('/api/sensors/monitoring/live/')).length,
    { timeout: 5_000 },
  ).toBeGreaterThanOrEqual(2)
  await expect(page.getByRole('alert').first()).toBeVisible()
  await expect(page.getByRole('table', { name: 'Back Cluster' }).getByText('24.6°C')).toBeVisible()
  expect(violations).toEqual([])
})

test('fixed range has no tail or recorded mutation and Retry recovers its failed load', async ({ page }, testInfo) => {
  const { requests, violations } = trackRequests(page)
  await page.goto(fixtureUrl('/flower/monitoring', testInfo, 'fixed', 'fixed-range-retry'))
  await expect(page.getByRole('table', { name: 'Back Cluster' }).getByText('24.6°C')).toBeVisible()

  const fixedStart = page.getByRole('textbox', { name: 'Start', exact: true })
  const fixedEnd = page.getByRole('textbox', { name: 'End', exact: true })
  await fixedStart.fill('2026-08-02T12:00')
  await fixedEnd.fill('2026-08-02T13:00')
  await page.getByRole('button', { name: 'Apply' }).click()

  await expect(page.getByRole('alert').first()).toBeVisible()
  const fixedBackTable = page.getByRole('table', { name: 'Back Cluster' })
  await expect(fixedBackTable.getByText('24.6°C')).toBeVisible()
  const fixedTailCount = requests.filter((url) => new URL(url).pathname.endsWith('/tail')).length
  const fixedPanelData = await fixedBackTable.innerText()
  await expect.poll(() => fixedBackTable.innerText(), { timeout: 3_000 }).toBe(fixedPanelData)

  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByRole('status').filter({ hasText: 'FIXED' })).toBeVisible()
  await expect(fixedBackTable.getByText('24.6°C')).toBeVisible()

  const laterTailCount = requests.filter((url) => new URL(url).pathname.endsWith('/tail')).length
  expect(laterTailCount).toBe(fixedTailCount)
  expect(violations).toEqual([])
})
