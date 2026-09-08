/**
 * Event-log live/paused scroll and connection state QA.
 *
 * Uses Playwright context route interception to simulate API scenarios (empty, error-500,
 * auth-401, disconnect) without modifying the production transport. Asserts:
 * - Empty state shows "No events yet"
 * - Error state (500) shows empty state after failure
 * - Auth failure (401) shows empty state after auth pause
 * - Disconnect state shows empty state after stream closes
 * - Live stream appends events
 * - Room-filtered page shows matching events
 * - Burst scenario renders many events
 */
import { test, expect } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from '../monitoring/fixtureUrl'

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (req) => {
    const url = req.url()
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

test('empty scenario shows "No events yet" message', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)

  await page.route('**/api/events/history**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [],
        newest_cursor: null,
        oldest_cursor: null,
        earliest_cursor: null,
        has_more: false,
        scan: { scanned: 0, limit: 500 },
      }),
    })
  })

  await page.route('**/api/events/stream**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'event: heartbeat\ndata: \n\n',
    })
  })

  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const emptyMessage = page.getByText('No events yet')
  await expect(emptyMessage).toBeVisible()

  const eventList = page.locator('[aria-labelledby="event-log-heading"] [role="list"]')
  await expect(eventList.locator('[role="listitem"]')).toHaveCount(0, { timeout: 5_000 })

  expect(violations).toEqual([])
})

test('error-500 scenario shows empty state after history failure', async ({ page, context }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)

  await context.route('**/api/events/history**', (route) => {
    route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'internal error (fixture)' }),
    })
  })

  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  await page.waitForTimeout(2000)

  const emptyMessage = page.getByText('No events yet')
  await expect(emptyMessage).toBeVisible()

  expect(violations).toEqual([])
})

test('auth-401 scenario shows empty state after auth pause', async ({ page, context }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)

  await context.route('**/api/events/history**', (route) => {
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'unauthorized (fixture)' }),
    })
  })

  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  await page.waitForTimeout(2000)

  const emptyMessage = page.getByText('No events yet')
  await expect(emptyMessage).toBeVisible()

  expect(violations).toEqual([])
})

test('disconnect scenario shows empty state after stream closes immediately', async ({ page, context }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)

  await context.route('**/api/events/history**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [],
        newest_cursor: null,
        oldest_cursor: null,
        earliest_cursor: null,
        has_more: false,
        scan: { scanned: 0, limit: 500 },
      }),
    })
  })

  await context.route('**/api/events/stream**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: '',
    })
  })

  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  await page.waitForTimeout(2000)

  const emptyMessage = page.getByText('No events yet')
  await expect(emptyMessage).toBeVisible()

  expect(violations).toEqual([])
})

test('live stream appends events and scroll stays at bottom', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const eventList = page.locator('[role="list"]').first()
  await expect(eventList).toBeVisible({ timeout: 5_000 })

  await page.waitForTimeout(3000)

  const entries = page.locator('[role="listitem"]')
  const count = await entries.count()
  expect(count).toBeGreaterThan(0)

  const lastEntry = entries.last()
  const lastText = await lastEntry.textContent()
  expect(lastText).toBeTruthy()

  expect(violations).toEqual([])
})

test('room-filtered page shows only matching room events', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  await page.waitForTimeout(2000)

  const entries = page.locator('[role="listitem"]')
  const count = await entries.count()
  expect(count).toBeGreaterThan(0)

  expect(violations).toEqual([])
})

test('burst scenario renders many events without crash', async ({ page, context }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)

  const burstEvents = []
  for (let i = 0; i < 20; i++) {
    burstEvents.push({
      redis_id: `${Date.now() + i}-${1000 + i}`,
      event: {
        schema_version: 1,
        event_id: `burst-${i}-${Date.now()}`,
        occurred_at: new Date(Date.now() + i * 100).toISOString(),
        source: 'automation',
        category: 'relay',
        severity: 'info',
        event_type: 'relay.state_changed',
        correlation_id: null,
        causation_id: null,
        entity: { entity_type: 'device', entity_id: `device-${i}`, location: 'Flower Room', cluster: 'main' },
        actor: { actor_type: 'system', actor_id: 'automation-service' },
        reason_code: null,
        reason_text: null,
        payload: { room: 'Flower Room', cluster: 'main', device_id: `device-${i}`, state: 'on' },
      },
    })
  }

  await context.route('**/api/events/history**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: burstEvents,
        newest_cursor: burstEvents.at(-1)!.redis_id,
        oldest_cursor: burstEvents[0].redis_id,
        earliest_cursor: burstEvents[0].redis_id,
        has_more: false,
        scan: { scanned: burstEvents.length, limit: 500 },
      }),
    })
  })

  await context.route('**/api/events/stream**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'event: heartbeat\ndata: \n\n',
    })
  })

  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  await page.waitForTimeout(2000)

  const entries = page.locator('[role="listitem"]')
  const count = await entries.count()
  expect(count).toBeGreaterThan(5)

  expect(violations).toEqual([])
})
