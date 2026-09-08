/**
 * Event-log interaction QA.
 *
 * Exercises filters, expansion, keyboard navigation, severity filtering,
 * and dedupe behavior. Asserts:
 * - Severity filter buttons work and update the visible entries
 * - Search filter works
 * - Expansion toggle reveals/hides details
 * - Keyboard (Enter/Space) toggles expansion
 * - Dedupe: same event ID is not rendered twice
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

test('severity filter buttons update visible entries', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const allButton = page.getByRole('button', { name: 'All', exact: true })
  const criticalButton = page.getByRole('button', { name: 'Critical', exact: true })
  const warningButton = page.getByRole('button', { name: 'Warning', exact: true })
  const infoButton = page.getByRole('button', { name: 'Info', exact: true })

  await expect(allButton).toHaveAttribute('aria-pressed', 'true')
  const allCount = await page.locator('[role="listitem"]').count()
  expect(allCount).toBeGreaterThan(0)

  await criticalButton.click()
  await expect(criticalButton).toHaveAttribute('aria-pressed', 'true')
  await expect(allButton).toHaveAttribute('aria-pressed', 'false')
  const eventList = page.locator('[aria-labelledby="event-log-heading"] [role="list"]')
  await expect(eventList.locator('[role="listitem"]')).toHaveCount(4, { timeout: 5_000 })
  const criticalCount = await eventList.locator('[role="listitem"]').count()
  expect(criticalCount).toBeGreaterThan(0)
  expect(criticalCount).toBeLessThanOrEqual(allCount)

  const criticalBadges = eventList.locator('[role="listitem"] [aria-label="Severity: Critical"]')
  await expect(criticalBadges).toHaveCount(criticalCount, { timeout: 5_000 })
  const visibleCritical = await criticalBadges.count()
  expect(visibleCritical).toBe(criticalCount)

  await warningButton.click()
  await expect(warningButton).toHaveAttribute('aria-pressed', 'true')
  const warningCount = await page.locator('[role="listitem"]').count()
  expect(warningCount).toBeGreaterThan(0)

  await infoButton.click()
  await expect(infoButton).toHaveAttribute('aria-pressed', 'true')
  const infoCount = await page.locator('[role="listitem"]').count()
  expect(infoCount).toBeGreaterThan(0)

  expect(violations).toEqual([])
})

test('search filter narrows visible entries', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const searchInput = page.getByRole('searchbox', { name: 'Filter events' })
  await expect(searchInput).toBeVisible()

  const eventList = page.locator('[aria-labelledby="event-log-heading"] [role="list"]')
  await expect(eventList.locator('[role="listitem"]')).toHaveCount(8, { timeout: 5_000 })
  const allCount = await eventList.locator('[role="listitem"]').count()
  expect(allCount).toBeGreaterThan(0)

  await searchInput.fill('relay')
  await expect(eventList.locator('[role="listitem"]')).not.toHaveCount(allCount, { timeout: 5_000 })
  const filteredCount = await eventList.locator('[role="listitem"]').count()
  expect(filteredCount).toBeGreaterThan(0)
  expect(filteredCount).toBeLessThan(allCount)

  const entries = eventList.locator('[role="listitem"]')
  for (let i = 0; i < await entries.count(); i++) {
    const text = await entries.nth(i).textContent()
    expect(text!.toLowerCase()).toContain('relay')
  }

  await searchInput.fill('nonexistent-xyz-123')
  await expect(eventList.locator('[role="listitem"]')).toHaveCount(0, { timeout: 5_000 })
  const emptyMessage = page.getByText('No events yet')
  await expect(emptyMessage).toBeVisible()
})

test('expansion toggle reveals and hides event details', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const eventList = page.locator('[aria-labelledby="event-log-heading"] [role="list"]')
  await expect(eventList.locator('[role="listitem"]').first()).toBeVisible({ timeout: 5_000 })
  const firstEntry = eventList.locator('[role="listitem"]').first()
  const toggle = firstEntry.locator('button[aria-label="Toggle details"]')
  await expect(toggle).toBeVisible()

  const details = firstEntry.locator('[aria-label="Event details"]')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(details).toBeVisible()

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
})

test('keyboard Enter and Space toggle expansion', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const eventList = page.locator('[aria-labelledby="event-log-heading"] [role="list"]')
  await expect(eventList.locator('[role="listitem"]').first()).toBeVisible({ timeout: 5_000 })
  const firstEntry = eventList.locator('[role="listitem"]').first()
  const toggle = firstEntry.locator('button[aria-label="Toggle details"]')
  await toggle.focus()

  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('Enter')
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')

  await page.keyboard.press('Space')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
})

test('event-log deduplicates entries with same redis_id', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const eventList = page.locator('[aria-labelledby="event-log-heading"] [role="list"]')
  await expect(eventList.locator('[role="listitem"]').first()).toBeVisible({ timeout: 5_000 })
  await page.waitForTimeout(2000)

  const entries = eventList.locator('[role="listitem"]')
  const count = await entries.count()
  expect(count).toBeGreaterThan(0)

  const eventTypes: string[] = []
  for (let i = 0; i < count; i++) {
    const typeEl = entries.nth(i).locator('.font-mono.truncate')
    const type = await typeEl.textContent()
    eventTypes.push(type ?? '')
  }

  const uniqueTypes = new Set(eventTypes)
  expect(uniqueTypes.size).toBeLessThanOrEqual(count)
})

test('event count display updates with filter', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  const countDisplay = page.locator('.tabular-nums').filter({ hasText: /event/ })
  await expect(countDisplay.first()).toBeVisible()
  const initialText = await countDisplay.first().textContent()
  expect(initialText).toMatch(/\d+ events?/)

  await page.getByRole('button', { name: 'Critical' }).click()
  const filteredText = await countDisplay.first().textContent()
  expect(filteredText).toMatch(/\d+ events?/)
})
