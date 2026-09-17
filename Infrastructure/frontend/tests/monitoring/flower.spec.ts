/**
 * Flower monitoring page browser coverage.
 *
 * Runs against the fixture preview on exactly `http://127.0.0.1:4173`. Asserts
 * the native page renders the toolbar, both chart regions, and the canonical
 * tables at the two configured desktop viewports, and that no request is made to `/grafana/*` or to
 * any external production endpoint (loopback ports 8000/8001/8003/8080 or the
 * Grafana host). The partial-failure scenario (`MONITORING_SCENARIO=flower-partial`)
 * verifies Back data and recorded history survive when Front and projection are
 * unavailable.
 */
import { test, expect } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (req) => {
    const url = req.url()
    if (url.includes('/grafana/')) violations.push(`grafana: ${url}`)
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

async function temperatureScale(page: import('@playwright/test').Page): Promise<{
  readonly observedMin: number
  readonly observedMax: number
  readonly scaleMin: number
  readonly scaleMax: number
} | null> {
  return page.evaluate(() => {
    const isObject = (value: unknown): value is object =>
      typeof value === 'object' && value !== null
    const get = (value: object, key: PropertyKey): unknown => Reflect.get(value, key)
    const frame = document.querySelector('.mon-chart__frame')
    if (frame === null) return null

    const fiberKey = Object.keys(frame).find(key => key.startsWith('__reactFiber'))
    let fiber: unknown = fiberKey === undefined ? null : get(frame, fiberKey)
    while (isObject(fiber) && get(fiber, 'tag') !== 11) fiber = get(fiber, 'return')
    if (!isObject(fiber)) return null

    let plot: object | null = null
    let hook: unknown = get(fiber, 'memoizedState')
    while (isObject(hook)) {
      const hookValue = get(hook, 'memoizedState')
      if (isObject(hookValue)) {
        const current = get(hookValue, 'current')
        if (isObject(current) && isObject(get(current, 'scales'))) {
          plot = current
          break
        }
      }
      hook = get(hook, 'next')
    }
    if (plot === null) return null

    const scales = get(plot, 'scales')
    const temperature = isObject(scales) ? get(scales, 'temperature') : null
    const scaleMin = isObject(temperature) ? get(temperature, 'min') : null
    const scaleMax = isObject(temperature) ? get(temperature, 'max') : null
    const series = get(plot, 'series')
    const data = get(plot, 'data')
    if (!isObject(temperature) || typeof scaleMin !== 'number' || typeof scaleMax !== 'number')
      return null
    if (!Array.isArray(series) || !Array.isArray(data)) return null

    const values: number[] = []
    series.forEach((entry, index) => {
      if (!isObject(entry) || index === 0 || get(entry, 'scale') !== 'temperature') return
      const points = data[index]
      if (!Array.isArray(points)) return
      points.forEach(value => {
        if (typeof value === 'number' && Number.isFinite(value)) values.push(value)
      })
    })
    if (values.length === 0) return null

    return {
      observedMin: values.reduce((min, value) => Math.min(min, value), Infinity),
      observedMax: values.reduce((max, value) => Math.max(max, value), -Infinity),
      scaleMin,
      scaleMax,
    }
  })
}

test('flower monitoring renders natively', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/monitoring', testInfo))

  await expect(page.getByRole('button', { name: 'Reset Zoom' })).toBeVisible()
  await page.getByRole('button', { name: 'Reset Zoom' }).click()
  await expect(
    page.getByRole('heading', { name: 'Flower climate conditions' }),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Flower atmosphere & equipment' })).toBeVisible()

  await expect(page.getByRole('table', { name: 'Averages' })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Front Cluster' })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Back Cluster' })).toBeVisible()
  await expect(
    page.getByRole('table', { name: 'Statistics - All Available Sensors' }),
  ).toBeVisible()

  expect(violations).toEqual([])
})

test('renders temperature headroom around extreme fixture values', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/monitoring', testInfo, undefined, 'extreme-y'))
  await expect(page.getByRole('heading', { name: 'Flower climate conditions' })).toBeVisible()
  await expect
    .poll(async () => {
      const result = await temperatureScale(page)
      return result !== null && result.observedMin < 10 && result.observedMax > 35
    })
    .toBe(true)

  const result = await temperatureScale(page)
  expect(result).not.toBeNull()
  if (result === null) throw new Error('temperature uPlot scale was not mounted')

  expect(result.observedMin).toBeLessThan(10)
  expect(result.observedMax).toBeGreaterThan(35)
  expect(result.scaleMin).toBeLessThan(result.observedMin)
  expect(result.scaleMax).toBeGreaterThan(result.observedMax)
  expect(violations).toEqual([])
})

test('keeps Back and history when Front/projection fail', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/monitoring', testInfo))

  const backTable = page.getByRole('table', { name: 'Back Cluster' })
  await expect(backTable).toBeVisible()
  await expect(backTable.getByText('Dry Bulb')).toBeVisible()
  await expect(backTable.getByText('24.6°C')).toBeVisible({ timeout: 15000 })

  await expect(
    page.getByRole('heading', { name: 'Flower climate conditions' }),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Flower atmosphere & equipment' })).toBeVisible()

  expect(violations).toEqual([])
})
