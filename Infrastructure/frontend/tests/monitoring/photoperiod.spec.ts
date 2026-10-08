/**
 * Historical photoperiod browser proof (guarded fixture preview).
 *
 * Exercises the plan's six browser-proof cases on the real monitoring pages
 * with the exact `historical-photoperiod` fixture scenario (browser clock
 * T = 2026-08-20T12:00Z):
 *
 * 1. the fixed range `[T-28h, T-16h)` is applied through the toolbar's
 *    Toronto wall-time fields and the URL/x scale settle to it;
 * 2. both chart canvases paint SUN as yellow and MOON as blue/purple with the
 *    UNKNOWN gap unpainted, band boundaries verified against the live uPlot
 *    x scale/bbox (never against source constants), and screenshots captured;
 * 3. a real drag zoom across the absolute SUN→MOON transition keeps the same
 *    transition at the same historical time and never leaks fills into axes;
 * 4. returning to a live 12h preset ends the historical MOON band at Now
 *    where the independent projected SUN begins, without recoloring the past;
 * 5. reloading under all six ThemeContext themes (range re-applied through the
 *    toolbar Apply) keeps the overlay RGBA identical on actual canvas pixels
 *    of BOTH charts while the surrounding theme and geometry checks prove the
 *    theme genuinely changed without moving the page;
 * 6. zero origin violations and unchanged chart/page geometry throughout.
 *
 * The guarded fixture origin is the only permitted request origin
 * (`describeViolation`); no production, external, or Grafana traffic occurs.
 */
import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'

import { THEME_NAMES } from '../../src/contexts/ThemeContext'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

const T = Date.parse('2026-08-20T12:00:00.000Z')
const HOUR_MS = 3_600_000
/** Absolute scenario instant, in hours from T (negative = historical). */
const at = (hours: number) => T + hours * HOUR_MS

const FIXED_RANGE = { start: at(-28), end: at(-16) }
/** Fixed-range `[T-28h, T-16h)` renders `SUN | MOON | gap | SUN | MOON` (the
 * T-27h anchor is in-range): probes cover the left-edge SUN carry, both MOON
 * runs, the UNKNOWN gap, the merged SUN run, and the right-edge MOON. */
const FIXED_PROBES = [
  at(-27.9), at(-27.4), at(-26), at(-23.5), at(-22), at(-17), at(-16.1),
]
const FIXED_PROBE = {
  sunEdge: 0, sunCarry: 1, moonCarry: 2, gapMid: 3, sunRun: 4, moonMid: 5, moonEdge: 6,
} as const

/** Zoom subrange `[T-20h, T-16.4h)` crossing the absolute T-18h SUN→MOON
 * transition (`SUN | MOON` to the window end). */
const ZOOM_PROBES = [at(-19.9), at(-18.5), at(-17), at(-16.5)]

/** Live 12h window `[T-12h, T+80min)` probes: historical MOON, historical
 * SUN, just before/after Now, projected MOON, expired span. */
const LIVE_PROBES = [at(-10), at(-8), at(-1 / 12), at(1 / 12), at(2 / 3), at(7 / 6)]
const LIVE_PROBE = { histMoon: 0, histSun: 1, beforeNow: 2, afterNow: 3, projMoon: 4, expired: 5 } as const

interface FixtureRoom {
  readonly room: 'Flower Room' | 'Veg Room'
  readonly path: '/flower/monitoring' | '/vegetation/monitoring'
  readonly climate: string
  readonly equipment: string
}

const ROOMS: readonly FixtureRoom[] = [
  {
    room: 'Flower Room',
    path: '/flower/monitoring',
    climate: 'Flower climate conditions',
    equipment: 'Flower atmosphere & equipment',
  },
  {
    room: 'Veg Room',
    path: '/vegetation/monitoring',
    climate: 'Veg climate conditions',
    equipment: 'Veg atmosphere & equipment',
  },
]

interface FrameRect {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

interface BandProbe {
  readonly t: number
  readonly x: number
  readonly sun: number
  readonly moon: number
}

interface DragPoint {
  readonly t: number
  readonly x: number
  readonly y: number
}

interface PlotMeasure {
  readonly scaleMin: number
  readonly scaleMax: number
  readonly bboxLeft: number
  readonly bboxTop: number
  readonly bboxWidth: number
  readonly bboxHeight: number
  readonly canvasWidth: number
  readonly canvasHeight: number
  readonly frameRect: FrameRect
  readonly sunInside: number
  readonly moonInside: number
  readonly sunOutside: number
  readonly moonOutside: number
  readonly sunColor: readonly number[]
  readonly moonColor: readonly number[]
  readonly columns: string
  readonly probes: readonly BandProbe[]
  readonly points: readonly DragPoint[]
}

interface CanvasFill {
  x: number
  y: number
  width: number
  height: number
  style: string | CanvasGradient | CanvasPattern
}

type CapturedCanvas = HTMLCanvasElement & { __phaseFills?: CanvasFill[] }

async function captureBandDrawing(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const fill = CanvasRenderingContext2D.prototype.fillRect
    const clear = CanvasRenderingContext2D.prototype.clearRect
    CanvasRenderingContext2D.prototype.clearRect = function (x, y, width, height) {
      ;(this.canvas as CapturedCanvas).__phaseFills = []
      return clear.call(this, x, y, width, height)
    }
    CanvasRenderingContext2D.prototype.fillRect = function (x, y, width, height) {
      const canvas = this.canvas as CapturedCanvas
      const draws = canvas.__phaseFills ?? (canvas.__phaseFills = [])
      draws.push({ x, y, width, height, style: this.fillStyle })
      return fill.call(this, x, y, width, height)
    }
  })
}

function trackViolations(page: Page): string[] {
  const violations: string[] = []
  page.on('request', request => {
    const url = request.url()
    if (url.includes('/grafana/')) violations.push(`grafana: ${url}`)
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

/**
 * Measure one chart's live uPlot state and its actual canvas pixels.
 *
 * The uPlot instance is reached through the frame's React fiber exactly like
 * `equipment.spec.ts` does. Band pixels are classified against the expected
 * composited overlay color computed on a same-context sample canvas, so the
 * proof is real canvas output. `pixels: false` skips the pixel pass for
 * cheap scale-settle polling.
 */
function measurePlot(
  section: Locator,
  request: { probes?: number[]; points?: number[]; pixels?: boolean } = {}
): Promise<PlotMeasure | null> {
  const frame = section.locator('.mon-chart__frame')
  return (async () => {
    if ((await frame.count()) === 0) return null
    try {
      return await frame.evaluate(scanPlotFrame, request)
    } catch {
      return null
    }
  })()
}

/** In-page scan: fiber walk to the plot, then one pass over canvas pixels. */
function scanPlotFrame(
  frame: Element,
  request: { probes?: number[]; points?: number[]; pixels?: boolean }
): PlotMeasure | null {
  const isObject = (value: unknown): value is object =>
    typeof value === 'object' && value !== null
  const get = (value: object, key: PropertyKey): unknown => Reflect.get(value, key)
  const rawRect = frame.getBoundingClientRect()
  const frameRect: FrameRect = { x: rawRect.x, y: rawRect.y, width: rawRect.width, height: rawRect.height }
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
      if (
        isObject(current) &&
        isObject(get(current, 'scales')) &&
        isObject(get(current, 'bbox')) &&
        get(current, 'ctx') instanceof CanvasRenderingContext2D
      ) {
        plot = current
        break
      }
    }
    hook = get(hook, 'next')
  }
  if (plot === null) return null

  const scales = get(plot, 'scales')
  const xScale = isObject(scales) ? get(scales, 'x') : null
  const scaleMin = isObject(xScale) ? get(xScale, 'min') : null
  const scaleMax = isObject(xScale) ? get(xScale, 'max') : null
  const bbox = get(plot, 'bbox')
  const valToPos = get(plot, 'valToPos')
  const ctx = get(plot, 'ctx')
  if (
    typeof scaleMin !== 'number' ||
    typeof scaleMax !== 'number' ||
    !isObject(bbox) ||
    typeof valToPos !== 'function' ||
    !(ctx instanceof CanvasRenderingContext2D)
  ) {
    return null
  }
  const left = get(bbox, 'left')
  const top = get(bbox, 'top')
  const width = get(bbox, 'width')
  const height = get(bbox, 'height')
  if (
    typeof left !== 'number' ||
    typeof top !== 'number' ||
    typeof width !== 'number' ||
    typeof height !== 'number'
  ) {
    return null
  }
  // Canvas-space mapping (uPlot bbox units): probes/boundary math must match
  // the plugin's own clipping coordinates (`valToPos(ms,'x',true)`).
  const toCanvasX = (ms: number): number =>
    (valToPos as (value: number, scale: string, crop?: boolean) => number).call(plot, ms, 'x', true)
  // Overlay-relative CSS mapping: only for trusted mouse interaction points.
  const toOverlayX = (ms: number): number =>
    (valToPos as (value: number, scale: string) => number).call(plot, ms, 'x')

  // Drag/hover coordinates use the live plot mapping plus the event overlay.
  const root = get(plot, 'root')
  const overlay = isObject(root) ? (root as HTMLElement).querySelector('.u-over') : null
  const overlayRect = overlay instanceof HTMLElement ? overlay.getBoundingClientRect() : null
  const points = (request.points ?? []).map(t => {
    if (overlayRect === null) return { t, x: NaN, y: NaN }
    return {
      t,
      x: overlayRect.left + toOverlayX(t),
      // 40px below the overlay top stays inside the plot and on screen.
      y: overlayRect.top + Math.min(40, overlayRect.height / 2),
    }
  })

  const head = {
    scaleMin,
    scaleMax,
    bboxLeft: left,
    bboxTop: top,
    bboxWidth: width,
    bboxHeight: height,
    canvasWidth: ctx.canvas.width,
    canvasHeight: ctx.canvas.height,
    frameRect,
    points,
  }
  if (request.pixels === false) {
    return {
      ...head,
      sunInside: 0,
      moonInside: 0,
      sunOutside: 0,
      moonOutside: 0,
      sunColor: [],
      moonColor: [],
      columns: '',
      probes: [],
    }
  }

  // Expected overlay pixels come from the browser's own compositing math on an
  // identical-context sample canvas (opaque canvases composite over black).
  // The fills are inlined here because this function runs inside the page.
  const SUN_FILL = 'rgba(251, 191, 36, 0.12)'
  const MOON_FILL = 'rgba(129, 140, 248, 0.12)'
  const image = ctx.getImageData(0, 0, ctx.canvas.width, ctx.canvas.height).data
  const transparentBackdrop = image[3] === 0
  const expectedPixel = (fill: string): [number, number, number, number] => {
    const sample = document.createElement('canvas')
    sample.width = 1
    sample.height = 1
    const sampleCtx = sample.getContext('2d', { alpha: transparentBackdrop })
    if (sampleCtx === null) return [-1, -1, -1, -1]
    sampleCtx.fillStyle = fill
    sampleCtx.fillRect(0, 0, 1, 1)
    const data = sampleCtx.getImageData(0, 0, 1, 1).data
    return [data[0]!, data[1]!, data[2]!, data[3]!]
  }
  const sunColor = expectedPixel(SUN_FILL)
  const moonColor = expectedPixel(MOON_FILL)

  const xStart = Math.max(0, Math.floor(left))
  const xEnd = Math.min(ctx.canvas.width, Math.ceil(left + width))
  const yStart = Math.max(0, Math.floor(top))
  const yEnd = Math.min(ctx.canvas.height, Math.ceil(top + height))
  const colSun = new Int32Array(xEnd - xStart)
  const colMoon = new Int32Array(xEnd - xStart)
  let sunInside = 0
  let moonInside = 0
  // Similar-colored antialiased axis glyphs are not band leakage. Check the
  // actual fillRect operations, while pixels prove the visible phase/colors.
  const draws = (ctx.canvas as CapturedCanvas).__phaseFills
  if (draws === undefined) return null
  const outOfBounds = draws.filter(draw =>
    draw.x < left - 0.001 || draw.x + draw.width > left + width + 0.001 ||
    draw.y < top - 0.001 || draw.y + draw.height > top + height + 0.001
  )
  const sunOutside = outOfBounds.filter(draw => draw.style === SUN_FILL).length
  const moonOutside = outOfBounds.filter(draw => draw.style === MOON_FILL).length
  for (let y = yStart; y < yEnd; y += 1) {
    for (let x = xStart; x < xEnd; x += 1) {
      const offset = (y * ctx.canvas.width + x) * 4
      const r = image[offset]!
      const g = image[offset + 1]!
      const b = image[offset + 2]!
      const a = image[offset + 3]!
      const sun =
        Math.abs(r - sunColor[0]!) <= 6 &&
        Math.abs(g - sunColor[1]!) <= 6 &&
        Math.abs(b - sunColor[2]!) <= 6 &&
        Math.abs(a - sunColor[3]!) <= 6
      const moon =
        Math.abs(r - moonColor[0]!) <= 6 &&
        Math.abs(g - moonColor[1]!) <= 6 &&
        Math.abs(b - moonColor[2]!) <= 6 &&
        Math.abs(a - moonColor[3]!) <= 6
      if (!sun && !moon) continue
      const column = x - xStart
      if (sun) {
        colSun[column] = colSun[column]! + 1
        sunInside += 1
      } else {
        colMoon[column] = colMoon[column]! + 1
        moonInside += 1
      }
    }
  }

  const strong = Math.max(8, Math.floor(height * 0.2))
  let columns = ''
  for (let i = 0; i < colSun.length; i += 1) {
    columns += colSun[i]! >= strong ? 'S' : colMoon[i]! >= strong ? 'M' : '.'
  }
  const probes = (request.probes ?? []).map(t => {
    const x = toCanvasX(t)
    const idx = Math.round(x) - xStart
    // A vertical grid stroke can cover the entire exact-time column. Probe
    // neighboring pixels inside the same interval, not the grid/series stroke.
    let sun = 0
    let moon = 0
    for (let column = Math.max(0, idx - 2); column <= Math.min(colSun.length - 1, idx + 2); column += 1) {
      sun = Math.max(sun, colSun[column]!)
      moon = Math.max(moon, colMoon[column]!)
    }
    return {
      t,
      x,
      sun,
      moon,
    }
  })

  return {
    ...head,
    sunInside,
    moonInside,
    sunOutside,
    moonOutside,
    sunColor,
    moonColor,
    columns,
    probes,
  }
}

/** Plot-area x pixel of an absolute ms instant on the measured scale. */
function scalePxOf(scan: PlotMeasure, ms: number): number {
  return scan.bboxLeft + ((ms - scan.scaleMin) / (scan.scaleMax - scan.scaleMin)) * scan.bboxWidth
}

function expectBandColumn(scan: PlotMeasure, probeIndex: number, phase: 'SUN' | 'MOON'): void {
  const probe = scan.probes[probeIndex]
  expect(probe, `probe ${probeIndex} missing`).toBeDefined()
  if (probe === undefined) return
  const wanted = phase === 'SUN' ? probe.sun : probe.moon
  const other = phase === 'SUN' ? probe.moon : probe.sun
  expect(
    wanted,
    `${phase} band missing at ${new Date(probe.t).toISOString()} (x=${probe.x.toFixed(1)})`
  ).toBeGreaterThan(scan.bboxHeight * 0.25)
  expect(
    other,
    `opposite band leaked at ${new Date(probe.t).toISOString()} (x=${probe.x.toFixed(1)})`
  ).toBeLessThanOrEqual(2)
}

function expectGapColumn(scan: PlotMeasure, probeIndex: number): void {
  const probe = scan.probes[probeIndex]
  expect(probe, `probe ${probeIndex} missing`).toBeDefined()
  if (probe === undefined) return
  expect(
    probe.sun,
    `SUN band painted in the UNKNOWN gap at ${new Date(probe.t).toISOString()}`
  ).toBeLessThanOrEqual(2)
  expect(
    probe.moon,
    `MOON band painted in the UNKNOWN gap at ${new Date(probe.t).toISOString()}`
  ).toBeLessThanOrEqual(2)
}

function expectNoLeak(scan: PlotMeasure, label: string): void {
  expect(scan.sunOutside, `${label}: SUN fill leaked outside the plot bbox`).toBe(0)
  expect(scan.moonOutside, `${label}: MOON fill leaked outside the plot bbox`).toBe(0)
}

/** Rightmost fully-classified column boundary of a phase run. */
function lastColumnOf(scan: PlotMeasure, kind: 'S' | 'M'): number {
  const origin = Math.floor(scan.bboxLeft)
  for (let i = scan.columns.length - 1; i >= 0; i -= 1) {
    if (scan.columns[i] === kind) return origin + i + 1
  }
  return origin
}

/**
 * Locate the painted transition between two phase runs near `boundaryMs` and
 * assert the painted edge sits on the real x-scale position (±3px) and maps
 * back to the absolute boundary time. Returns the edge's absolute ms.
 */
function edgeMsAtBoundary(
  scan: PlotMeasure,
  from: 'S' | 'M' | '.',
  to: 'S' | 'M' | '.',
  boundaryMs: number
): number {
  const expectedPx = scalePxOf(scan, boundaryMs)
  const origin = Math.floor(scan.bboxLeft)
  const center = Math.round(expectedPx) - origin
  const span = 80
  let lastFrom = -1
  let firstTo = -1
  for (let i = Math.max(0, center - span); i <= Math.min(scan.columns.length - 1, center + span); i += 1) {
    if (i < center && scan.columns[i] === from) lastFrom = i
    if (i >= center && scan.columns[i] === to && firstTo === -1) firstTo = i
  }
  const boundaryLabel = new Date(boundaryMs).toISOString()
  expect(lastFrom, `no ${from} run left of ${boundaryLabel}`).toBeGreaterThan(-1)
  expect(firstTo, `no ${to} run right of ${boundaryLabel}`).toBeGreaterThan(-1)
  const edgePx = origin + (lastFrom + firstTo + 1) / 2
  const msPerPx = (scan.scaleMax - scan.scaleMin) / scan.bboxWidth
  const edgeMs = scan.scaleMin + ((edgePx - scan.bboxLeft) / scan.bboxWidth) * (scan.scaleMax - scan.scaleMin)
  expect(
    Math.abs(edgePx - expectedPx),
    `painted seam at ${boundaryLabel} is ${Math.abs(edgePx - expectedPx).toFixed(1)}px off the x scale`
  ).toBeLessThanOrEqual(3)
  expect(
    Math.abs(edgeMs - boundaryMs),
    `painted seam at ${boundaryLabel} drifted from its absolute time`
  ).toBeLessThanOrEqual(3 * msPerPx)
  return edgeMs
}

function urlRange(page: Page): { start: string | null; end: string | null } {
  const params = new URL(page.url()).searchParams
  return { start: params.get('start'), end: params.get('end') }
}

/** Drive the fixed range through the toolbar's Toronto wall-time fields and
 * Apply, then wait until the URL carries exactly that range. */
async function applyFixedRange(page: Page): Promise<void> {
  await page.getByLabel('Range start').fill('2026-08-19T04:00')
  await page.getByLabel('Range end').fill('2026-08-19T16:00')
  await page.getByRole('button', { name: 'Apply fixed range' }).click()
  await expect
    .poll(() => urlRange(page))
    .toEqual({
      start: new Date(FIXED_RANGE.start).toISOString(),
      end: new Date(FIXED_RANGE.end).toISOString(),
    })
}

async function scaleOf(section: Locator): Promise<{ min: number; max: number } | null> {
  const measure = await measurePlot(section, { pixels: false })
  return measure === null ? null : { min: measure.scaleMin, max: measure.scaleMax }
}

async function frameRectOf(section: Locator): Promise<FrameRect | null> {
  return section.locator('.mon-chart__frame').evaluate(element => {
    const rect = element.getBoundingClientRect()
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
  })
}

async function pageOverflow(page: Page): Promise<{ scroll: number; client: number }> {
  return page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
}

function expectRectStable(baseline: FrameRect, current: FrameRect | null, label: string): void {
  expect(current, `${label} frame missing`).not.toBeNull()
  if (current === null) return
  for (const key of ['x', 'width', 'height'] as const) {
    expect(
      Math.abs(current[key] - baseline[key]),
      `${label} frame ${key} changed`
    ).toBeLessThan(0.6)
  }
}

interface PageGeometry {
  readonly climate: FrameRect | null
  readonly equipment: FrameRect | null
  readonly overflow: { readonly scroll: number; readonly client: number }
}

async function captureGeometry(
  page: Page,
  climate: Locator,
  equipment: Locator
): Promise<PageGeometry> {
  await page.evaluate(() => window.scrollTo(0, 0))
  return {
    climate: await frameRectOf(climate),
    equipment: await frameRectOf(equipment),
    overflow: await pageOverflow(page),
  }
}

function expectGeometryStable(baseline: PageGeometry, current: PageGeometry): void {
  expectRectStable(baseline.climate!, current.climate, 'climate')
  expectRectStable(baseline.equipment!, current.equipment, 'equipment')
  expect(current.overflow.scroll, 'page grew horizontally').toBeLessThanOrEqual(
    current.overflow.client
  )
  expect(current.overflow.scroll).toBe(baseline.overflow.scroll)
}

for (const room of ROOMS) {
  test(`${room.room} historical bands, zoom seam, and live seam keep absolute transitions`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(240_000)
    const violations = trackViolations(page)
    await captureBandDrawing(page)
    await page.clock.setFixedTime(new Date(T))
    await page.goto(fixtureUrl(room.path, testInfo, undefined, 'historical-photoperiod'))
    await expect(page.getByRole('button', { name: 'Reset Zoom' })).toBeVisible()

    const climate = page.locator(`section[aria-label="${room.climate}"]`)
    const equipment = page.locator(`section[aria-label="${room.equipment}"]`)

    // Case 1: apply the fixed range through the toolbar in Toronto wall time
    // ([T-28h, T-16h) = Aug 19 04:00 → 16:00 EDT).
    await applyFixedRange(page)
    await expect
      .poll(() => scaleOf(climate))
      .toEqual({ min: FIXED_RANGE.start, max: FIXED_RANGE.end })
    await expect
      .poll(() => scaleOf(equipment))
      .toEqual({ min: FIXED_RANGE.start, max: FIXED_RANGE.end })
    const baseline = await captureGeometry(page, climate, equipment)

    // Case 2: actual canvas bands on both charts, boundaries on the real scale.
    for (const [label, section] of [
      ['climate', climate],
      ['equipment', equipment],
    ] as const) {
      const scan = await measurePlot(section, { probes: FIXED_PROBES })
      expect(scan, `${label} plot is not measurable`).not.toBeNull()
      const measured = scan!
      // SUN carry-in from the T-30h predecessor reaches the left plot edge.
      expectBandColumn(measured, FIXED_PROBE.sunEdge, 'SUN')
      expectBandColumn(measured, FIXED_PROBE.sunCarry, 'SUN')
      expectBandColumn(measured, FIXED_PROBE.moonCarry, 'MOON')
      expectGapColumn(measured, FIXED_PROBE.gapMid)
      expectBandColumn(measured, FIXED_PROBE.sunRun, 'SUN')
      expectBandColumn(measured, FIXED_PROBE.moonMid, 'MOON')
      expectBandColumn(measured, FIXED_PROBE.moonEdge, 'MOON')
      // Semantic colors from real pixels: SUN yellow (r > b), MOON blue/purple (b > r).
      expect(measured.sunColor[0]!).toBeGreaterThan(measured.sunColor[2]!)
      expect(measured.moonColor[2]!).toBeGreaterThan(measured.moonColor[0]!)
      expectNoLeak(measured, `${label} fixed range`)
      edgeMsAtBoundary(measured, 'S', 'M', at(-27))
      edgeMsAtBoundary(measured, 'M', '.', at(-24))
      edgeMsAtBoundary(measured, '.', 'S', at(-23))
      edgeMsAtBoundary(measured, 'S', 'M', at(-18))
      expect(
        Math.abs(lastColumnOf(measured, 'M') - scalePxOf(measured, FIXED_RANGE.end)),
        `${label}: MOON band does not reach the window end`
      ).toBeLessThanOrEqual(3)
    }

    await climate.scrollIntoViewIfNeeded()
    await climate.screenshot({ path: testInfo.outputPath('fixed-climate.png'), animations: 'disabled' })
    await equipment.scrollIntoViewIfNeeded()
    await equipment.screenshot({ path: testInfo.outputPath('fixed-equipment.png'), animations: 'disabled' })
    await page.screenshot({ path: testInfo.outputPath('fixed-page.png'), fullPage: true, animations: 'disabled' })

    // Case 3: real drag zoom across the absolute SUN→MOON transition at T-18h.
    await climate.scrollIntoViewIfNeeded()
    const dragTarget = await measurePlot(climate, { points: [at(-20), at(-16.4)] })
    expect(dragTarget?.points[0], 'drag start point unavailable').toBeDefined()
    expect(dragTarget?.points[1], 'drag end point unavailable').toBeDefined()
    const dragFrom = dragTarget!.points[0]!
    const dragTo = dragTarget!.points[1]!
    expect(Number.isFinite(dragFrom.x) && Number.isFinite(dragFrom.y)).toBe(true)
    expect(Number.isFinite(dragTo.x) && Number.isFinite(dragTo.y)).toBe(true)
    await page.mouse.move(dragFrom.x, dragFrom.y)
    await page.mouse.down()
    await page.mouse.move(dragTo.x, dragTo.y, { steps: 12 })
    await page.mouse.up()

    // Mouse coordinates are pixel-quantized by the browser input pipeline, so
    // the requested range may land within one plot pixel of the intended
    // times; it must still cross the absolute T-18h transition.
    const pixelMs = (dragTarget!.scaleMax - dragTarget!.scaleMin) / dragTarget!.bboxWidth
    const dragToleranceMs = Math.ceil(pixelMs) + 10
    await expect
      .poll(() => {
        const { start, end } = urlRange(page)
        if (start === null || end === null) return false
        return (
          Math.abs(Date.parse(start) - at(-20)) <= dragToleranceMs &&
          Math.abs(Date.parse(end) - at(-16.4)) <= dragToleranceMs &&
          Date.parse(start) < at(-18) &&
          at(-18) < Date.parse(end)
        )
      })
      .toBe(true)
    const zoomed = urlRange(page)
    const zoomRange = { min: Date.parse(zoomed.start ?? ''), max: Date.parse(zoomed.end ?? '') }
    await expect.poll(() => scaleOf(climate)).toEqual(zoomRange)
    await expect.poll(() => scaleOf(equipment)).toEqual(zoomRange)

    const zoomScan = await measurePlot(climate, { probes: ZOOM_PROBES })
    expect(zoomScan, 'zoomed plot is not measurable').not.toBeNull()
    expectBandColumn(zoomScan!, 0, 'SUN')
    expectBandColumn(zoomScan!, 1, 'SUN')
    expectBandColumn(zoomScan!, 2, 'MOON')
    expectBandColumn(zoomScan!, 3, 'MOON')
    expectNoLeak(zoomScan!, 'zoomed range')
    edgeMsAtBoundary(zoomScan!, 'S', 'M', at(-18))
    expect(
      Math.abs(lastColumnOf(zoomScan!, 'M') - scalePxOf(zoomScan!, zoomRange.max)),
      'zoomed MOON band does not reach the window end'
    ).toBeLessThanOrEqual(3)
    await climate.screenshot({ path: testInfo.outputPath('zoom-climate.png'), animations: 'disabled' })
    expectGeometryStable(baseline, await captureGeometry(page, climate, equipment))

    // Case 4: live 12h preset — historical MOON ends at Now, projected SUN begins there.
    await page
      .getByRole('group', { name: 'Time range presets' })
      .getByRole('button', { name: '12h', exact: true })
      .click()
    const liveRange = { min: at(-12), max: T + (12 * HOUR_MS) / 9 }
    await expect.poll(() => scaleOf(climate)).toEqual(liveRange)
    await expect.poll(() => scaleOf(equipment)).toEqual(liveRange)

    const liveScan = await measurePlot(climate, { probes: LIVE_PROBES })
    expect(liveScan, 'live plot is not measurable').not.toBeNull()
    expectBandColumn(liveScan!, LIVE_PROBE.histMoon, 'MOON')
    expectBandColumn(liveScan!, LIVE_PROBE.histSun, 'SUN')
    expectBandColumn(liveScan!, LIVE_PROBE.beforeNow, 'MOON')
    expectBandColumn(liveScan!, LIVE_PROBE.afterNow, 'SUN')
    expectBandColumn(liveScan!, LIVE_PROBE.projMoon, 'MOON')
    expectGapColumn(liveScan!, LIVE_PROBE.expired)
    expectNoLeak(liveScan!, 'live range')
    edgeMsAtBoundary(liveScan!, 'M', 'S', T)
    await climate.scrollIntoViewIfNeeded()
    await climate.screenshot({ path: testInfo.outputPath('live-climate.png'), animations: 'disabled' })
    expectGeometryStable(baseline, await captureGeometry(page, climate, equipment))
    expect(violations).toEqual([])
  })

  test(`${room.room} keeps overlay RGBA identical across all six themes`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(300_000)
    const violations = trackViolations(page)
    await captureBandDrawing(page)
    await page.clock.setFixedTime(new Date(T))
    await page.goto(fixtureUrl(room.path, testInfo, undefined, 'historical-photoperiod'))
    await expect(page.getByRole('button', { name: 'Reset Zoom' })).toBeVisible()

    const climate = page.locator(`section[aria-label="${room.climate}"]`)
    const equipment = page.locator(`section[aria-label="${room.equipment}"]`)

    // The fixed range is applied through the toolbar Apply; reloads restore it
    // from the URL the Apply wrote, and every iteration re-applies it.
    await applyFixedRange(page)
    await expect
      .poll(() => scaleOf(climate))
      .toEqual({ min: FIXED_RANGE.start, max: FIXED_RANGE.end })
    await expect
      .poll(() => scaleOf(equipment))
      .toEqual({ min: FIXED_RANGE.start, max: FIXED_RANGE.end })
    const themeBaseline = await captureGeometry(page, climate, equipment)

    const climateCounts: number[] = []
    const moonCounts: number[] = []
    const equipmentSunCounts: number[] = []
    const equipmentMoonCounts: number[] = []
    const backgrounds: string[] = []
    for (const theme of THEME_NAMES) {
      await page.evaluate(name => {
        localStorage.setItem('cea-theme', name)
      }, theme)
      await page.reload()
      await applyFixedRange(page)
      await expect
        .poll(() => scaleOf(climate))
        .toEqual({ min: FIXED_RANGE.start, max: FIXED_RANGE.end })
      await expect
        .poll(() => scaleOf(equipment))
        .toEqual({ min: FIXED_RANGE.start, max: FIXED_RANGE.end })
      expect(await page.evaluate(() => document.documentElement.dataset.theme ?? '')).toBe(theme)
      // The surrounding theme genuinely changes while the overlay must not.
      expectGeometryStable(themeBaseline, await captureGeometry(page, climate, equipment))

      for (const [label, section, sunCountsOut, moonCountsOut] of [
        ['climate', climate, climateCounts, moonCounts],
        ['equipment', equipment, equipmentSunCounts, equipmentMoonCounts],
      ] as const) {
        const scan = await measurePlot(section, { probes: FIXED_PROBES })
        expect(scan, `plot is not measurable under theme ${theme}`).not.toBeNull()
        const measured = scan!
        // The overlay itself: fixed semantic colors on real canvas pixels.
        expectBandColumn(measured, FIXED_PROBE.sunCarry, 'SUN')
        expectBandColumn(measured, FIXED_PROBE.moonCarry, 'MOON')
        expectGapColumn(measured, FIXED_PROBE.gapMid)
        expectBandColumn(measured, FIXED_PROBE.moonMid, 'MOON')
        expectNoLeak(measured, `${theme} ${label} overlay`)
        expect(measured.sunColor[0]!).toBeGreaterThan(measured.sunColor[2]!)
        expect(measured.moonColor[2]!).toBeGreaterThan(measured.moonColor[0]!)
        expect(measured.sunInside).toBeGreaterThan(measured.bboxHeight * 40)
        sunCountsOut.push(measured.sunInside)
        moonCountsOut.push(measured.moonInside)
        await section.scrollIntoViewIfNeeded()
        await section.screenshot({
          path: testInfo.outputPath(`theme-${theme}-${label}.png`),
          animations: 'disabled',
        })
      }
      backgrounds.push(
        await page.evaluate(
          () => getComputedStyle(document.querySelector('.mon-page')!).backgroundColor
        )
      )
    }

    // Overlay pixel totals stay identical in every theme on both charts.
    const overlayTolerance = (counts: number[]): number =>
      Math.max(64, 0.02 * counts[0]!)
    expect(distinctCount(backgrounds)).toBeGreaterThan(1)
    expect(
      Math.max(...climateCounts) - Math.min(...climateCounts),
      'climate SUN overlay pixel count varies across themes'
    ).toBeLessThanOrEqual(overlayTolerance(climateCounts))
    expect(
      Math.max(...moonCounts) - Math.min(...moonCounts),
      'climate MOON overlay pixel count varies across themes'
    ).toBeLessThanOrEqual(overlayTolerance(moonCounts))
    expect(
      Math.max(...equipmentSunCounts) - Math.min(...equipmentSunCounts),
      'equipment SUN overlay pixel count varies across themes'
    ).toBeLessThanOrEqual(overlayTolerance(equipmentSunCounts))
    expect(
      Math.max(...equipmentMoonCounts) - Math.min(...equipmentMoonCounts),
      'equipment MOON overlay pixel count varies across themes'
    ).toBeLessThanOrEqual(overlayTolerance(equipmentMoonCounts))
    expect(violations).toEqual([])
  })
}

/** Number of distinct values in a small runtime list. */
function distinctCount<T extends string | number>(values: readonly T[]): number {
  const sorted = [...values].sort()
  let count = 0
  for (let i = 0; i < sorted.length; i += 1) {
    if (i === 0 || sorted[i] !== sorted[i - 1]) count += 1
  }
  return count
}
