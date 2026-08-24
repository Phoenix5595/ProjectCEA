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

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (req) => {
    const url = req.url()
    if (url.includes('/grafana/')) violations.push(`grafana: ${url}`)
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
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

  await page.waitForTimeout(1500)
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(violations).toEqual([])
})

test('automation-down shows error, keeps panels, and recovers on retry', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=automation-down', testInfo))

  await expect(page.getByRole('alert').first()).toBeVisible()
  await assertPanelsRender(page)

  await page.waitForTimeout(1500)
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

test('missing-projection shows unavailable status without blanking panels', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=missing-projection', testInfo))

  await expect(page.getByText('Unavailable')).toBeVisible()
  await assertPanelsRender(page)
  expect(violations).toEqual([])
})

test('unknown-photoperiod renders without blanking panels', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=unknown-photoperiod', testInfo))

  await assertPanelsRender(page)
  expect(violations).toEqual([])
})
