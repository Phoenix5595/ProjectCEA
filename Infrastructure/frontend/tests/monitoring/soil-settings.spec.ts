/**
 * Sensor Settings sub-tabs (R9): CAN bus and RS485 are separate tab pages
 * (`settings_tab=can|rs485`, default CAN). RS485 probes are placed by native
 * drag (drop point → bed fractions) or per-row buttons; the bus banner reports
 * a pending power cycle. Bodies are asserted on the wire against the guarded
 * loopback fixture.
 */
import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

function trackViolations(page: Page): string[] {
  const violations: string[] = []
  page.on('request', request => {
    const violation = describeViolation(request.url())
    if (violation !== null) violations.push(`${violation}: ${request.url()}`)
  })
  return violations
}

function assignmentRequest(page: Page, method: 'PUT' | 'DELETE', registryId: number) {
  return page.waitForRequest(
    request =>
      request.method() === method &&
      new URL(request.url()).pathname === `/api/sensors/registry/${registryId}/assignment`
  )
}

/** Real HTML5 drag of `source` onto `target`, dropped at a fraction of the target box. */
async function dragTo(
  page: Page,
  source: Locator,
  target: Locator,
  fraction = { x: 0.5, y: 0.5 }
): Promise<void> {
  await target.scrollIntoViewIfNeeded()
  const box = await target.boundingBox()
  if (box === null) throw new Error('drop target must be measurable')
  const point = {
    clientX: box.x + fraction.x * box.width,
    clientY: box.y + fraction.y * box.height,
  }
  const dataTransfer = await page.evaluateHandle(() => new DataTransfer())
  await source.dispatchEvent('dragstart', { dataTransfer })
  await target.dispatchEvent('dragenter', { dataTransfer, ...point })
  await target.dispatchEvent('dragover', { dataTransfer, ...point })
  await target.dispatchEvent('drop', { dataTransfer, ...point })
  await source.dispatchEvent('dragend', { dataTransfer })
}

const RS485_SETTINGS = '/devices?tab=sensors&settings_tab=rs485'

async function openRs485Settings(page: Page, url: string): Promise<void> {
  await page.goto(url)
  await expect(page.getByRole('heading', { name: 'Sensor Settings' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'RS485' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('soil-tag-2')).toBeVisible()
}

test('CAN bus and RS485 are separate sub-tab pages', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/devices?tab=sensors', testInfo))

  const canTab = page.getByRole('tab', { name: 'CAN bus' })
  const rs485Tab = page.getByRole('tab', { name: 'RS485' })
  // No settings_tab lands on CAN; the RS485 page is not mounted.
  await expect(canTab).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('heading', { name: 'CAN bus' })).toBeVisible()
  await expect(page.getByText('Node 1', { exact: true })).toBeVisible()
  await expect(page.getByRole('table', { name: 'RS485 probes' })).toHaveCount(0)

  await rs485Tab.click()
  await expect(rs485Tab).toHaveAttribute('aria-selected', 'true')
  const url = new URL(page.url())
  expect(url.searchParams.get('settings_tab')).toBe('rs485')
  // Switching keeps every other query parameter (here the fixture session).
  expect(url.searchParams.get('fixtureSession')).toBe(testInfo.testId)
  await expect(page.getByRole('heading', { name: 'RS485' })).toBeVisible()
  await expect(page.getByRole('table', { name: 'RS485 probes' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'CAN bus' })).toHaveCount(0)
  await expect(page.getByText('Node 1', { exact: true })).toHaveCount(0)
  await expect(page.getByTestId('soil-bus-status')).toHaveCount(0)
  expect(violations).toEqual([])
})

test('dragging a probe into a bed places it at the drop point', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await openRs485Settings(page, fixtureUrl(RS485_SETTINGS, testInfo))

  const request = assignmentRequest(page, 'PUT', 12)
  await dragTo(page, page.getByTestId('soil-tag-2'), page.getByTestId('soil-bed-back'), {
    x: 0.25,
    y: 0.75,
  })
  expect((await request).postDataJSON()).toEqual({
    kind: 'rs485',
    room: 'Flower Room',
    bed: 'Back Bed',
    x_frac: 0.25,
    y_frac: 0.75,
  })
  await expect(page.getByTestId('soil-bed-back').getByTestId('soil-tag-2')).toBeVisible()
  expect(violations).toEqual([])
})

test('dragging a placed probe onto Unassigned unassigns it', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await openRs485Settings(page, fixtureUrl(RS485_SETTINGS, testInfo))
  await expect(page.getByTestId('soil-bed-back').getByTestId('soil-tag-4')).toBeVisible()

  const request = assignmentRequest(page, 'DELETE', 14)
  await dragTo(page, page.getByTestId('soil-tag-4'), page.getByTestId('soil-unassigned'))
  await request
  await expect(page.getByTestId('soil-unassigned').getByTestId('soil-tag-4')).toBeVisible()
  expect(violations).toEqual([])
})

test('the Veg Room button places a probe room-level', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await openRs485Settings(page, fixtureUrl(RS485_SETTINGS, testInfo))

  const row = page
    .getByRole('table', { name: 'RS485 probes' })
    .getByRole('row')
    .filter({ hasText: 'Soil 3' })
  const request = assignmentRequest(page, 'PUT', 13)
  await row.getByRole('button', { name: 'Veg Room' }).click()
  expect((await request).postDataJSON()).toEqual({ kind: 'rs485', room: 'Veg Room' })
  await expect(page.getByTestId('soil-veg').getByTestId('soil-tag-3')).toBeVisible()
  await expect(row.getByRole('button', { name: 'Veg Room' })).toHaveAttribute(
    'aria-pressed',
    'true'
  )
  expect(violations).toEqual([])
})

test('the bus banner asks for a power cycle with the reserved address', async ({
  page,
}, testInfo) => {
  const violations = trackViolations(page)
  await openRs485Settings(page, fixtureUrl(RS485_SETTINGS, testInfo, undefined, 'soil-power-cycle'))

  await expect(page.getByTestId('soil-bus-status')).toHaveText(
    'Power-cycle the new probe to finish address 3'
  )
  expect(violations).toEqual([])
})
