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
