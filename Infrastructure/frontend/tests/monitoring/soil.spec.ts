/**
 * Flower soil observable workflow: open /flower/soil, verify Back above
 * Front beside the history chart, live probe cards, the default Moisture
 * heatmap painted under a placed probe, a hollow marker for a placement
 * closed 36 h ago and the legend; follow the unassigned badge to filtered
 * Sensor Settings; assert no request leaves the test-only loopback origin.
 */
import { expect, test, type Page } from '@playwright/test'

import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

function trackViolations(page: Page): string[] {
  const violations: string[] = []
  page.on('request', request => {
    const url = request.url()
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

test('flower soil renders beds, heatmap, live values, and history', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/soil', testInfo, 'soil'))

  // Back Bed first, Front Bed second.
  const front = page.getByRole('figure', { name: 'Front Bed bed schematic' })
  const back = page.getByRole('figure', { name: 'Back Bed bed schematic' })
  await expect(front).toBeVisible()
  await expect(back).toBeVisible()
  const frontBox = await front.boundingBox()
  const backBox = await back.boundingBox()
  if (frontBox === null || backBox === null) {
    throw new Error('bed schematics must be measurable')
  }
  // Monitoring layout: beds are the sticky side column (left), the full-height
  // graph is the main column.
  expect(backBox.y).toBeLessThan(frontBox.y)
  const chartBox = await page.locator('.mon-main .mon-card').first().boundingBox()
  if (chartBox === null) {
    throw new Error('history chart must be measurable')
  }
  expect(frontBox.x + frontBox.width).toBeLessThan(chartBox.x)

  // Live probe cards carry the four metric families.
  await expect(page.getByTestId('soil-probe-card').first()).toContainText('Water content')
  await expect(page.getByTestId('soil-probe-card').first()).toContainText('µS/cm')
  await expect(page.getByTestId('soil-probe-card').first()).toContainText('pH')
  await expect(page.getByTestId('soil-probe-card').first()).toContainText('°C')

  // Moisture is the default heatmap: the cell under Soil 4 (0.3, 0.4) is painted.
  await expect(page.getByRole('button', { name: 'Moisture' })).toHaveAttribute(
    'aria-pressed',
    'true'
  )
  const heatmap = page.getByTestId('soil-heatmap-back')
  await expect
    .poll(() =>
      heatmap.evaluate(canvas => {
        const context = (canvas as HTMLCanvasElement).getContext('2d')
        return context === null ? 0 : (context.getImageData(9, 12, 1, 1).data[3] ?? 0)
      })
    )
    .toBeGreaterThan(150)
  await expect(page.getByTestId('soil-marker-closed-6')).toHaveText('#6 · 36 h')
  await expect(page.getByTestId('soil-heatmap-legend').first()).toBeVisible()

  // History: toolbar preset + one plot render.
  await expect(page.getByRole('button', { name: 'Reset Zoom' })).toBeVisible()

  expect(violations).toEqual([])
})

test('the unassigned badge navigates to Sensor Settings', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/soil', testInfo, 'badge'))

  // The persistent unassigned badge appears and navigates to the filtered panel.
  const badge = page.getByRole('button', { name: /unassigned sensor/ })
  await expect(badge).toBeVisible()
  await badge.click()
  await expect(page).toHaveURL(/\/devices\?tab=sensors&status=unassigned/)
  await expect(page.getByRole('heading', { name: 'Sensor Settings' })).toBeVisible()

  expect(violations).toEqual([])
})
