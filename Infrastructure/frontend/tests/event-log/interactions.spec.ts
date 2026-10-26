/**
 * Event-log interaction QA.
 *
 * Exercises expansion and keyboard navigation. Asserts:
 * - Expansion toggle reveals/hides details
 * - Keyboard (Enter/Space) toggles expansion
 */
import { test, expect } from '@playwright/test'
import { fixtureUrl } from '../monitoring/fixtureUrl'

test('expansion toggle reveals and hides event details', async ({ page }, testInfo) => {
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
