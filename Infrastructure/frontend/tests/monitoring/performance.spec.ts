import { expect, test } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (request) => {
    const url = request.url()
    if (url.includes('/grafana/')) violations.push(`grafana: ${url}`)
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

test('keeps steady live mode bounded for sixty seconds without historical reloads', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  const violations = trackViolations(page)
  let sensorRangeRequests = 0
  let controlRangeRequests = 0
  let tailInFlight = 0
  let maxTailInFlight = 0

  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/sensors/monitoring/range/')) sensorRangeRequests += 1
    const isControlRange =
      url.pathname.startsWith('/api/monitoring/control/') &&
      !url.pathname.endsWith('/tail') &&
      !url.pathname.endsWith('/projection')
    if (isControlRange) controlRangeRequests += 1
    if (url.pathname.endsWith('/tail')) {
      tailInFlight += 1
      maxTailInFlight = Math.max(maxTailInFlight, tailInFlight)
    }
  })
  const completeTail = (request: import('@playwright/test').Request): void => {
    if (new URL(request.url()).pathname.endsWith('/tail')) tailInFlight -= 1
  }
  page.on('requestfinished', completeTail)
  page.on('requestfailed', completeTail)

  await page.goto(fixtureUrl('/vegetation/monitoring?scenario=large-x', testInfo))
  await expect(page.getByRole('table', { name: 'Sensor Values' })).toBeVisible()
  await page.waitForTimeout(2_000)
  const initialSensorRangeRequests = sensorRangeRequests
  const initialControlRangeRequests = controlRangeRequests

  await page.waitForTimeout(60_000)

  expect(sensorRangeRequests).toBe(initialSensorRangeRequests)
  expect(controlRangeRequests).toBe(initialControlRangeRequests)
  expect(maxTailInFlight).toBe(1)
  expect(tailInFlight).toBe(0)
  expect(violations).toEqual([])
})

test('performs one bounded control reconciliation after flush health recovers', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  let controlRangeRequests = 0
  let tailInFlight = 0
  let maxTailInFlight = 0

  page.on('request', (request) => {
    const url = new URL(request.url())
    const isControlRange =
      url.pathname.startsWith('/api/monitoring/control/') &&
      !url.pathname.endsWith('/tail') &&
      !url.pathname.endsWith('/projection')
    if (isControlRange) controlRangeRequests += 1
    if (url.pathname.endsWith('/tail')) {
      tailInFlight += 1
      maxTailInFlight = Math.max(maxTailInFlight, tailInFlight)
    }
  })
  const completeTail = (request: import('@playwright/test').Request): void => {
    if (new URL(request.url()).pathname.endsWith('/tail')) tailInFlight -= 1
  }
  page.on('requestfinished', completeTail)
  page.on('requestfailed', completeTail)

  await page.goto(fixtureUrl('/flower/monitoring?scenario=delayed-control-recovery', testInfo))
  await expect(page.getByRole('table', { name: 'Back Cluster' })).toBeVisible()
  await page.waitForTimeout(500)
  const initialControlRanges = controlRangeRequests

  await page.waitForTimeout(3_000)

  expect(controlRangeRequests - initialControlRanges).toBe(1)
  expect(maxTailInFlight).toBe(1)
  expect(tailInFlight).toBe(0)
  expect(violations).toEqual([])
})
