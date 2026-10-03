import { test, expect } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', request => {
    const violation = describeViolation(request.url())
    if (violation !== null) violations.push(`${violation}: ${request.url()}`)
  })
  return violations
}

test('grouped alert console opens by default with category rows, counts, and dual timestamps', async ({
  page,
}, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/', testInfo, undefined, 'grouped-console'))

  // One card per presentation bucket: raw ramp merges into Control and raw
  // mutation into System, so six cards render.
  await expect(page.getByTestId('event-group-control')).toBeVisible()
  await expect(page.getByTestId('event-group-relay')).toBeVisible()
  await expect(page.getByTestId('event-group-manual_override')).toBeVisible()
  await expect(page.getByTestId('event-group-alarm')).toBeVisible()
  await expect(page.getByTestId('event-group-sensor')).toBeVisible()
  await expect(page.getByTestId('event-group-system')).toBeVisible()
  await expect(page.getByTestId('event-group-sensor')).toContainText('Sensor degraded')

  // Normal short-label fixture: the dashboard fits the viewport without page
  // scroll (no component scrolling involved).
  const viewportFit = await page.evaluate(() => ({
    doc: document.documentElement.scrollHeight,
    client: document.documentElement.clientHeight,
  }))
  expect(viewportFit.doc).toBeLessThanOrEqual(viewportFit.client + 1)

  // Count badge + latest-event summary per row; Control merges 3 setpoint
  // changes with the newer ramp.started event.
  await expect(
    page.getByTestId('event-group-control').getByText('4', { exact: true })
  ).toBeVisible()
  await expect(page.getByTestId('event-group-control').getByText('Ramp started')).toBeVisible()
  await expect(page.getByTestId('event-group-relay').getByText('3', { exact: true })).toBeVisible()
  await expect(page.getByTestId('event-group-relay')).toContainText('Relay command failed')
  await expect(page.getByTestId('event-group-alarm')).toContainText('Alarm triggered')

  // Dual timestamps: relative chip AND a visible absolute clock time (HH:MM:SS).
  await expect(page.getByTestId('event-group-control').locator('time').last()).toHaveText(
    /\d{2}:\d{2}:\d{2}/
  )
  await expect(page.getByTestId('event-group-control').locator('time').first()).toContainText(/ago/)

  await page.screenshot({ path: `${testInfo.outputDir}/grouped-console.png`, fullPage: true })

  expect(violations).toEqual([])
})

test('relay-active state text renders in the green category shade', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/', testInfo, undefined, 'grouped-console'))
  const relayRow = page.getByTestId('event-group-relay')
  await expect(relayRow).toBeVisible()
  await expect(relayRow).toContainText('Relay command failed')

  // Expanding the relay category surfaces the engaged ON state row.
  await relayRow.click()
  const items = page.getByRole('list', { name: 'Event list' }).getByRole('listitem')
  await expect(items).toHaveCount(3)
  const onState = items
    .filter({ hasText: 'Relay state changed' })
    .getByText('ON', { exact: true })
    .first()
  const actualRelayColor = await onState.evaluate(element => getComputedStyle(element).color)
  const expectedRelayColor = await page.evaluate(() => {
    const probe = document.createElement('span')
    probe.style.color = 'var(--event-relay)'
    document.body.append(probe)
    const color = getComputedStyle(probe).color
    probe.remove()
    return color
  })
  expect(actualRelayColor).toBe(expectedRelayColor)

  expect(violations).toEqual([])
})

test('from-to setpoint values render on enriched rows and in expanded lists', async ({
  page,
}, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/', testInfo, undefined, 'grouped-console'))
  await page.getByTestId('event-group-control').click()

  const list = page.getByRole('list', { name: 'Event list' })
  await expect(list).toBeVisible()
  const items = list.getByRole('listitem')
  await expect(items).toHaveCount(4)
  await expect(items.first()).toContainText('Ramp started')
  await expect(items.nth(1)).toContainText('44.2% → 43.8%')
  await expect(items.nth(2)).toContainText('44.4% → 44.2%')
  await expect(items.nth(3)).toContainText('44.6% → 44.4%')

  // The collapsed state is one control away.
  await page.getByTestId('event-group-collapse').click()
  await expect(page.getByTestId('event-group-control')).toBeVisible()

  expect(violations).toEqual([])
})

test('the All events toggle shows the flat newest-first list and filters still work', async ({
  page,
}, testInfo) => {
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
