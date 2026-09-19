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

test('grouped alert console opens by default with category rows, counts, and dual timestamps', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower', testInfo, undefined, 'grouped-console'))

  // One colour-coded row per category, newest-category first, all room categories visible.
  await expect(page.getByTestId('event-group-control')).toBeVisible()
  await expect(page.getByTestId('event-group-ramp')).toBeVisible()
  await expect(page.getByTestId('event-group-relay')).toBeVisible()
  await expect(page.getByTestId('event-group-manual_override')).toBeVisible()
  await expect(page.getByTestId('event-group-mutation')).toBeVisible()
  await expect(page.getByTestId('event-group-alarm')).toBeVisible()
  await expect(page.getByTestId('event-group-sensor')).toBeVisible()
  await expect(page.getByTestId('event-group-sensor')).toContainText('Sensor degraded')

  // Count badge + latest-event summary per row.
  await expect(page.getByTestId('event-group-control').getByText('3 events')).toBeVisible()
  await expect(page.getByTestId('event-group-control').getByText('Control setpoint changed')).toBeVisible()
  await expect(page.getByTestId('event-group-relay').getByText('3 events')).toBeVisible()
  await expect(page.getByTestId('event-group-relay')).toContainText('Relay command failed')
  await expect(page.getByTestId('event-group-alarm')).toContainText('Alarm triggered')

  // Dual timestamps: relative chip AND a visible absolute clock time (HH:MM:SS).
  await expect(page.getByTestId('event-group-control').locator('time').last()).toHaveText(/\d{2}:\d{2}:\d{2}/)
  await expect(page.getByTestId('event-group-control').locator('time').first()).toContainText(/ago$/)

  // Concurrent-entity summary for the three-light setpoint ramp.
  await expect(page.getByTestId('event-group-control').getByText(/3 devices in the last 10 minutes/)).toBeVisible()

  await page.screenshot({ path: `${testInfo.outputDir}/grouped-console.png`, fullPage: true })

  expect(violations).toEqual([])
})

test('relay-active state text renders in the green category shade', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower', testInfo, undefined, 'grouped-console'))
  const relayRow = page.getByTestId('event-group-relay')
  await expect(relayRow).toBeVisible()
  await expect(relayRow).toContainText('Relay command failed')

  // Expanding the relay category surfaces the engaged ON state row.
  await relayRow.click()
  const items = page.getByRole('list', { name: 'Event list' }).getByRole('listitem')
  await expect(items).toHaveCount(3)
  const onState = items.filter({ hasText: 'Relay state changed' }).getByText('ON', { exact: true }).first()
  await expect(onState).toHaveClass(/text-\[var\(--event-relay\)\]/)

  expect(violations).toEqual([])
})

test('from-to setpoint values render on enriched rows and in expanded lists', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower', testInfo, undefined, 'grouped-console'))
  await page.getByTestId('event-group-control').click()

  const list = page.getByRole('list', { name: 'Event list' })
  await expect(list).toBeVisible()
  const items = list.getByRole('listitem')
  await expect(items).toHaveCount(3)
  await expect(items.first()).toContainText('44.2% → 43.8%')
  await expect(items.nth(1)).toContainText('44.4% → 44.2%')

  // The collapsed state is one control away.
  await page.getByTestId('event-group-collapse').click()
  await expect(page.getByTestId('event-group-control')).toBeVisible()

  expect(violations).toEqual([])
})

test('the All events toggle shows the flat newest-first list and filters still work', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower', testInfo, undefined, 'grouped-console'))
  await page.getByRole('button', { name: 'All events' }).click()
  const list = page.getByRole('list', { name: 'Event list' })
  await expect(list).toBeVisible()
  await expect(list.getByRole('listitem')).toHaveCount(11)
  await expect(list.getByRole('listitem').first()).toContainText('Relay command failed')

  // Severity filtering still applies in the flat view.
  await page.getByRole('button', { name: 'Critical' }).click()
  await expect(list.getByRole('listitem')).toHaveCount(1)
  await expect(list.getByRole('listitem').first()).toContainText('Alarm triggered')

  await page.screenshot({ path: `${testInfo.outputDir}/flat-filtered.png` })

  expect(violations).toEqual([])
})

test('keeps the empty state visible when the room has no events', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower', testInfo, undefined, 'empty'))
  await expect(page.getByText('No events yet')).toBeVisible()

  expect(violations).toEqual([])
})
