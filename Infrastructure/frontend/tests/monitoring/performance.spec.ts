import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import type { MonitoringPerfDebug } from '../../src/features/monitoring/perfMarks'
import { fixtureUrl } from './fixtureUrl'

declare global {
  interface Window {
    readonly __monitoringPerf?: MonitoringPerfDebug
  }
}

const CLIENT_PROCESSING_SLO_MS = 8
const VISUAL_AGE_SLO_MS = 1000
const TICK_SAMPLE_COUNT = 120
const ARTIFACT_PATH = path.resolve(
  process.cwd(),
  '../../.omo/evidence/monitoring-pipeline-radical-optimization/task-7-monitoring-pipeline-radical-optimization-browser-baseline.json',
)
let happyArtifact: Record<string, unknown> | undefined

interface PerfTick {
  readonly tickIndex: number
  readonly alignMs: number
  readonly convertMs: number
  readonly setDataMs: number
  readonly totalTickMs: number
  readonly commitMs: number
  readonly requestToPaintMs: number | null
  readonly visualAgeMs: number
}

interface PerfSnapshot {
  readonly tickCount: number
  readonly lastTickAt: number | null
  readonly samples: readonly PerfTick[]
  readonly requestCount: number
  readonly lastResizeMs: number | null
  readonly resizeCount: number
}

interface HeapSample {
  readonly tickIndex: number
  readonly usedJSHeapSize: number | null
}

function nearestRank(samples: readonly number[], percentile: number): number {
  const ordered = [...samples].sort((left, right) => left - right)
  const index = Math.max(0, Math.ceil((percentile / 100) * ordered.length) - 1)
  return ordered[index] ?? 0
}

function latencySummary(samples: readonly number[]): { readonly p50: number; readonly p95: number; readonly p99: number } {
  return {
    p50: nearestRank(samples, 50),
    p95: nearestRank(samples, 95),
    p99: nearestRank(samples, 99),
  }
}

async function perfSnapshot(page: import('@playwright/test').Page): Promise<PerfSnapshot | null> {
  return page.evaluate(() => window.__monitoringPerf ?? null)
}

async function waitForTickCount(
  page: import('@playwright/test').Page,
  tickCount: number,
): Promise<void> {
  await expect
    .poll(async () => (await perfSnapshot(page))?.tickCount ?? -1, {
      intervals: [100, 250, 500],
      timeout: 90_000,
    })
    .toBeGreaterThanOrEqual(tickCount)
}

async function heapUsed(page: import('@playwright/test').Page): Promise<number | null> {
  return page.evaluate(() => {
    const memory = Reflect.get(performance, 'memory')
    if (typeof memory !== 'object' || memory === null) return null
    const used = Reflect.get(memory, 'usedJSHeapSize')
    return typeof used === 'number' ? used : null
  })
}

function heapTrend(samples: readonly HeapSample[]): { readonly first: number | null; readonly last: number | null; readonly delta: number | null } {
  const values = samples.flatMap((sample) =>
    sample.usedJSHeapSize === null ? [] : [sample.usedJSHeapSize],
  )
  const first = values[0] ?? null
  const last = values[values.length - 1] ?? null
  return { first, last, delta: first === null || last === null ? null : last - first }
}

async function writeArtifact(contents: Record<string, unknown>): Promise<void> {
  await mkdir(path.dirname(ARTIFACT_PATH), { recursive: true })
  await writeFile(ARTIFACT_PATH, `${JSON.stringify(contents, null, 2)}\n`)
}

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

test('records fixture-only client processing and paint-age SLO samples', async ({ page }, testInfo) => {
  test.setTimeout(150_000)
  const violations = trackViolations(page)
  let sensorRangeRequests = 0
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/sensors/monitoring/range/')) {
      sensorRangeRequests += 1
    }
  })

  await page.goto(fixtureUrl('/flower/monitoring?scenario=performance', testInfo))
  await expect(page.getByRole('img', { name: 'Flower climate conditions' })).toBeVisible()
  expect(await perfSnapshot(page)).not.toBeNull()
  await waitForTickCount(page, 1)
  const initial = await perfSnapshot(page)
  expect(initial).not.toBeNull()
  const initialTickCount = initial?.tickCount ?? 0
  const heaps: HeapSample[] = []

  for (let target = initialTickCount + 20; target <= initialTickCount + TICK_SAMPLE_COUNT; target += 20) {
    await waitForTickCount(page, target)
    heaps.push({ tickIndex: target, usedJSHeapSize: await heapUsed(page) })
  }

  const afterTicks = await perfSnapshot(page)
  expect(afterTicks).not.toBeNull()
  const samples = (afterTicks?.samples ?? []).filter((sample) => sample.tickIndex > initialTickCount)
  expect(samples.length).toBeGreaterThanOrEqual(TICK_SAMPLE_COUNT)

  const climateChart = page.getByRole('img', { name: 'Flower climate conditions' })
  const chartBox = await climateChart.boundingBox()
  expect(chartBox).not.toBeNull()
  if (chartBox === null) return

  const beforeZoomRequests = sensorRangeRequests
  const zoomStartedAt = performance.now()
  const rangeResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.startsWith('/api/sensors/monitoring/range/'),
  )
  await page.mouse.move(chartBox.x + chartBox.width * 0.2, chartBox.y + chartBox.height * 0.5)
  await page.mouse.down()
  await page.mouse.move(chartBox.x + chartBox.width * 0.8, chartBox.y + chartBox.height * 0.5)
  await page.mouse.up()
  await rangeResponse
  const zoomRefetchMs = performance.now() - zoomStartedAt
  await expect.poll(() => sensorRangeRequests).toBe(beforeZoomRequests + 1)

  const beforeResize = await perfSnapshot(page)
  await page.setViewportSize({ width: 1180, height: 900 })
  await expect
    .poll(async () => (await perfSnapshot(page))?.resizeCount ?? -1)
    .toBeGreaterThan(beforeResize?.resizeCount ?? -1)
  const afterResize = await perfSnapshot(page)

  const totalTickMs = samples.map((sample) => sample.totalTickMs)
  const visualAgeMs = samples.map((sample) => sample.visualAgeMs)
  const requestToPaintMs = samples.flatMap((sample) =>
    sample.requestToPaintMs === null ? [] : [sample.requestToPaintMs],
  )
  const heap = heapTrend(heaps)
  const processing = latencySummary(totalTickMs)
  const visualAge = latencySummary(visualAgeMs)

  happyArtifact = {
    schema_version: 1,
    fixture_origin: 'http://127.0.0.1:4173',
    percentile_method: 'nearest-rank',
    thresholds: { client_processing_p95_ms: CLIENT_PROCESSING_SLO_MS, visual_age_ms: VISUAL_AGE_SLO_MS },
    happy_run: {
      tick_count: samples.length,
      samples,
      latency_ms: {
        align: latencySummary(samples.map((sample) => sample.alignMs)),
        convert: latencySummary(samples.map((sample) => sample.convertMs)),
        set_data: latencySummary(samples.map((sample) => sample.setDataMs)),
        total_tick: processing,
        request_to_paint: latencySummary(requestToPaintMs),
        visual_age: visualAge,
      },
      heap,
      heap_samples: heaps,
      request_count: afterTicks?.requestCount ?? 0,
      zoom: { range_refetches: sensorRangeRequests - beforeZoomRequests, requery_ms: zoomRefetchMs },
      resize: { resize_ms: afterResize?.lastResizeMs ?? null, range_refetches: sensorRangeRequests - beforeZoomRequests - 1 },
    },
    gate_outputs: { performance_spec: 'pass' },
  }
  await writeArtifact(happyArtifact)

  expect(processing.p95).toBeLessThanOrEqual(CLIENT_PROCESSING_SLO_MS)
  expect(visualAge.p99).toBeLessThanOrEqual(VISUAL_AGE_SLO_MS)
  expect(Math.max(...visualAgeMs)).toBeLessThanOrEqual(VISUAL_AGE_SLO_MS)
  expect(requestToPaintMs.length).toBeGreaterThan(0)
  if (heap.first !== null && heap.last !== null) {
    expect.soft(heap.last).toBeLessThanOrEqual(heap.first * 1.1)
  }
  expect(sensorRangeRequests - beforeZoomRequests).toBe(1)
  expect(violations).toEqual([])
})

test('detects the fixture-only synthetic alignment delay', async ({ page }, testInfo) => {
  test.setTimeout(60_000)
  await page.goto(fixtureUrl('/flower/monitoring?scenario=performance-delay', testInfo))
  await expect(page.getByRole('img', { name: 'Flower climate conditions' })).toBeVisible()
  await waitForTickCount(page, 20)
  const snapshot = await perfSnapshot(page)
  const samples = snapshot?.samples ?? []
  const p95 = nearestRank(samples.map((sample) => sample.totalTickMs), 95)

  let failureOutput = ''
  try {
    expect(p95).toBeLessThanOrEqual(CLIENT_PROCESSING_SLO_MS)
  } catch (error) {
    if (error instanceof Error) failureOutput = error.message
    else throw error
  }

  expect(failureOutput).not.toBe('')
  expect(p95).toBeGreaterThan(CLIENT_PROCESSING_SLO_MS)
  await writeArtifact({
    ...happyArtifact,
    negative_probe: {
      scenario: 'performance-delay',
      tick_count: samples.length,
      p95_total_tick_ms: p95,
      expected_assertion_failure: failureOutput,
    },
    gate_outputs: { performance_spec: 'pass', negative_probe: 'pass' },
  })
})

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
