import { test, expect } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

const WIDTHS = [375, 768, 1280]

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (request) => {
    const violation = describeViolation(request.url())
    if (violation !== null) violations.push(`${violation}: ${request.url()}`)
  })
  return violations
}

for (const width of WIDTHS) {
  test(`climate timeline remains usable at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const violations = trackViolations(page)

    await page.goto(fixtureUrl('/flower/control', testInfo))
    await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
    await expect(page.getByText('READ ONLY')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Climate Periods' })).toBeVisible()
    await expect(page.getByTestId('control-timeline-handle-0-start')).toHaveCount(0)

    const firstStart = page.locator('input[placeholder="HH:MM"]').first()
    await expect(firstStart).toHaveValue('06:00')
    await page.getByRole('button', { name: 'Expand editor' }).click()
    await expect(page.getByText('EDITABLE')).toBeVisible()
    await expect(page.getByTestId('control-timeline-handle-0-start')).toBeVisible()

    await page.getByTestId('control-timeline-handle-0-start').focus()
    await page.keyboard.press('ArrowRight')
    await expect(firstStart).toHaveValue('06:05')
    await page.getByRole('button', { name: 'Review' }).click()
    await expect(page.getByText('Reviewed draft 1')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Apply' })).toBeEnabled()
    await page.screenshot({
      path: `.omo/evidence/projected-climate-timeline/browser/task-9/task-9-${width}.png`,
      fullPage: true,
    })

    await page.getByRole('button', { name: 'Apply' }).click()
    await expect(page.getByText('No review yet')).toBeVisible()
    expect(violations).toEqual([])
  })
}

test('keeps the control shell in the viewport while the primary table scrolls inside its own shell', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 900 })
  await page.goto(fixtureUrl('/flower/control', testInfo))
  await expect(page.getByRole('heading', { name: 'Climate Periods' })).toBeVisible()

  const layout = await page.evaluate(() => {
    const table = document.querySelector('table')
    const tableShell = table?.parentElement ?? null
    return {
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      tableWidth: table?.scrollWidth ?? 0,
      tableShellWidth: tableShell?.clientWidth ?? 0,
      tableOverflowX: tableShell === null ? '' : getComputedStyle(tableShell).overflowX,
    }
  })

  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth)
  expect(layout.tableWidth).toBeGreaterThan(layout.tableShellWidth)
  expect(layout.tableOverflowX).toBe('auto')
})

test('keeps the primary climate periods table functional when the saved timeline API fails', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo, undefined, 'timeline-api-failure'))

  const table = page.getByRole('table')
  await expect(table).toBeVisible()
  const firstStart = table.locator('input[placeholder="HH:MM"]').first()
  await firstStart.fill('06:15')
  await expect(firstStart).toHaveValue('06:15')
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toHaveCount(0)
  expect(violations).toEqual([])
})
