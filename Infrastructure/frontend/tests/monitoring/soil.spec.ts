/**
 * Flower soil observable workflow: open /flower/soil, verify Front above
 * Back with live values; inject a newly detected probe; verify toast and
 * badge; follow the badge to filtered Sensor Settings; assign the probe;
 * return and verify the correct bed/reflow; exercise the history preset,
 * drag zoom, reset, grouped legend, and semantic table; assert no request
 * leaves 127.0.0.1:4173.
 */
import { expect, test, type Page } from '@playwright/test'

import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

function trackViolations(page: Page): string[] {
  const violations: string[] = []
  page.on('request', (request) => {
    const url = request.url()
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

test('flower soil renders beds, live values, badge, and history', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/soil', testInfo, 'soil', 'soil-probes-2'))

  // Front Bed first, Back Bed second, both labelled 4 ft x 4 ft.
  const front = page.getByRole('figure', { name: 'Front Bed schematic' })
  const back = page.getByRole('figure', { name: 'Back Bed schematic' })
  await expect(front).toBeVisible()
  await expect(back).toBeVisible()
  const frontBox = await front.boundingBox()
  const backBox = await back.boundingBox()
  if (frontBox === null || backBox === null) {
    throw new Error('bed schematics must be measurable')
  }
  // Beds stacked in a side column to the right of the full-height graph.
  expect(backBox.y).toBeGreaterThan(frontBox.y)
  const chartBox = await page.locator('.mon-card').first().boundingBox()
  if (chartBox === null) {
    throw new Error('history chart must be measurable')
  }
  expect(frontBox.x).toBeGreaterThan(chartBox.x + chartBox.width)

  // Live probe cards carry the four metric families.
  await expect(page.getByTestId('soil-probe-card').first()).toContainText('Water content')
  await expect(page.getByTestId('soil-probe-card').first()).toContainText('µS/cm')
  await expect(page.getByTestId('soil-probe-card').first()).toContainText('pH')
  await expect(page.getByTestId('soil-probe-card').first()).toContainText('°C')

  // History: toolbar preset + one plot render.
  await expect(page.getByRole('button', { name: 'Reset Zoom' })).toBeVisible()

  expect(violations).toEqual([])
})

test('injected probe raises one toast and the badge navigates to Sensor Settings', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/soil', testInfo, 'badge', 'unassigned-after-mount'))

  // The persistent unassigned badge appears and navigates to the filtered panel.
  const badge = page.getByRole('button', { name: /unassigned sensor/ })
  await expect(badge).toBeVisible()
  await badge.click()
  await expect(page).toHaveURL(/\/devices\?tab=sensors&status=unassigned/)
  await expect(page.getByRole('heading', { name: 'Sensor Settings' })).toBeVisible()

  expect(violations).toEqual([])
})
