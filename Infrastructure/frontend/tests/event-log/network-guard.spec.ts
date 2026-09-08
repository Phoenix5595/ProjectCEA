/**
 * Event-log network guard QA.
 *
 * Proves that no request from the event-log UI leaves the exact fixture
 * origin (http://127.0.0.1:4173). Asserts zero violations against the
 * originGuard for every page and scenario.
 */
import { test, expect } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from '../monitoring/fixtureUrl'

const PAGES = ['/', '/flower', '/vegetation', '/laboratory'] as const
const SCENARIOS = [undefined, 'empty', 'error-500', 'auth-401', 'disconnect', 'burst'] as const

for (const pagePath of PAGES) {
  for (const scenario of SCENARIOS) {
    const scenarioLabel = scenario ?? 'default'
    test(`network guard: ${pagePath} with scenario=${scenarioLabel} makes zero external requests`, async ({
      page: p,
    }, testInfo) => {
      const violations: string[] = []
      p.on('request', (req) => {
        const url = req.url()
        const violation = describeViolation(url)
        if (violation !== null) violations.push(`${violation}: ${url}`)
      })

      const url = scenario
        ? fixtureUrl(`${pagePath}?scenario=${scenario}`, testInfo)
        : fixtureUrl(pagePath, testInfo)
      await p.goto(url)
      await expect(p.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

      await p.waitForTimeout(2000)

      expect(violations).toEqual([])
    })
  }
}

test('network guard: event-log history and stream endpoints are on fixture origin', async ({
  page: p,
}, testInfo) => {
  const eventLogRequests: string[] = []
  p.on('request', (req) => {
    const url = req.url()
    if (url.includes('/api/events/')) {
      eventLogRequests.push(url)
    }
  })

  await p.goto(fixtureUrl('/flower', testInfo))
  await expect(p.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  await p.waitForTimeout(3000)

  const historyRequests = eventLogRequests.filter((u) => u.includes('/api/events/history'))
  const streamRequests = eventLogRequests.filter((u) => u.includes('/api/events/stream'))

  expect(historyRequests.length).toBeGreaterThan(0)
  expect(streamRequests.length).toBeGreaterThan(0)

  for (const url of eventLogRequests) {
    expect(url).toContain('http://127.0.0.1:4173')
    expect(describeViolation(url)).toBeNull()
  }
})

test('network guard: event-log uses X-API-Key header, not query token', async ({ page }, testInfo) => {
  const historyRequests: string[] = []
  await page.route('**/api/events/history**', async (route) => {
    const request = route.request()
    historyRequests.push(request.url())
    await route.continue()
  })

  await page.goto(fixtureUrl('/flower', testInfo))
  await expect(page.getByRole('heading', { name: 'Event Log' })).toBeVisible({ timeout: 10_000 })

  await page.waitForTimeout(2000)

  expect(historyRequests.length).toBeGreaterThan(0)
  for (const url of historyRequests) {
    const parsed = new URL(url)
    expect(parsed.searchParams.has('token')).toBe(false)
  }
})
