import { expect, test } from '@playwright/test'
import path from 'node:path'
import { mkdirSync, writeFileSync } from 'node:fs'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import type { MonitoringPerfDebug } from '../../src/features/monitoring/perfMarks'
import { fixtureUrl } from './fixtureUrl'

const EVIDENCE_DIR = path.resolve(process.cwd(), '../../.omo/evidence/projected-climate-timeline/browser/live-update')
const FLOWER_BACK = 'Back Cluster'
const DRY_BULB_VALUES = ['24.6°C', '24.7°C', '24.8°C', '24.9°C', '25.0°C', '25.1°C'] as const

type ChartTelemetry = {
  readonly title: string
  readonly instanceId: number
  readonly xScaleMin: number | null
  readonly xScaleMax: number | null
  readonly destroyCount: number
}

declare global {
  interface Window {
    readonly __monitoringPerf?: MonitoringPerfDebug
  }
}

function telemetry(page: import('@playwright/test').Page): Promise<readonly ChartTelemetry[]> {
  return page.evaluate(() => window.__monitoringPerf?.charts ?? [])
}

test('renders five live value and viewport updates, handles stale pause/resume, and tears down', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const requests: string[] = []
  const violations: string[] = []
  page.on('request', (request) => {
    const url = request.url()
    requests.push(url)
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })

  await page.goto(fixtureUrl('/flower/monitoring', testInfo, 'live', 'live-update'))
  const backTable = page.getByRole('table', { name: FLOWER_BACK })
  const observedValues: string[] = []
  const observedScales: Array<{ readonly min: number; readonly max: number }> = []

  for (const value of DRY_BULB_VALUES) {
    await expect(backTable.getByText(value, { exact: true })).toBeVisible({ timeout: 10_000 })
    observedValues.push(value)
    await expect.poll(async () => {
      const charts = await telemetry(page)
      const chart = charts.find((candidate) => candidate.title === 'Flower climate conditions')
      return chart?.xScaleMin ?? null
    }, { timeout: 10_000 }).not.toBeNull()
    const charts = await telemetry(page)
    const chart = charts.find((candidate) => candidate.title === 'Flower climate conditions')
    if (chart?.xScaleMin !== null && chart?.xScaleMin !== undefined && chart.xScaleMax !== null && chart.xScaleMax !== undefined) {
      observedScales.push({ min: chart.xScaleMin, max: chart.xScaleMax })
    }
  }

  expect(observedValues).toEqual([...DRY_BULB_VALUES])
  expect(observedScales).toHaveLength(6)
  expect(new Set(observedScales.map((scale) => scale.min)).size).toBeGreaterThanOrEqual(5)

  await expect(backTable.getByLabel('Dry Bulb stale')).toBeVisible({ timeout: 10_000 })
  const pausedValue = await backTable.locator('tbody tr').filter({ hasText: 'Dry Bulb' }).locator('td').nth(1).textContent()
  const pausedCharts = await telemetry(page)
  const liveRequestsBeforePause = requests.filter((url) => url.includes('/api/sensors/monitoring/live/')).length
  await page.getByRole('button', { name: 'Pause' }).first().click()
  await expect(page.getByRole('button', { name: 'Resume' }).first()).toBeVisible()
  await page.waitForTimeout(2_000)
  expect(requests.filter((url) => url.includes('/api/sensors/monitoring/live/')).length).toBe(liveRequestsBeforePause)
  expect(await backTable.locator('tbody tr').filter({ hasText: 'Dry Bulb' }).locator('td').nth(1).textContent()).toBe(pausedValue)
  expect(await telemetry(page)).toEqual(pausedCharts)

  await page.getByRole('button', { name: 'Resume' }).first().click()
  await expect(backTable.getByText('25.4°C', { exact: true })).toBeVisible({ timeout: 10_000 })

  const beforeTeardown = await telemetry(page)
  expect(beforeTeardown).toHaveLength(2)
  await page.goto('http://127.0.0.1:4173/')
  await expect.poll(() => telemetry(page)).toEqual([])
  expect(violations).toEqual([])

  mkdirSync(EVIDENCE_DIR, { recursive: true })
  writeFileSync(path.join(EVIDENCE_DIR, 'live-update-proof.json'), JSON.stringify({
    origin: 'http://127.0.0.1:4173',
    values: observedValues,
    scales: observedScales,
    staleObserved: true,
    pausedValue,
    pausedCharts,
    resumedValue: '25.4°C',
    chartsBeforeTeardown: beforeTeardown,
    chartsAfterTeardown: [],
    liveRequestsBeforePause,
    violations,
  }, null, 2))
})
