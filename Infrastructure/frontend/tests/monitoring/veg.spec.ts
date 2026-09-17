/**
 * Veg monitoring page browser coverage.
 *
 * Runs against the fixture preview on exactly `http://127.0.0.1:4173`. Asserts
 * the native page renders the toolbar, both chart regions, and the canonical
 * tables at the two configured desktop viewports, and that no request is made to `/grafana/*` or to
 * any external production endpoint (loopback ports 8000/8001/8003/8080 or the
 * Grafana host).
 */
import { expect, test } from '@playwright/test'
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

test('veg monitoring renders natively', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/vegetation/monitoring', testInfo))

  await expect(page.getByRole('button', { name: 'Reset Zoom' })).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'Veg climate conditions' }),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Veg atmosphere & equipment' })).toBeVisible()

  await expect(page.getByRole('table', { name: 'Sensor Values' })).toBeVisible()
  await expect(
    page.getByRole('table', { name: 'Statistics - All Available Sensors' }),
  ).toBeVisible()

  expect(violations).toEqual([])
})
