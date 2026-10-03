import { test, expect } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'
import {
  EVENT_BUCKET_ORDERS,
  type EventBucketCount,
} from '../../src/features/event-log/presentation/categoryTheme'

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', request => {
    const violation = describeViolation(request.url())
    if (violation !== null) violations.push(`${violation}: ${request.url()}`)
  })
  return violations
}

test('grouped alert console opens by default, reacts to window height, and keeps raw filters', async ({
  page,
}, testInfo) => {
  const violations = trackViolations(page)

  const bucketCountOf = (height: number): EventBucketCount => (height >= 1440 ? 8 : 6)
  const initialViewport = page.viewportSize()!
  const initialPolicy = bucketCountOf(initialViewport.height)
  const otherViewport =
    initialPolicy === 8 ? { width: 1920, height: 1080 } : { width: 1280, height: 1440 }
  const otherPolicy = bucketCountOf(otherViewport.height)
  const categoryCard = (category: string) => page.getByTestId(`event-group-${category}`)
  const groupedIds = () =>
    page
      .locator('[data-testid^="event-group-"]')
      .evaluateAll(elements => elements.map(element => element.getAttribute('data-testid')))
  const expectGroupedIds = async (policy: EventBucketCount) => {
    await expect(page.getByRole('group', { name: 'Grouped alert console' })).toBeVisible()
    await expect
      .poll(groupedIds)
      .toEqual(EVENT_BUCKET_ORDERS[policy].map(category => `event-group-${category}`))
  }

  await page.goto(fixtureUrl('/', testInfo, undefined, 'grouped-console'))
  await expectGroupedIds(initialPolicy)
  await expect(categoryCard('sensor')).toContainText('Sensor degraded')

  // Normal short-label fixture: the dashboard fits the viewport without page
  // scroll (no component scrolling involved) at BOTH supported heights.
  const viewportFit = async () =>
    page.evaluate(() => ({
      doc: document.documentElement.scrollHeight,
      client: document.documentElement.clientHeight,
    }))
  const expectViewportFit = async () => {
    const fit = await viewportFit()
    expect(fit.doc).toBeLessThanOrEqual(fit.client + 1)
  }
  await expectViewportFit()

  // Count badge + latest-event summary per row; the policy decides whether
  // ramp/mutation merge into their neighbours.
  await expect(categoryCard('relay').getByText('3', { exact: true })).toBeVisible()
  await expect(categoryCard('relay')).toContainText('Relay command failed')
  await expect(categoryCard('alarm')).toContainText('Alarm triggered')
  if (initialPolicy === 6) {
    await expect(categoryCard('control').getByText('4', { exact: true })).toBeVisible()
    await expect(categoryCard('control').getByText('Ramp started')).toBeVisible()
    await expect(page.getByTestId('event-group-ramp')).toHaveCount(0)
    await expect(page.getByTestId('event-group-mutation')).toHaveCount(0)
  } else {
    await expect(categoryCard('control').getByText('3', { exact: true })).toBeVisible()
    await expect(categoryCard('control').getByText('Control setpoint changed')).toBeVisible()
    await expect(categoryCard('ramp').getByText('1', { exact: true })).toBeVisible()
    await expect(categoryCard('ramp').getByText('Ramp started')).toBeVisible()
    await expect(categoryCard('mutation').getByText('1', { exact: true })).toBeVisible()
    await expect(categoryCard('mutation')).toContainText('Configuration updated')
    await expect(categoryCard('system')).toContainText('No recent events')
    await expect(categoryCard('system')).toHaveAttribute('aria-disabled', 'true')
  }

  // Dual timestamps: relative chip AND a visible absolute clock time (HH:MM:SS).
  await expect(categoryCard('control').locator('time').last()).toHaveText(/\d{2}:\d{2}:\d{2}/)
  await expect(categoryCard('control').locator('time').first()).toContainText(/ago/)

  await page.screenshot({ path: `${testInfo.outputDir}/grouped-console.png`, fullPage: true })

  // Live height transition leg: before switching, expand the category that
  // changes meaning across policies (raw ramp when tall, merged control when
  // short), open its inline detail, and select one raw filter. The transition
  // must return to the proper overview with the filter preserved and no stale
  // detail or page position.
  if (initialPolicy === 8) {
    await categoryCard('ramp').click()
  } else {
    await categoryCard('control').click()
  }
  const expandedList = page.getByRole('list', { name: 'Event list' })
  await expect(expandedList.getByRole('listitem')).toHaveCount(initialPolicy === 8 ? 1 : 4)
  await expandedList.getByTestId('event-detail-opener').first().click()
  await expect(page.getByRole('region', { name: 'Selected event details' })).toBeVisible()
  await page.getByRole('button', { name: /^Filters/ }).click()
  await page.getByRole('checkbox', { name: 'ramp', exact: true }).check()
  await expect(page.getByTestId('event-events-page-status')).toHaveText('Page 1 of 1 · 1 event')

  await page.setViewportSize(otherViewport)
  await expectGroupedIds(otherPolicy)
  await expect(page.getByRole('region', { name: 'Selected event details' })).toHaveCount(0)
  await expect(page.getByTestId('event-events-page-status')).toHaveCount(0)
  await expect(page.getByRole('list', { name: 'Event list' })).toHaveCount(0)
  if (otherPolicy === 8) {
    await expect(categoryCard('ramp').getByText('1', { exact: true })).toBeVisible()
    await expect(categoryCard('ramp')).toContainText('Ramp started')
    await expect(categoryCard('control').getByText('0', { exact: true })).toBeVisible()
  } else {
    // Merged six-bucket card shows ONLY the surviving raw ramp filter.
    await expect(categoryCard('control').getByText('1', { exact: true })).toBeVisible()
    await expect(categoryCard('control')).toContainText('Ramp started')
  }

  await page.setViewportSize(initialViewport)
  await expectGroupedIds(initialPolicy)
  await expect(page.getByRole('region', { name: 'Selected event details' })).toHaveCount(0)
  if (initialPolicy === 8) {
    await expect(categoryCard('ramp').getByText('1', { exact: true })).toBeVisible()
  } else {
    await expect(categoryCard('control')).toContainText('Ramp started')
  }

  // Raw Ramp and Mutation filters select only the original backend
  // categories; clearing returns the full 11-event fixture and Alerts view.
  // Grouped overview has no page-status row, so switch to the flat list first:
  // this also re-proves the raw filter survived both resizes.
  if (!(await page.getByRole('button', { name: 'All events' }).isVisible())) {
    await page.getByRole('button', { name: /^Filters/ }).click()
  }
  await page.getByRole('button', { name: 'All events' }).click()
  await expect(page.getByTestId('event-events-page-status')).toHaveText('Page 1 of 1 · 1 event')
  const clearAll = page.getByRole('button', { name: /Clear all filters/ })
  if (await clearAll.isVisible()) await clearAll.click()
  if (!(await page.getByRole('checkbox', { name: 'mutation', exact: true }).isVisible())) {
    await page.getByRole('button', { name: /^Filters/ }).click()
  }
  await page.getByRole('checkbox', { name: 'mutation', exact: true }).check()
  await expect(page.getByTestId('event-events-page-status')).toHaveText('Page 1 of 1 · 1 event')
  await expect(page.getByText('Configuration updated')).toBeVisible()
  await page.getByRole('checkbox', { name: 'mutation', exact: true }).uncheck()
  await page.getByRole('checkbox', { name: 'ramp', exact: true }).check()
  await expect(page.getByTestId('event-events-page-status')).toHaveText('Page 1 of 1 · 1 event')
  await expect(page.getByText('Ramp started')).toBeVisible()
  await page.getByRole('checkbox', { name: 'ramp', exact: true }).uncheck()
  await expect(page.getByTestId('event-events-page-status')).toHaveText('Page 1 of 3 · 11 events')
  await page.getByRole('button', { name: 'Alerts' }).click()
  await expectGroupedIds(initialPolicy)
  await expect(categoryCard('relay').getByText('3', { exact: true })).toBeVisible()
  await expect(categoryCard('relay')).toContainText('Relay command failed')

  // The viewport-fit proof runs with the disclosure closed: an opened filter
  // disclosure may legitimately grow the document per the layout rules.
  const filtersTrigger = page.getByRole('button', { name: 'Filters' })
  if ((await filtersTrigger.getAttribute('aria-expanded')) === 'true') {
    await filtersTrigger.click()
  }
  await expectViewportFit()
  await page.screenshot({ path: `${testInfo.outputDir}/grouped-console-final.png`, fullPage: true })

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
  const policy: EventBucketCount = page.viewportSize()!.height >= 1440 ? 8 : 6

  await page.goto(fixtureUrl('/', testInfo, undefined, 'grouped-console'))
  await page.getByTestId('event-group-control').click()

  const list = page.getByRole('list', { name: 'Event list' })
  await expect(list).toBeVisible()
  const items = list.getByRole('listitem')

  if (policy === 6) {
    // Merged Control bucket: the ramp event stays on top of the three
    // setpoint rows.
    await expect(items).toHaveCount(4)
    await expect(items.first()).toContainText('Ramp started')
    await expect(items.nth(1)).toContainText('44.2% → 43.8%')
    await expect(items.nth(2)).toContainText('44.4% → 44.2%')
    await expect(items.nth(3)).toContainText('44.6% → 44.4%')
  } else {
    // Eight-bucket Control contains only the three setpoint rows; the raw
    // ramp category must still be reachable through its own card.
    await expect(items).toHaveCount(3)
    await expect(items.nth(0)).toContainText('44.2% → 43.8%')
    await expect(items.nth(1)).toContainText('44.4% → 44.2%')
    await expect(items.nth(2)).toContainText('44.6% → 44.4%')
    await expect(list.getByText('Ramp started')).toHaveCount(0)

    await page.getByTestId('event-group-collapse').click()
    await expect(page.getByTestId('event-group-control')).toBeVisible()
    await expect(page.getByTestId('event-group-ramp')).toBeVisible()
    await page.getByTestId('event-group-ramp').click()
    const rampItems = list.getByRole('listitem')
    await expect(rampItems).toHaveCount(1)
    await expect(rampItems.first()).toContainText('Ramp started')
    await page.getByTestId('event-group-collapse').click()
    await expect(page.getByTestId('event-group-control')).toBeVisible()
  }

  // The collapsed state is one control away.
  if (policy === 6) {
    await page.getByTestId('event-group-collapse').click()
  }
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
