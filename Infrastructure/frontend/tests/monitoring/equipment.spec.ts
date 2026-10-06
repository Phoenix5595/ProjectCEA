import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'

import { describeViolation, FIXTURE_ORIGIN } from '../../src/features/monitoring/config/originGuard'
import { DASHBOARD_DEVICES } from './dashboardDeviceFixtures'
import { fixtureUrl } from './fixtureUrl'

interface EquipmentRoom {
  readonly room: 'Flower Room' | 'Veg Room'
  readonly path: '/flower/monitoring' | '/vegetation/monitoring'
  readonly equipmentLabel: string
  readonly climateLabel: string
  readonly heaterName: string
}

interface PlotSeries {
  readonly label: string
  readonly scale: string
  readonly visible: boolean
  readonly dash: readonly number[]
}

interface PlotSnapshot {
  readonly series: readonly PlotSeries[]
  readonly mouse: {
    readonly x: number
    readonly y: number
    readonly rootLeft: number
    readonly overlayLeft: number
    readonly bboxLeft: number | null
    readonly valPosition: number
    readonly millisecondsPerPixel: number
  } | null
  readonly cursorTime: number | null
  readonly lightCoverage: { readonly recorded: number; readonly future: number } | null
}

const ROOMS: readonly EquipmentRoom[] = [
  {
    room: 'Flower Room',
    path: '/flower/monitoring',
    equipmentLabel: 'Flower atmosphere & equipment',
    climateLabel: 'Flower climate conditions',
    heaterName: 'Heater Flower',
  },
  {
    room: 'Veg Room',
    path: '/vegetation/monitoring',
    equipmentLabel: 'Veg atmosphere & equipment',
    climateLabel: 'Veg climate conditions',
    heaterName: 'Heater Veg',
  },
]

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

function displayName(room: EquipmentRoom, deviceName: string, fallback: string): string {
  if (room.room === 'Flower Room' && deviceName === 'light_f_1') return 'Chilled Front QA'
  if (room.room === 'Veg Room' && deviceName === 'light_v_1') return 'Eyefinity Top QA'
  return fallback
}

function fixtureRegistry(room: EquipmentRoom) {
  return DASHBOARD_DEVICES.map(device => ({
    ...device,
    display_name: displayName(room, device.device_name, device.display_name),
  }))
}

async function plotSnapshot(
  section: Locator,
  timestamp: number | null = null,
  strokeProbe: { label: string; value: number; now: number; duration: number } | null = null
): Promise<PlotSnapshot | null> {
  return section.locator('.mon-chart__frame').evaluate((frame, requested) => {
    const requestedTime = requested.timestamp
    const isObject = (value: unknown): value is object =>
      typeof value === 'object' && value !== null
    const get = (value: object, key: PropertyKey): unknown => Reflect.get(value, key)
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
          Array.isArray(get(current, 'series')) &&
          Array.isArray(get(current, 'data'))
        ) {
          plot = current
          break
        }
      }
      hook = get(hook, 'next')
    }
    if (plot === null) return null

    const series = get(plot, 'series')
    const data = get(plot, 'data')
    const root = get(plot, 'root')
    const bbox = get(plot, 'bbox')
    if (!Array.isArray(series) || !Array.isArray(data) || !isObject(root)) return null
    const renderedSeries = series.slice(1).map((entry: unknown) => {
      if (!isObject(entry)) return { label: '', scale: '', visible: false, dash: [] }
      const label = get(entry, 'label')
      const scale = get(entry, 'scale')
      const dash = get(entry, 'dash')
      return {
        label: typeof label === 'string' ? label : '',
        scale: typeof scale === 'string' ? scale : '',
        visible: get(entry, 'show') !== false,
        dash: Array.isArray(dash) ? dash.filter((value): value is number => typeof value === 'number') : [],
      }
    })

    let mouse: PlotSnapshot['mouse'] = null
    if (requestedTime !== null) {
      const valToPos = get(plot, 'valToPos')
      const rootElement = root as HTMLElement
      const rootRect = rootElement.getBoundingClientRect()
      const overlay = rootElement.querySelector('.u-over')
      const bboxLeft = isObject(bbox) ? get(bbox, 'left') : null
      if (typeof valToPos === 'function' && overlay !== null) {
        const overlayRect = overlay.getBoundingClientRect()
        const valPosition = valToPos.call(plot, requestedTime, 'x')
        const nextMinutePosition = valToPos.call(plot, requestedTime + 60_000, 'x')
        if (typeof valPosition === 'number' && typeof nextMinutePosition === 'number' &&
            nextMinutePosition !== valPosition) {
          mouse = {
            x: overlayRect.left + valPosition,
            y: overlayRect.top + overlayRect.height / 2,
            rootLeft: rootRect.left,
            overlayLeft: overlayRect.left,
            bboxLeft: typeof bboxLeft === 'number' ? bboxLeft : null,
            valPosition,
            millisecondsPerPixel: 60_000 / Math.abs(nextMinutePosition - valPosition),
          }
        }
      }
    }

    const cursor = get(plot, 'cursor')
    const cursorLeft = isObject(cursor) ? get(cursor, 'left') : null
    const posToVal = get(plot, 'posToVal')
    const cursorTime =
      typeof cursorLeft === 'number' && typeof posToVal === 'function'
        ? posToVal.call(plot, cursorLeft, 'x')
        : null

    let lightCoverage: PlotSnapshot['lightCoverage'] = null
    if (requested.strokeProbe !== null) {
      const probe = requested.strokeProbe
      const entryIndex = series.findIndex((entry: unknown) =>
        isObject(entry) && get(entry, 'label') === probe.label
      )
      const entry: unknown = series[entryIndex]
      const ctx = get(plot, 'ctx')
      const valToPos = get(plot, 'valToPos')
      if (entryIndex > 0 && isObject(entry) && ctx instanceof CanvasRenderingContext2D &&
          typeof valToPos === 'function') {
        const rawStroke = get(entry, 'stroke')
        const stroke: unknown = typeof rawStroke === 'function'
          ? rawStroke.call(plot, plot, entryIndex)
          : rawStroke
        if (typeof stroke === 'string') {
          const colorCanvas = document.createElement('canvas')
          colorCanvas.width = colorCanvas.height = 1
          const colorContext = colorCanvas.getContext('2d')!
          colorContext.fillStyle = stroke
          colorContext.fillRect(0, 0, 1, 1)
          const color = colorContext.getImageData(0, 0, 1, 1).data
          const y = Math.round(valToPos.call(plot, probe.value, 'light', true))
          const coverage = (from: number, until: number): number => {
            const left = Math.max(0, Math.ceil(valToPos.call(plot, from, 'x', true)))
            const right = Math.min(ctx.canvas.width, Math.floor(valToPos.call(plot, until, 'x', true)))
            const width = right - left
            if (width < 2 || y < 4 || y + 4 >= ctx.canvas.height) return 0
            const image = ctx.getImageData(left, y - 4, width, 9).data
            let occupied = 0
            for (let x = 0; x < width; x += 1) {
              for (let row = 0; row < 9; row += 1) {
                const offset = (row * width + x) * 4
                if (image[offset + 3]! > 90 &&
                    Math.abs(image[offset]! - color[0]!) < 35 &&
                    Math.abs(image[offset + 1]! - color[1]!) < 35 &&
                    Math.abs(image[offset + 2]! - color[2]!) < 35) {
                  occupied += 1
                  break
                }
              }
            }
            return occupied / width
          }
          lightCoverage = {
            recorded: coverage(probe.now - probe.duration / 4, probe.now - probe.duration / 8),
            future: coverage(probe.now + probe.duration / 36, probe.now + probe.duration / 18),
          }
        }
      }
    }

    return { series: renderedSeries, mouse, cursorTime, lightCoverage }
  }, { timestamp, strokeProbe })
}

async function hoverAt(
  page: Page,
  section: Locator,
  timestamp: number,
  expected: string | ((cursorTime: number) => string)
): Promise<number> {
  const before = await plotSnapshot(section, timestamp)
  if (before?.mouse === null || before === null) throw new Error('light chart hover target is unavailable')
  await page.mouse.move(before.mouse.x, before.mouse.y)
  const after = await plotSnapshot(section)
  if (after?.cursorTime === null || after?.cursorTime === undefined) {
    throw new Error('light chart cursor did not register the hover')
  }
  expect(
    Math.abs(after.cursorTime - timestamp),
    `cursor ${after.cursorTime} missed ${timestamp}: ${JSON.stringify(before.mouse)}`
  ).toBeLessThan(Math.max(30_000, before.mouse.millisecondsPerPixel))
  const tooltip = section.locator('.mon-tooltip')
  await expect(tooltip).toBeVisible()
  const expectedText = typeof expected === 'string' ? expected : expected(after.cursorTime)
  await expect(tooltip).toContainText(expectedText)
  return after.cursorTime

}

async function equipmentHistoryScenario(
  page: Page,
  testInfo: TestInfo,
  room: EquipmentRoom
): Promise<void> {
  test.setTimeout(90_000)
  const violations = trackViolations(page)
  const registryGate = (
    Promise as PromiseConstructor & {
      withResolvers<T>(): {
        promise: Promise<T>
        resolve(value: T | PromiseLike<T>): void
      }
    }
  ).withResolvers<void>()
  let registryCalls = 0
  await page.route('**/api/devices/registry', async route => {
    const request = route.request()
    const url = request.url()
    const violation = describeViolation(url)
    if (
      violation !== null ||
      new URL(url).origin !== FIXTURE_ORIGIN ||
      request.method() !== 'GET'
    ) {
      violations.push(`${violation ?? 'unexpected-registry-method'}: ${request.method()} ${url}`)
      await route.abort()
      return
    }
    registryCalls += 1
    await registryGate.promise
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixtureRegistry(room)),
    })
  })

  const historyPath = `/api/monitoring/control/${encodeURIComponent(room.room)}/history`
  const projectionPath = `/api/monitoring/control/${encodeURIComponent(room.room)}/projection`
  const historyPromise = page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.origin === FIXTURE_ORIGIN && url.pathname === historyPath
  })
  const projectionPromise = page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.origin === FIXTURE_ORIGIN && url.pathname === projectionPath
  })

  try {
    await page.goto(fixtureUrl(room.path, testInfo, undefined, 'equipment-history'), {
      waitUntil: 'domcontentloaded',
    })
    const [historyResponse, projectionResponse] = await Promise.all([
      historyPromise,
      projectionPromise,
    ])
    expect(historyResponse.ok()).toBe(true)
    expect(projectionResponse.ok()).toBe(true)
    const history = (await historyResponse.json()) as {
      climate: Array<{ name: string; points: Array<{ metric?: string }> }>
      lights: Array<{ name: string; points: unknown[]; steps: unknown[]; linear: unknown[] }>
      devices: Array<{ name: string }>
      pid: Array<{ name: string }>
    }
    const projection = (await projectionResponse.json()) as {
      trajectory?: { window: { start: string; end: string } }
    }
    const lightNames = DASHBOARD_DEVICES.filter(
      device => device.location === room.room && device.device_type === 'light'
    ).map(device => device.device_name)
    const [budgetedName, rawName] = lightNames
    if (budgetedName === undefined || rawName === undefined || projection.trajectory === undefined) {
      throw new Error('equipment-history fixture is incomplete')
    }
    const budgeted = history.lights.find(series => series.name === budgetedName)
    const raw = history.lights.find(series => series.name === rawName)
    expect(budgeted?.points).toEqual([])
    expect(budgeted?.steps.length).toBeGreaterThan(0)
    expect(budgeted?.linear.length).toBeGreaterThan(0)
    expect(raw?.points.length).toBeGreaterThan(0)
    expect(history.climate.find(series => series.name === 'heating')?.points[0]?.metric).toBe(
      'heating_setpoint'
    )
    expect(history.devices.some(series => series.name === budgetedName)).toBe(true)
    expect(history.pid.some(series => series.name === budgetedName)).toBe(true)
    expect(history.devices.some(series => series.name === room.heaterName)).toBe(true)
    expect(history.pid.some(series => series.name === room.heaterName)).toBe(true)

    const equipment = page.locator(`section[aria-label="${room.equipmentLabel}"]`)
    const climate = page.locator(`section[aria-label="${room.climateLabel}"]`)
    await expect(equipment).toBeVisible()
    await expect(equipment.getByRole('button', { name: 'View data as table' })).toBeVisible()
    await expect.poll(() => registryCalls).toBe(1)
    await equipment.getByRole('button', { name: 'View data as table' }).click()
    const table = equipment.getByRole('table', { name: `${room.equipmentLabel} data` })
    await expect(table).toBeVisible()
    await expect(table.getByRole('columnheader', { name: `${budgetedName} - Intensity` })).toHaveCount(1)

    registryGate.resolve()
    const expectedIntensityLabels = DASHBOARD_DEVICES.filter(
      device => device.location === room.room && device.device_type === 'light'
    )
      .map(device => `${displayName(room, device.device_name, device.display_name)} - Intensity`)
      .sort()
    await expect
      .poll(async () => (await table.getByRole('columnheader').allTextContents()).filter(label =>
        label.endsWith(' - Intensity')
      ).sort())
      .toEqual(expectedIntensityLabels)

    const headers = await table.getByRole('columnheader').allTextContents()
    expect(headers.some(label => /setpoint/i.test(label))).toBe(false)
    expect(headers).toContain(`${room.heaterName} - State`)
    expect(headers).toContain(`${room.heaterName} - PID Output`)
    expect(headers).toContain(`${room.heaterName} - Duty Cycle`)
    expect(headers).not.toContain(`${budgetedName} - Intensity`)

    await expect(table.getByText('18.0% (recorded/exact)').first()).toBeVisible()
    await expect
      .poll(async () => {
        const snapshot = await plotSnapshot(equipment)
        return (snapshot?.series.filter(
          series => series.scale === 'light' && series.label.endsWith(' - Intensity')
        ) ?? []).sort((left, right) => left.label.localeCompare(right.label))
      })
      .toEqual(expectedIntensityLabels.map(label => ({ label, scale: 'light', visible: true, dash: [] })))

    await equipment.scrollIntoViewIfNeeded()
    await page.getByRole('button', { name: 'Pause', exact: true }).click()
    await equipment.screenshot({ path: testInfo.outputPath('equipment-panel.png'), animations: 'disabled' })
    const tooltipRows = equipment.locator('.mon-tooltip > div')
    const historyStart = Date.parse((await historyResponse.json()).range.start)
    const historyEnd = Date.parse((await historyResponse.json()).range.end)
    await hoverAt(
      page, equipment, historyStart + (historyEnd - historyStart) * 0.30,
      `${displayName(room, budgetedName, '')} - Intensity 0.0 % recorded/exact`
    )
    await expect(tooltipRows.filter({ hasText: `${displayName(room, budgetedName, '')} - Intensity` })).toHaveCount(1)
    await hoverAt(
      page, equipment, historyStart + (historyEnd - historyStart) * 0.41,
      `${displayName(room, budgetedName, '')} - Intensity 10.0 % recorded/exact`
    )
    await expect(tooltipRows.filter({ hasText: `${displayName(room, budgetedName, '')} - Intensity` })).toHaveCount(1)

    const projectionStart = Date.parse(projection.trajectory.window.start)
    await hoverAt(page, equipment, projectionStart + 30_000, `${displayName(room, budgetedName, '')} - Intensity 40.0 % projected/estimated`)
    const rampMidpoint = projectionStart + 108_000
    const rampCursor = await hoverAt(page, equipment, rampMidpoint, cursorTime => {
      const fraction = (cursorTime - (projectionStart + 72_000)) / 72_000
      return `${displayName(room, budgetedName, '')} - Intensity ${(40 + 40 * fraction).toFixed(1)} % projected/estimated`
    })
    expect(Math.abs(rampCursor - rampMidpoint)).toBeLessThan(15_000)
    await hoverAt(
      page,
      equipment,
      projectionStart + 252_000,
      `${displayName(room, budgetedName, '')} - Intensity — projected/unavailable`
    )

    await climate.getByRole('button', { name: 'View data as table' }).click()
    const climateTable = climate.getByRole('table', { name: `${room.climateLabel} data` })
    await expect(climateTable.getByRole('columnheader', { name: /heating/i })).toHaveCount(2)
    await expect(climateTable).toContainText('22.0 (recorded/exact)')
    expect(registryCalls).toBe(1)
    expect(violations).toEqual([])
  } finally {
    registryGate.resolve()
    await page.unroute('**/api/devices/registry')
  }
}

test('Flower equipment graph uses one registry-labeled intensity trace per light', async (
  { page },
  testInfo
) => {
  await equipmentHistoryScenario(page, testInfo, ROOMS[0]!)
})

test('Veg equipment graph uses one registry-labeled intensity trace per light', async (
  { page },
  testInfo
) => {
  await equipmentHistoryScenario(page, testInfo, ROOMS[1]!)
})

for (const room of ROOMS) {
  test(`${room.room} keeps recorded ramp shape across ranges and dots only future light coverage`, async (
    { page },
    testInfo
  ) => {
    test.setTimeout(120_000)
    const violations = trackViolations(page)
    const now = Date.parse('2026-10-06T16:00:00.000Z')
    await page.clock.setFixedTime(new Date(now))
    await page.goto(fixtureUrl(room.path, testInfo, undefined, 'light-range-fidelity'))
    const equipment = page.locator(`section[aria-label="${room.equipmentLabel}"]`)
    await expect(equipment).toBeVisible()
    const lights = DASHBOARD_DEVICES.filter(device =>
      device.location === room.room && device.device_type === 'light'
    )
    const primary = `${lights[0]!.display_name} - Intensity`
    const constant = `${lights[1]!.display_name} - Intensity`
    const presets = page.getByRole('group', { name: 'Time range presets' })
    for (const [label, duration] of [
      ['1h', 3_600_000],
      ['12h', 12 * 3_600_000],
      ['24h', 24 * 3_600_000],
      ['7d', 7 * 24 * 3_600_000],
    ] as const) {
      await presets.getByRole('button', { name: label, exact: true }).click()
      await expect.poll(async () => {
        const snapshot = await plotSnapshot(equipment)
        return snapshot?.series.filter(series => series.scale === 'light').map(series => series.label)
      }).toEqual(lights.map(light => `${light.display_name} - Intensity`))
      await equipment.scrollIntoViewIfNeeded()
      await hoverAt(page, equipment, now - 45 * 60_000, cursorTime => {
        const value = 10 + (cursorTime - (now - 55 * 60_000)) / (20 * 60_000) * 30
        return `${primary} ${value.toFixed(1)} % recorded/exact`
      })
      await hoverAt(page, equipment, now - 15.5 * 60_000, `${primary} — recorded/unavailable`)
      await hoverAt(page, equipment, now + duration / 36, cursorTime => {
        const value = cursorTime <= now + 60 * 60_000
          ? 70 + (cursorTime - now) / (60 * 60_000) * 20
          : cursorTime < now + 120 * 60_000 ? 90 : 0
        return `${primary} ${value.toFixed(1)} % projected/estimated`
      })
      await page.mouse.move(0, 0)
      await expect.poll(async () =>
        (await plotSnapshot(equipment, null, { label: constant, value: 25, now, duration }))?.lightCoverage
      ).not.toBeNull()
      const snapshot = await plotSnapshot(equipment, null, { label: constant, value: 25, now, duration })
      expect(snapshot!.lightCoverage!.recorded).toBeGreaterThan(0.85)
      expect(snapshot!.lightCoverage!.future).toBeGreaterThan(0.05)
      expect(snapshot!.lightCoverage!.future).toBeLessThan(0.80)
      await equipment.screenshot({ path: testInfo.outputPath(`light-range-${label}.png`) })
    }
    expect(violations).toEqual([])
  })
}
