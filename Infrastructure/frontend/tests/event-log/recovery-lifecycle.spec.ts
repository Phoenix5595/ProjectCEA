import { test, expect } from '@playwright/test'
import { ALL_EVENTS, FLOWER_EVENTS } from '../../src/features/event-log/config/fixtures'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from '../monitoring/fixtureUrl'

function historyResponse(items: ReadonlyArray<(typeof ALL_EVENTS)[number]>, hasMore: boolean) {
  return {
    items,
    newest_cursor: items.at(0)?.redis_id ?? null,
    oldest_cursor: items.at(-1)?.redis_id ?? null,
    earliest_cursor: items.at(-1)?.redis_id ?? null,
    has_more: hasMore,
    scan: { scanned: items.length, limit: 200 },
  }
}

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (request) => {
    const violation = describeViolation(request.url())
    if (violation !== null) violations.push(`${violation}: ${request.url()}`)
  })
  return violations
}

test('Load older requests the fixture cursor and disappears at exhaustion', async ({ page }, testInfo) => {
  const initialItems = ALL_EVENTS.slice(0, 2)
  const olderItems = ALL_EVENTS.slice(2, 4)
  const historyUrls: string[] = []
  await page.route('**/api/events/history**', async (route) => {
    const url = route.request().url()
    historyUrls.push(url)
    const before = new URL(url).searchParams.get('before')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(before === null ? historyResponse(initialItems, true) : historyResponse(olderItems, false)),
    })
  })
  await page.route('**/api/events/stream**', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }))

  await page.goto(fixtureUrl('/flower', testInfo))
  const section = page.getByRole('region', { name: 'Event Log' })
  await expect(section.getByRole('button', { name: 'Load older' })).toBeVisible()
  await section.getByRole('button', { name: 'Load older' }).click()
  await expect(section.getByRole('button', { name: 'Load older' })).toBeHidden()
  expect(new URL(historyUrls[1] ?? '').searchParams.get('before')).toBe(initialItems.at(-1)?.redis_id)
})

test('409 clears fixture history before one replacement bootstrap and tail', async ({ page }, testInfo) => {
  const staleItem = FLOWER_EVENTS[0]
  const latestItem = FLOWER_EVENTS[1]
  let historyCalls = 0
  let streamCalls = 0
  await page.route('**/api/events/history**', async (route) => {
    historyCalls += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(historyResponse(historyCalls === 1 ? [staleItem] : [latestItem], false)),
    })
  })
  await page.route('**/api/events/stream**', async (route) => {
    streamCalls += 1
    if (streamCalls === 1) {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ earliest_cursor: latestItem.redis_id, latest_cursor: latestItem.redis_id }),
      })
      return
    }
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
  })

  await page.goto(fixtureUrl('/flower', testInfo))
  const eventList = page.locator('[aria-labelledby="event-log-heading"] [role="list"]')
  await expect(eventList.getByText('Sensor degraded')).toBeVisible()
  await expect(eventList.getByText('Relay state changed')).toHaveCount(0)
  expect(historyCalls).toBe(2)
  expect(streamCalls).toBe(2)
})

test('Dashboard teardown/remount and room navigation remain fixture-origin only', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  const eventRequests: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/api/events/')) eventRequests.push(request.url())
  })

  await page.goto(fixtureUrl('/', testInfo, 'dashboard'))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible()
  await expect.poll(() => eventRequests.filter((url) => url.includes('/api/events/stream')).length).toBe(1)
  await page.goto(fixtureUrl('/flower', testInfo, 'flower'))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible()
  await expect.poll(() => eventRequests.filter((url) => url.includes('/api/events/stream')).length).toBe(2)
  await page.goto(fixtureUrl('/vegetation', testInfo, 'vegetation'))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible()
  await expect.poll(() => eventRequests.filter((url) => url.includes('/api/events/stream')).length).toBe(3)

  expect(eventRequests.filter((url) => url.includes('/api/events/history')).length).toBeGreaterThanOrEqual(3)
  expect(eventRequests.filter((url) => url.includes('/api/events/stream')).length).toBeGreaterThanOrEqual(3)
  expect(violations).toEqual([])
})
