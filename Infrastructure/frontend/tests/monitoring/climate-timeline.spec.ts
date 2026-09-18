import { test, expect } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (request) => {
    const violation = describeViolation(request.url())
    if (violation !== null) violations.push(`${violation}: ${request.url()}`)
  })
  return violations
}

test('climate timeline remains usable', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo))
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
  await expect(page.getByText('READ ONLY')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Climate Periods' })).toBeVisible()
  await expect(page.getByTestId('control-timeline-handle-0-start')).toHaveCount(0)
  await expect(page.getByTestId('calendar-transition-skipped-overlay')).toHaveCount(0)

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
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByText('Reviewed draft 1')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Apply' })).toBeDisabled()
  expect(violations).toEqual([])
})

test('renders a skipped calendar transition over the saved reality in compact and expanded modes', async ({ page }, testInfo) => {
  await page.goto(fixtureUrl('/flower/control', testInfo, undefined, 'calendar-transition-skipped'))

  const overlay = page.getByTestId('calendar-transition-skipped-overlay')
  await expect(overlay).toBeVisible()
  await expect(overlay).toHaveAttribute('aria-label', 'Calendar transition skipped: unknown_mode')
  await expect(page.getByText('Day cycle')).toBeVisible()
  await expect(page.getByText('Calendar transition skipped: unknown_mode')).toBeVisible()

  await page.getByRole('button', { name: 'Expand editor' }).click()
  await expect(page.getByText('EDITABLE')).toBeVisible()
  await expect(overlay).toBeVisible()
  await expect(page.getByTestId('control-timeline-handle-0-start')).toBeVisible()
})

for (const scenario of ['timeline-preview-failed', 'timeline-preview-stale', 'timeline-wrong-room'] as const) {
  test(`rejects ${scenario} without an unsafe Apply call`, async ({ page }, testInfo) => {
    const violations = trackViolations(page)
    const applyRequests: string[] = []
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.endsWith('/apply')) applyRequests.push(request.url())
    })

    await page.goto(fixtureUrl('/flower/control', testInfo, undefined, scenario))
    await page.getByRole('button', { name: 'Expand editor' }).click()
    await page.getByTestId('control-timeline-handle-0-start').focus()
    await page.keyboard.press('ArrowRight')
    await page.getByRole('button', { name: 'Review' }).click()

    await expect(page.getByText('Review failed for draft 1')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Apply' })).toBeDisabled()
    expect(applyRequests).toEqual([])
    expect(violations).toEqual([])
  })
}

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

test('surfaces timeline_unavailable while retaining the fallback periods UI', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo, undefined, 'timeline-unavailable-409'))

  await expect(page.getByRole('table')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toHaveCount(0)
  await expect(page.getByText('saved timeline unavailable (fixture)')).toBeVisible()
  expect(violations).toEqual([])
})

test('uses the canonical mode instead of contradictory API is_constant flags', async ({ page }, testInfo) => {
  await page.goto(fixtureUrl('/vegetation/control', testInfo, undefined, 'veg-constant-flag'))
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
  await expect(page.getByText('READ ONLY')).toBeVisible()

  await page.goto(fixtureUrl('/flower/control', testInfo, 'sleep', 'sleep-scheduled-flag'))
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
  await expect(page.getByText('READ ONLY')).toBeVisible()
})

async function canvasPixelStats(page: import('@playwright/test').Page, selector: string): Promise<{
  orangeRows: number
  grayStrip: number
}> {
  return page.evaluate((sel) => {
    const host = document.querySelector(sel)
    const canvas = host?.querySelector('canvas')
    if (!(canvas instanceof HTMLCanvasElement)) return { orangeRows: -1, grayStrip: -1 }
    const ctx = canvas.getContext('2d')
    if (!ctx) return { orangeRows: -1, grayStrip: -1 }
    const { width, height } = canvas
    const data = ctx.getImageData(0, 0, width, height).data
    const orangeRows = new Set<number>()
    let grayStrip = 0
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const index = (y * width + x) * 4
        const r = data[index], g = data[index + 1], b = data[index + 2]
        if (Math.abs(r - 234) < 45 && Math.abs(g - 88) < 45 && b < 60) orangeRows.add(y)
        if (y > height * 0.9 && Math.abs(r - g) < 8 && Math.abs(g - b) < 8 && r > 30 && r < 200) grayStrip += 1
      }
    }
    return { orangeRows: orangeRows.size, grayStrip }
  }, selector)
}

test('expanded daily editor supports mouse drags and two-way table sync', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo))
  await page.getByRole('button', { name: 'Expand editor' }).click()
  await expect(page.getByText('EDITABLE')).toBeVisible()
  await page.getByRole('button', { name: 'daily' }).click()
  await expect(page.getByTestId('control-timeline-value-grip-0-heating')).toBeVisible()

  const stats = await canvasPixelStats(page, '[data-testid="control-timeline-uplot"]')
  expect(stats.orangeRows).toBeGreaterThanOrEqual(4)
  expect(stats.grayStrip).toBeGreaterThan(0)

  const grip = page.getByTestId('control-timeline-value-grip-0-heating')
  const gripBox = await grip.boundingBox()
  if (!gripBox) throw new Error('value grip not positioned')
  await page.mouse.move(gripBox.x + 5, gripBox.y + 5)
  await page.mouse.down()
  await page.mouse.move(gripBox.x + 5, Math.max(gripBox.y - 80, 0), { steps: 10 })
  await page.mouse.up()

  const heatInput = page.locator('input[placeholder="°C"]').first()
  await expect(heatInput).not.toHaveValue(/^22$/)

  const startGrip = page.getByTestId('control-timeline-boundary-grip-1-start')
  const startBox = await startGrip.boundingBox()
  if (!startBox) throw new Error('boundary grip not positioned')
  await page.mouse.move(startBox.x + 4, startBox.y + 20)
  await page.mouse.down()
  await page.mouse.move(startBox.x + 84, startBox.y + 20, { steps: 10 })
  await page.mouse.up()

  const nightStart = page.locator('input[placeholder="HH:MM"]').nth(2)
  const boundaryValue = await nightStart.inputValue()
  expect(boundaryValue).not.toBe('18:00')
  const minutes = Number(boundaryValue.slice(0, 2)) * 60 + Number(boundaryValue.slice(3, 5))
  expect(minutes % 5).toBe(0)

  const heatBefore = page.locator('input[placeholder="°C"]').first()
  await heatBefore.fill('24')
  await expect(page.getByTestId('control-timeline-effective-stale')).toBeVisible()
  await expect(page.getByText('Reviewed draft')).toBeVisible({ timeout: 3000 })
  await expect(page.getByTestId('control-timeline-effective-stale')).toHaveCount(0, { timeout: 3000 })

  await page.getByRole('button', { name: 'Collapse editor' }).click()
  await expect(page.getByText('READ ONLY')).toBeVisible()
  await expect(page.getByTestId('control-timeline-value-grip-0-heating')).toHaveCount(0)
  expect(violations).toEqual([])
})

test('renders faint period labels and archives viewport screenshots', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  const browserDir = 'browser/'

  await page.goto(fixtureUrl('/flower/control', testInfo))
  await page.screenshot({ path: `${browserDir}climate-timeline-compact-${testInfo.project.name}.png`, fullPage: false })

  await page.getByRole('button', { name: 'Expand editor' }).click()
  await expect(page.getByText('EDITABLE')).toBeVisible()
  await page.screenshot({ path: `${browserDir}climate-timeline-expanded-${testInfo.project.name}.png`, fullPage: false })

  await expect(page.getByTestId('control-timeline-period-legend')).toContainText('Day cycle')
  const stats = await canvasPixelStats(page, '[data-testid="control-timeline-uplot"]')
  expect(stats.grayStrip).toBeGreaterThan(0)
  expect(violations).toEqual([])
})

test('header Save through the draft persists, and a 409 keeps the draft', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  const applyCalls: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.endsWith('/apply')) applyCalls.push(request.url())
  })

  await page.goto(fixtureUrl('/flower/control', testInfo))
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
  const heat = page.locator('input[placeholder="°C"]').first()
  await expect(await heat.inputValue()).toBe('24')
  await heat.fill('23.5')
  await page.getByRole('button', { name: 'SAVE' }).click()
  await expect(page.getByText(/^Saved$/, { exact: true })).toBeVisible({ timeout: 5000 })
  await expect(applyCalls).toHaveLength(1)
  await expect(page.locator('input[placeholder="°C"]').first()).toHaveValue('24')
  expect(violations).toEqual([])
})

test('surfaces a 409 conflict from the header Save and preserves the draft', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo, undefined, 'timeline-apply-conflict'))
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
  const heat = page.locator('input[placeholder="°C"]').first()
  await heat.fill('23.5')
  await page.getByRole('button', { name: 'SAVE' }).click()
  await expect(page.getByText(/conflict/i)).toBeVisible({ timeout: 5000 })
  await expect(page.locator('input[placeholder="°C"]').first()).toHaveValue('23.5')
  expect(violations).toEqual([])
})
