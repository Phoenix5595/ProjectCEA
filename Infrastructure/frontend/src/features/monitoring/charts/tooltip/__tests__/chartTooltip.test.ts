import { describe, expect, it } from 'vitest'

import type uPlot from 'uplot'
import type { AlignedData, AlignedSeries, LightTrajectorySegment } from '../../../data'
import { seriesKey } from '../../../data/alignSeries.types'
import { formatTooltipValue, tooltipPlugin, valueAtCursor } from '../chartTooltip'

describe('valueAtCursor', () => {
  it('interpolates a linear series between finite samples', () => {
    // Given: a linear ramp with values at either side of the cursor
    const xValues = [0, 10]
    const yValues = [10, 30]

    // When: the cursor is halfway through the ramp
    const value = valueAtCursor('linear', xValues, yValues, 5)

    // Then: the tooltip uses the interpolated value
    expect(value).toBe(20)
  })

  it('interpolates a sensor series between finite samples', () => {
    // Given: a continuous sensor trace with values around the cursor
    const xValues = [0, 10]
    const yValues = [10, 30]

    // When: the cursor is halfway through the trace
    const value = valueAtCursor('sensor', xValues, yValues, 5)

    // Then: the tooltip reports the trace value at that time
    expect(value).toBe(20)
  })

  it('holds a step series left value until its next timestamp', () => {
    // Given: a device state that changes at the next sample
    const xValues = [0, 10]
    const yValues = [0, 100]

    // When: the cursor is between state changes
    const value = valueAtCursor('step', xValues, yValues, 9)

    // Then: the tooltip reports the active left state
    expect(value).toBe(0)
  })

  it('uses the nearest actual point value', () => {
    // Given: sparse point values on either side of the cursor
    const xValues = [0, 10]
    const yValues = [10, 30]

    // When: the cursor is closer to the right point
    const value = valueAtCursor('point', xValues, yValues, 8)

    // Then: the tooltip preserves a real point value rather than interpolating
    expect(value).toBe(30)
  })

  it('returns null inside a null gap', () => {
    // Given: a missing sample adjacent to the cursor interval
    const xValues = [0, 10, 20]
    const yValues = [10, null, 30]

    // When: the cursor falls within the gap
    const value = valueAtCursor('linear', xValues, yValues, 5)

    // Then: no value is invented across the gap
    expect(value).toBeNull()
  })

  it('does not extrapolate before or after finite samples', () => {
    // Given: finite values with a bounded time domain
    const xValues = [0, 10]
    const yValues = [10, 30]

    // When: the cursor is outside that domain
    const beforeFirst = valueAtCursor('step', xValues, yValues, -1)
    const afterLast = valueAtCursor('linear', xValues, yValues, 11)

    // Then: neither side extrapolates a value
    expect(beforeFirst).toBeNull()
    expect(afterLast).toBeNull()
  })
})

describe('light trajectory tooltip', () => {
  it('lists each canonical light once while retaining different lights at the same intensity', () => {
    const light: AlignedSeries = {
      key: seriesKey('light', 'light_f_1', 'linear'), label: 'Chilled Front - Intensity',
      kind: 'linear', source: 'light', metric: 'light_f_1', family: 'light', role: 'linear',
      y: [0, 10], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '%',
      lightTrajectory: [
        { start: 0, end: 100, shape: 'step', startValue: 0, endValue: 0, origin: 'recorded', quality: 'exact' },
      ],
    }
    const other = { ...light, key: seriesKey('light', 'light_f_2', 'linear'), metric: 'light_f_2', label: 'Apache - Intensity' }
    const series = [light, { ...light }, { ...light }, other]
    const plugin = tooltipPlugin(series, { bg: 'white', border: 'black', text: 'black' })
    const root = document.createElement('div')
    const plot = {
      root, cursor: { left: 50, top: 5 }, data: [[0, 100], ...series.map(item => item.y)],
      series: [{}, ...series.map(() => ({ show: true }))], posToVal: (value: number) => value,
    } as unknown as uPlot
    const readyHooks = plugin.hooks?.ready
    const ready = Array.isArray(readyHooks) ? readyHooks[0] : readyHooks
    const cursorHooks = plugin.hooks?.setCursor
    const cursor = Array.isArray(cursorHooks) ? cursorHooks[0] : cursorHooks
    if (!ready || !cursor) throw new Error('Tooltip hooks are required')
    ready(plot)
    cursor(plot)
    const rows = [...root.querySelectorAll('.mon-tooltip > div')].map(row => row.textContent)
    expect(rows).toEqual([
      'Chilled Front - Intensity 0.0 % recorded/exact',
      'Apache - Intensity 0.0 % recorded/exact',
    ])
  })

  it('reads current step, ramp, gap, label, and provenance after a feed update', () => {
    const key = seriesKey('light', 'light_f_1', 'linear')
    const segments: readonly LightTrajectorySegment[] = [
      {
        start: 0,
        end: 10,
        shape: 'step',
        startValue: 40,
        endValue: 40,
        origin: 'recorded',
        quality: 'exact',
      },
      {
        start: 10,
        end: 20,
        shape: 'linear',
        startValue: 40,
        endValue: 80,
        origin: 'projected',
        quality: 'estimated',
      },
      {
        start: 20,
        end: 30,
        shape: 'step',
        startValue: null,
        endValue: null,
        origin: 'projected',
        quality: 'unavailable',
      },
    ]
    const initial: AlignedSeries = {
      key,
      label: 'light_f_1 - Intensity',
      kind: 'linear',
      source: 'light',
      metric: 'light_f_1',
      family: 'light',
      role: 'linear',
      y: [40, 40, null, null],
      origin: 'recorded',
      quality: 'exact',
      isAggregated: false,
      unit: '%',
      presentation: { color: 'yellow', decimals: 0 },
      lightTrajectory: [segments[0]!],
    }
    const asData = (series: AlignedSeries): AlignedData => ({
      x: [0, 10, 20, 30],
      series: [series],
      bands: [],
      photoperiod: [],
      nowIndex: 0,
      nowMs: 0,
      sensorIntervalSeconds: null,
      aggregated: false,
    })
    let currentData = asData(initial)
    const plugin = tooltipPlugin([initial], { bg: 'white', border: 'black', text: 'black' }, () =>
      currentData
    )
    const root = document.createElement('div')
    const plotState = {
      root,
      cursor: { left: 5, top: 5 },
      data: [[0, 10, 20, 30], [40, 40, null, null]],
      series: [{}, { show: true }],
      posToVal: (value: number) => value,
    }
    const plot = plotState as unknown as uPlot
    const readyHooks = plugin.hooks?.ready
    const ready = Array.isArray(readyHooks) ? readyHooks[0] : readyHooks
    const cursorHooks = plugin.hooks?.setCursor
    const setCursor = Array.isArray(cursorHooks) ? cursorHooks[0] : cursorHooks
    if (ready === undefined || setCursor === undefined) {
      throw new Error('tooltip ready and cursor hooks are required')
    }
    ready(plot)
    currentData = asData({ ...initial, label: 'Chilled Front QA - Intensity', lightTrajectory: segments })

    const at = (time: number): string => {
      plotState.cursor.left = time
      setCursor(plot)
      return root.querySelector('.mon-tooltip')?.textContent ?? ''
    }

    expect(at(5)).toContain('Chilled Front QA - Intensity 40 % recorded/exact')
    expect(at(15)).toContain('Chilled Front QA - Intensity 60 % projected/estimated')
    expect(at(25)).toContain('Chilled Front QA - Intensity — projected/unavailable')
  })
})

describe('formatTooltipValue', () => {
  it('renders presentation precision and null gaps', () => {
    // Given: a series with manifest precision and a value or gap
    const presentation = { decimals: 2 }

    // When: tooltip row text is formatted
    const formatted = formatTooltipValue(12.345, presentation, '°C')
    const gap = formatTooltipValue(null, presentation, '°C')

    // Then: precision and the gap marker are preserved in rendered text
    expect(formatted).toBe(' 12.35 °C')
    expect(gap).toBe(' —')
  })
})

describe('overview bucket tooltip', () => {
  it('uses half-open calendar bounds rather than interpolating sampled averages across months', () => {
    const boundaries = [
      Date.UTC(2025, 0, 1), Date.UTC(2025, 1, 1),
      Date.UTC(2025, 2, 1), Date.UTC(2025, 3, 1),
    ]
    const buckets = boundaries.slice(0, -1).map((start, index) => ({
      start, end: boundaries[index + 1],
    }))
    const series: AlignedSeries = {
      key: seriesKey('light', 'light_f_1', 'mean'),
      label: 'Recorded sampled intensity',
      kind: 'linear', source: 'light', metric: 'light_f_1', family: 'light',
      role: 'mean', y: [10, 90, null], origin: 'derived', quality: 'exact',
      isAggregated: true, preserveGaps: true, unit: '%', presentation: { decimals: 0 },
    }
    const data: AlignedData = {
      x: buckets.map(bucket => (bucket.start + bucket.end) / 2),
      series: [series], bands: [], photoperiod: [], nowIndex: -1,
      nowMs: 0, sensorIntervalSeconds: 3600,
      aggregated: true, overview: { buckets },
    }
    let instant = Date.UTC(2025, 0, 31, 23)
    const root = document.createElement('div')
    const plot = {
      root, cursor: { left: 5, top: 5 }, data: [data.x, series.y],
      series: [{}, { show: true }], posToVal: () => instant,
    } as unknown as uPlot
    const plugin = tooltipPlugin([series], { bg: 'white', border: 'black', text: 'black' }, () => data)
    const ready = plugin.hooks?.ready
    const cursor = plugin.hooks?.setCursor
    const onReady = Array.isArray(ready) ? ready[0] : ready
    const onCursor = Array.isArray(cursor) ? cursor[0] : cursor
    if (onReady === undefined || onCursor === undefined) throw new Error('Missing tooltip hooks')
    onReady(plot)
    onCursor(plot)
    const tooltip = root.querySelector<HTMLDivElement>('.mon-tooltip')!
    const torontoClock = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/Toronto',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    })
    expect(tooltip.textContent).toContain('10 %')
    expect(tooltip.textContent).toContain(torontoClock.format(new Date(boundaries[0])))
    expect(tooltip.textContent).toContain(torontoClock.format(new Date(boundaries[1])))
    instant = boundaries[1]
    onCursor(plot)
    expect(tooltip.textContent).toContain('90 %')
    expect(tooltip.textContent).toContain(torontoClock.format(new Date(boundaries[2])))
    instant = boundaries[2]
    onCursor(plot)
    expect(tooltip.textContent).toContain('unavailable')
    expect(tooltip.textContent).not.toContain('90 %')
    instant = boundaries[3]
    onCursor(plot)
    expect(tooltip.style.display).toBe('none')
  })
})
