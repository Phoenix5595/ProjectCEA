import { expect, test } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

const FIXTURE_ORIGIN = 'http://127.0.0.1:4173'
const EVIDENCE_DIR = path.resolve(process.cwd(), '../../.omo/evidence/grafana-chart-lifecycle-parity/T6')
const CHART_TITLES = ['Flower climate conditions', 'Flower atmosphere & equipment'] as const
const RANGE_DURATION_MS = 3_600_000

type ChartTelemetry = {
  readonly title: string
  readonly instanceId: number
  readonly width: number
  readonly height: number
  readonly xScaleMin: number | null
  readonly xScaleMax: number | null
  readonly viewportRevision: number
  readonly destroyCount: number
  readonly resizeCount: number
}

type LedgerEntry = {
  readonly url: string
  readonly kind: 'sensor-range' | 'control-history' | 'projection'
}

function telemetry(page: import('@playwright/test').Page): Promise<readonly ChartTelemetry[]> {
  return page.evaluate(() => window.__monitoringPerf?.charts ?? [])
}

function relevantRequest(url: string): LedgerEntry | null {
  const pathname = new URL(url).pathname
  if (pathname.startsWith('/api/sensors/monitoring/range/')) return { url, kind: 'sensor-range' }
  if (pathname.endsWith('/history')) return { url, kind: 'control-history' }
  if (pathname.endsWith('/projection')) return { url, kind: 'projection' }
  return null
}

function chartByTitle(charts: readonly ChartTelemetry[], title: string): ChartTelemetry {
  const chart = charts.find((candidate) => candidate.title === title)
  expect(chart, `missing telemetry for ${title}`).toBeDefined()
  if (chart === undefined) throw new Error(`missing telemetry for ${title}`)
  return chart
}

function expectPopulated(charts: readonly ChartTelemetry[]): void {
  expect(charts).toHaveLength(CHART_TITLES.length)
  for (const title of CHART_TITLES) {
    const chart = chartByTitle(charts, title)
    expect(chart.width).toBeGreaterThan(0)
    expect(chart.height).toBeGreaterThan(0)
    expect(chart.xScaleMin).not.toBeNull()
    expect(chart.xScaleMax).not.toBeNull()
  }
}

function expectStableInstances(before: readonly ChartTelemetry[], after: readonly ChartTelemetry[]): void {
  for (const title of CHART_TITLES) {
    expect(chartByTitle(after, title).instanceId).toBe(chartByTitle(before, title).instanceId)
  }
}

function recordEvidence(
  testInfo: import('@playwright/test').TestInfo,
  ledger: readonly LedgerEntry[],
  charts: readonly ChartTelemetry[],
): void {
  mkdirSync(EVIDENCE_DIR, { recursive: true })
  const suffix = testInfo.project.name.replaceAll(/[^a-z0-9]+/gi, '-')
  writeFileSync(path.join(EVIDENCE_DIR, `request-ledger-${suffix}.json`), JSON.stringify(ledger, null, 2))
  writeFileSync(path.join(EVIDENCE_DIR, `telemetry-${suffix}.json`), JSON.stringify(charts, null, 2))
}

function trackRequests(page: import('@playwright/test').Page): { readonly ledger: LedgerEntry[]; readonly violations: string[] } {
  const ledger: LedgerEntry[] = []
  const violations: string[] = []
  page.on('request', (request) => {
    const url = request.url()
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
    const entry = relevantRequest(url)
    if (entry !== null) ledger.push(entry)
  })
  return { ledger, violations }
}

function latestSensorBounds(ledger: readonly LedgerEntry[], duration: number): { readonly start: number; readonly end: number } | null {
  const sensorRequest = [...ledger].reverse().find((entry) => entry.kind === 'sensor-range')
  if (sensorRequest === undefined) return null
  const searchParams = new URL(sensorRequest.url).searchParams
  const start = Date.parse(searchParams.get('start') ?? '')
  const end = Date.parse(searchParams.get('end') ?? '')
  return end - start === duration ? { start, end } : null
}

async function waitForCharts(page: import('@playwright/test').Page): Promise<readonly ChartTelemetry[]> {
  await expect.poll(async () => {
    const charts = await telemetry(page)
    return charts.length === CHART_TITLES.length && charts.every((chart) => (
      chart.width > 0 && chart.height > 0 && chart.xScaleMin !== null && chart.xScaleMax !== null
    ))
  }).toBe(true)
  return telemetry(page)
}

async function waitForRangeFulfillment(
  page: import('@playwright/test').Page,
  ledger: readonly LedgerEntry[],
  duration: number,
): Promise<void> {
  await expect.poll(async () => {
    const bounds = latestSensorBounds(ledger, duration)
    if (bounds === null) return false
    const charts = await telemetry(page)
    return charts.length === CHART_TITLES.length && CHART_TITLES.every(
      (title) => chartByTitle(charts, title).xScaleMin === bounds.start,
    )
  }).toBe(true)
}

test('retains Flower chart instances, scale, and fulfilled data through resize transitions', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1090, height: 800 })
  const { ledger, violations } = trackRequests(page)

  await page.goto(fixtureUrl('/flower/monitoring', testInfo, 'resize'))
  await waitForRangeFulfillment(page, ledger, 3 * RANGE_DURATION_MS)
  await expect(page.getByRole('status').first()).toContainText('3h')
  const initial = await waitForCharts(page)
  await page.getByRole('button', { name: 'Pause' }).first().click()
  const requestsBeforeResize = ledger.length

  await page.setViewportSize({ width: 1280, height: 800 })
  const firstResize = await waitForCharts(page)
  expectStableInstances(initial, firstResize)
  expectPopulated(firstResize)
  expect(ledger).toHaveLength(requestsBeforeResize)

  await page.setViewportSize({ width: 640, height: 800 })
  await page.setViewportSize({ width: 1280, height: 800 })
  const secondResize = await waitForCharts(page)
  expectStableInstances(initial, secondResize)
  expectPopulated(secondResize)
  expect(ledger).toHaveLength(requestsBeforeResize)
  expect(violations).toEqual([])

  recordEvidence(testInfo, ledger, secondResize)
  await page.screenshot({ path: path.join(EVIDENCE_DIR, `resize-final-${testInfo.project.name}.png`), fullPage: true })
})

test('retains the fulfilled 3h viewport until delayed 1h history settles at the widest budget', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1090, height: 800 })
  const { ledger, violations } = trackRequests(page)

  await page.goto(fixtureUrl('/flower/monitoring', testInfo, 'range', 'delayed-range'))
  await waitForRangeFulfillment(page, ledger, 3 * RANGE_DURATION_MS)
  await expect(page.getByRole('status').first()).toContainText('3h')
  const fulfilledThreeHours = await waitForCharts(page)
  await page.getByRole('button', { name: 'Pause' }).first().click()
  const requestsBeforeRange = ledger.length
  const widestWidth = Math.max(...fulfilledThreeHours.map((chart) => chart.width))

  await page.getByRole('button', { name: '1h' }).click()
  await expect.poll(() => ledger.length - requestsBeforeRange, { timeout: 10_000 }).toBe(3)
  const rangeRequests = ledger.slice(requestsBeforeRange)
  expect(rangeRequests.map((entry) => entry.kind).sort()).toEqual([
    'control-history',
    'projection',
    'sensor-range',
  ])

  const pending = await telemetry(page)
  expectStableInstances(fulfilledThreeHours, pending)
  for (const title of CHART_TITLES) {
    const previous = chartByTitle(fulfilledThreeHours, title)
    const current = chartByTitle(pending, title)
    expect(current.viewportRevision).toBe(previous.viewportRevision)
    expect(current.xScaleMin).toBe(previous.xScaleMin)
    expect(current.xScaleMax).toBe(previous.xScaleMax)
  }

  await expect.poll(async () => {
    const charts = await telemetry(page)
    return CHART_TITLES.every((title) => {
      const chart = chartByTitle(charts, title)
      const prev = chartByTitle(fulfilledThreeHours, title)
      return chart.viewportRevision === prev.viewportRevision + 1
        && chart.xScaleMin !== prev.xScaleMin
        && chart.xScaleMin !== null
    })
  }, { timeout: 10_000 }).toBe(true)
  const fulfilledOneHour = await telemetry(page)
  recordEvidence(testInfo, ledger, fulfilledOneHour)
  expect(ledger.every((entry) => new URL(entry.url).origin === FIXTURE_ORIGIN)).toBe(true)
  expectStableInstances(fulfilledThreeHours, fulfilledOneHour)
  for (const title of CHART_TITLES) {
    expect(chartByTitle(fulfilledOneHour, title).viewportRevision).toBe(
      chartByTitle(fulfilledThreeHours, title).viewportRevision + 1,
    )
  }

  const oneHourRangeRequests = rangeRequests.filter((entry) => {
    if (entry.kind === 'projection') return true
    const url = new URL(entry.url)
    const s = Date.parse(url.searchParams.get('start') ?? '')
    const e = Date.parse(url.searchParams.get('end') ?? '')
    return e - s === RANGE_DURATION_MS
  })
  const sensorRequest = oneHourRangeRequests.find((entry) => entry.kind === 'sensor-range')
  const controlRequest = oneHourRangeRequests.find((entry) => entry.kind === 'control-history')
  expect(sensorRequest).toBeDefined()
  expect(controlRequest).toBeDefined()
  if (sensorRequest === undefined || controlRequest === undefined) throw new Error('missing range request')
  const sensorUrl = new URL(sensorRequest.url)
  const controlUrl = new URL(controlRequest.url)
  const start = Date.parse(sensorUrl.searchParams.get('start') ?? '')
  const end = Date.parse(sensorUrl.searchParams.get('end') ?? '')
  expect(end - start).toBe(RANGE_DURATION_MS)
  expect(controlUrl.searchParams.get('start')).toBe(sensorUrl.searchParams.get('start'))
  expect(controlUrl.searchParams.get('end')).toBe(sensorUrl.searchParams.get('end'))
  const expectedBudget = Math.min(50_000, Math.max(10, Math.ceil(widestWidth)))
  expect(Number(sensorUrl.searchParams.get('max_points'))).toBe(expectedBudget)
  expect(Number(controlUrl.searchParams.get('max_points'))).toBe(expectedBudget)
  for (const title of CHART_TITLES) {
    const chart = chartByTitle(fulfilledOneHour, title)
    expect(chart.xScaleMin).toBe(start)
    expect(chart.xScaleMax).toBeLessThanOrEqual(end + RANGE_DURATION_MS / 9)
  }
  expect(violations).toEqual([])
  expect(ledger.every((entry) => new URL(entry.url).origin === FIXTURE_ORIGIN)).toBe(true)

  await page.screenshot({ path: path.join(EVIDENCE_DIR, `range-final-${testInfo.project.name}.png`), fullPage: true })
})
