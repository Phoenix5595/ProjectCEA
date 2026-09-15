/**
 * Event-log viewport and accessibility QA.
 *
 * Exercises the event-log UI on Dashboard and the three overview pages
 * (Flower, Vegetation, Laboratory) at 375/768/1280 px. Asserts:
 * - The event-log section renders with entries
 * - axe-core reports zero serious/critical violations
 * - No request leaves the exact fixture origin
 * - Severity badges are visible and non-color-dependent
 * - No text clipping at any viewport width
 */
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import type { AxeResults } from 'axe-core'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from '../monitoring/fixtureUrl'

const WIDTHS = [375, 768, 1280] as const

const PAGES = [
  { path: '/', name: 'Dashboard', heading: 'Event Log' },
  { path: '/flower', name: 'Flower', heading: 'Event Log' },
  { path: '/vegetation', name: 'Vegetation', heading: 'Event Log' },
  { path: '/laboratory', name: 'Laboratory', heading: 'Event Log' },
] as const

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (req) => {
    const url = req.url()
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

function seriousCritical(results: AxeResults) {
  return results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
}

for (const pageDef of PAGES) {
  for (const width of WIDTHS) {
    test(`event-log renders on ${pageDef.name} at ${width}px with zero serious/critical axe violations`, async ({
      page: p,
    }, testInfo) => {
      await p.setViewportSize({ width, height: 900 })
      const violations = trackViolations(p)
      await p.goto(fixtureUrl(pageDef.path, testInfo))

      const eventLogSection = p.getByRole('region', { name: 'Event Log' })
      await expect(eventLogSection).toBeVisible({ timeout: 10_000 })

      const heading = p.getByRole('heading', { name: 'Event Log' })
      await expect(heading).toBeVisible()

      const severityBadges = p.locator('[aria-label^="Severity:"]')
      await expect(severityBadges.first()).toBeVisible({ timeout: 5_000 })

      const results = await new AxeBuilder({ page: p })
        .include('[aria-labelledby="event-log-heading"]')
        .analyze()
      const bad = seriousCritical(results)
      if (bad.length > 0) {
        console.log('Axe violations:', JSON.stringify(bad.map((v) => ({
          id: v.id,
          impact: v.impact,
          nodes: v.nodes.map((n) => ({
            html: n.html,
            target: n.target,
            failureSummary: n.failureSummary,
          })),
        })), null, 2))
      }
      expect(
        bad.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
        `axe violations on ${pageDef.name} at ${width}px`,
      ).toEqual([])

      expect(violations).toEqual([])
    })
  }
}

test('severity badges have non-color text treatment', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  await expect(page.locator('[aria-label^="Severity:"]').first()).toBeVisible({ timeout: 5_000 })
  const badges = page.locator('[aria-label^="Severity:"]')
  const count = await badges.count()
  expect(count).toBeGreaterThan(0)

  for (let i = 0; i < Math.min(count, 5); i++) {
    const badge = badges.nth(i)
    const text = await badge.textContent()
    expect(text).toMatch(/Critical|Warning|Info/)
    const ariaLabel = await badge.getAttribute('aria-label')
    expect(ariaLabel).toMatch(/Severity: (Critical|Warning|Info)/)
  }
})

test('event-label fixture exposes readable labels, exact severity, and keyboard details', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)
  const requestUrls: string[] = []
  page.on('request', (request) => requestUrls.push(request.url()))
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await page.route('**/api/events/stream**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'event: heartbeat\ndata: \n\n' }),
  )

  await page.goto(fixtureUrl('/flower', testInfo, undefined, 'event-labels'))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const eventList = page.locator('[aria-labelledby="event-log-heading"] [role="list"]')
  const entries = eventList.locator('[role="listitem"]')
  await expect(entries).toHaveCount(3, { timeout: 5_000 })

  await expect(page.getByText('Relay command failed', { exact: true })).toBeVisible()
  await expect(page.getByText('Relay state changed', { exact: true })).toBeVisible()
  await expect(page.getByText('Custom unknown type xyz', { exact: true })).toBeVisible()
  await expect(eventList.getByText('relay.command_failed', { exact: true })).toBeVisible()
  await expect(eventList.getByText('relay.state_changed', { exact: true })).toBeVisible()
  await expect(eventList.getByText('custom.unknown_type_xyz', { exact: true })).toBeVisible()
  await expect(page.getByText('Unknown event type', { exact: true })).toHaveCount(0)

  await expect(eventList.locator('[aria-label="Severity: Error"]')).toHaveCount(1)
  await expect(eventList.locator('[aria-label="Severity: Critical"]')).toHaveCount(1)
  await expect(eventList.locator('[aria-label="Severity: Info"]')).toHaveCount(1)

  await page.getByRole('button', { name: 'Critical', exact: true }).click()
  await expect(entries).toHaveCount(1)
  await expect(entries.first()).toContainText('Custom unknown type xyz')
  await expect(entries.first().locator('[aria-label="Severity: Critical"]')).toHaveCount(1)

  await page.getByRole('button', { name: 'All', exact: true }).click()
  await page.getByRole('button', { name: 'Error', exact: true }).click()
  await expect(entries).toHaveCount(1)
  await expect(entries.first()).toContainText('Relay command failed')
  await expect(entries.first()).toContainText('relay.command_failed')
  await expect(entries.first().locator('[aria-label="Severity: Error"]')).toHaveCount(1)
  await expect(page.getByText('Relay state changed', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Custom unknown type xyz', { exact: true })).toHaveCount(0)

  await page.getByRole('button', { name: 'All', exact: true }).click()
  const failedEntry = entries.filter({ hasText: 'relay.command_failed' })
  const toggle = failedEntry.getByRole('button', { name: 'Toggle details' })
  await toggle.focus()
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('Enter')
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(failedEntry.getByRole('region', { name: 'Event details' })).toContainText('exhaust-fan')
  await page.keyboard.press('Space')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')

  const evidenceDirectory = path.resolve(process.cwd(), '../../.omo/evidence/control-monitoring-correctness/T8')
  await mkdir(evidenceDirectory, { recursive: true })
  await page.screenshot({ path: path.join(evidenceDirectory, 'event-log.png'), fullPage: true })
  await writeFile(
    path.join(evidenceDirectory, 'network.json'),
    JSON.stringify({
      fixture_origin: 'http://127.0.0.1:4173',
      requests: requestUrls,
      violations,
    }, null, 2),
  )

  expect(consoleErrors).toEqual([])
  expect(violations).toEqual([])
})

test('event-log entries do not clip text at 375px', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 900 })
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const entries = page.locator('[role="listitem"]')
  const count = await entries.count()
  expect(count).toBeGreaterThan(0)

  for (let i = 0; i < Math.min(count, 3); i++) {
    const entry = entries.nth(i)
    const box = await entry.boundingBox()
    expect(box).not.toBeNull()
    if (box) {
      expect(box.width).toBeLessThanOrEqual(375)
      expect(box.height).toBeGreaterThan(0)
    }
    const text = await entry.textContent()
    expect(text).toBeTruthy()
    expect(text!.length).toBeGreaterThan(0)
  }
})

test('event-log renders with CJK payload without glyph drop', async ({ page, context }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })

  const cjkEvent = {
    redis_id: `${Date.now()}-100`,
    event: {
      schema_version: 1,
      event_id: `cjk-${Date.now()}`,
      occurred_at: new Date().toISOString(),
      source: 'system',
      category: 'system',
      severity: 'info',
      event_type: 'system.test_cjk',
      correlation_id: null,
      causation_id: null,
      entity: { entity_type: 'system', entity_id: 'test', location: 'Flower Room', cluster: 'main' },
      actor: { actor_type: 'system', actor_id: null },
      reason_code: null,
      reason_text: null,
      payload: {
        room: 'Flower Room',
        cluster: 'main',
        device_id: '시스템 테스트',
      },
    },
  }

  await context.route('**/api/events/history**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [cjkEvent],
        newest_cursor: cjkEvent.redis_id,
        oldest_cursor: cjkEvent.redis_id,
        earliest_cursor: cjkEvent.redis_id,
        has_more: false,
        scan: { scanned: 1, limit: 500 },
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

  const eventList = page.locator('[aria-labelledby="event-log-heading"] [role="list"]')
  await expect(eventList.locator('[role="listitem"]').first()).toBeVisible({ timeout: 5_000 })

  const details = page.locator('[aria-label="Event details"]')
  const firstDetails = details.first()
  const toggle = firstDetails.locator('..').locator('button[aria-label="Toggle details"]')
  await toggle.click()
  await expect(firstDetails).toBeVisible()

  const koreanText = page.getByText('시스템 테스트')
  await expect(koreanText).toBeVisible()
})

test('unknown event type uses deterministic humanized label', async ({ page, context }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })

  const unknownEvent = {
    redis_id: `${Date.now()}-99`,
    event: {
      schema_version: 1,
      event_id: `unknown-${Date.now()}`,
      occurred_at: new Date().toISOString(),
      source: 'system',
      category: 'system',
      severity: 'info',
      event_type: 'custom.unknown_type_xyz',
      correlation_id: null,
      causation_id: null,
      entity: { entity_type: 'system', entity_id: 'test', location: 'Flower Room', cluster: 'main' },
      actor: { actor_type: 'system', actor_id: null },
      reason_code: null,
      reason_text: null,
      payload: { room: 'Flower Room', cluster: 'main', custom_field: 'test' },
    },
  }

  await context.route('**/api/events/history**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [unknownEvent],
        newest_cursor: unknownEvent.redis_id,
        oldest_cursor: unknownEvent.redis_id,
        earliest_cursor: unknownEvent.redis_id,
        has_more: false,
        scan: { scanned: 1, limit: 500 },
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

  const unknownLabel = page.getByText('Custom unknown type xyz', { exact: true })
  await expect(unknownLabel).toBeVisible()
  await expect(page.getByText('Unknown event type', { exact: true })).toHaveCount(0)
})
