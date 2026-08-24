/**
 * Flower monitoring page browser coverage.
 *
 * Runs against the fixture preview on exactly `http://127.0.0.1:4173`. Asserts
 * the native page renders the toolbar, both chart regions, and the canonical
 * tables at 375/768/1280 px, and that no request is made to `/grafana/*` or to
 * any external production endpoint (loopback ports 8000/8001/8003/8080 or the
 * Grafana host). The partial-failure scenario (`MONITORING_SCENARIO=flower-partial`)
 * verifies Back data and recorded history survive when Front and projection are
 * unavailable.
 */
import { test, expect } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

const WIDTHS = [375, 768, 1280]

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

for (const width of WIDTHS) {
  test(`flower monitoring renders natively at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 800 })
    const violations = trackViolations(page)

    await page.goto(fixtureUrl('/flower/monitoring', testInfo))

    await expect(page.getByRole('button', { name: 'Reset Zoom' })).toBeVisible()
    await expect(
      page.getByRole('heading', { name: 'Flower climate conditions' }),
    ).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Flower atmosphere & equipment' })).toBeVisible()

    await expect(page.getByRole('table', { name: 'Averages' })).toBeVisible()
    await expect(page.getByRole('table', { name: 'Front Cluster' })).toBeVisible()
    await expect(page.getByRole('table', { name: 'Back Cluster' })).toBeVisible()
    await expect(
      page.getByRole('table', { name: 'Statistics - All Available Sensors' }),
    ).toBeVisible()

    expect(violations).toEqual([])
  })
}

test('keeps Back and history when Front/projection fail', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/monitoring', testInfo))

  const backTable = page.getByRole('table', { name: 'Back Cluster' })
  await expect(backTable).toBeVisible()
  await expect(backTable.getByText('Dry Bulb')).toBeVisible()
  await expect(backTable.getByText('24.6°C')).toBeVisible({ timeout: 15000 })

  await expect(
    page.getByRole('heading', { name: 'Flower climate conditions' }),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Flower atmosphere & equipment' })).toBeVisible()

  expect(violations).toEqual([])
})
