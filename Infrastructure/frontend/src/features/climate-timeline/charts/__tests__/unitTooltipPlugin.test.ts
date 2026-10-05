import type uPlot from 'uplot'
import { describe, expect, it } from 'vitest'

import type { EnvelopeSeriesKey } from '../envelopeSeries'
import { timelineSeriesMeta, type TimelineSeriesKey } from '../timelineOptions'
import type { TimelineSampleQuality } from '../timelineSources'
import {
  formatTooltipValue,
  unitTooltipPlugin,
  type TimelineTooltipState,
} from '../unitTooltipPlugin'

const KEYS = [
  'heating_setpoint:scheduled',
  'heating_setpoint:effective',
  'cooling_setpoint:scheduled',
  'vpd_setpoint:scheduled',
  'co2_setpoint:scheduled',
] as const satisfies readonly EnvelopeSeriesKey[]

function state(): TimelineTooltipState {
  return {
    data: [
      [0, 1, 2, 3],
      [23.46, null, 24.0, 25.0],
      [22.9, 22.5, null, 24.5],
      [27.5, 28.0, 26.5, 27.0],
      [1.02, null, 0.98, 1.1],
      [810, 795, null, 820],
    ],
    meta: timelineSeriesMeta(KEYS, 'selected-saved', 'Saved Flower · '),
  }
}

function harness(getState: () => TimelineTooltipState = state) {
  const over = document.createElement('div')
  Object.defineProperty(over, 'clientWidth', { value: 1440 })
  Object.defineProperty(over, 'clientHeight', { value: 400 })
  document.body.appendChild(over)
  const plugin = unitTooltipPlugin(getState)
  const u = {
    over,
    valToPos: (value: number) => value * 400,
    cursor: { left: -1, top: -1, idx: null },
  } as unknown as uPlot
  const init = plugin.hooks.init as (plot: uPlot) => void
  init(u)
  const tooltip = over.querySelector<HTMLElement>('.timeline-unit-tooltip')
  if (tooltip === null) throw new Error('tooltip not mounted')
  const setCursor = (left: number, top: number, idx: number | null) => {
    Object.assign(u.cursor, { left, top, idx })
    ;(plugin.hooks.setCursor as (plot: uPlot) => void)(u)
  }
  return { over, tooltip, setCursor }
}

describe('formatTooltipValue', () => {
  it('formats each family with its unit', () => {
    expect(formatTooltipValue('temp', 23.46)).toBe('23.5 °C')
    expect(formatTooltipValue('vpd', 1.02)).toBe('1.02 kPa')
    expect(formatTooltipValue('co2', 810.4)).toBe('810 ppm')
  })
})

describe('unitTooltipPlugin', () => {
  it('keeps equal current/draft values separate and exposes cursor-specific quality plus the observation timestamp', () => {
    const current = timelineSeriesMeta(['heating_setpoint:effective'], 'active-current', 'Current effective · ')
    const draft = timelineSeriesMeta(['heating_setpoint:scheduled'], 'selected-draft', 'Draft Drying · ')
    const { tooltip, setCursor } = harness(() => ({
      data: [[0.25, 1], [22, null], [22, 19]],
      meta: [...current, ...draft],
      windowStartMs: Date.parse('2026-01-01T12:00:00.000Z'),
      qualities: new Map<TimelineSeriesKey, readonly TimelineSampleQuality[]>([
        [current[0]!.key, ['exact', 'unavailable'] as const],
        [draft[0]!.key, ['exact', 'estimated'] as const],
      ]),
    }))
    setCursor(100, 200, 0)
    expect(tooltip.textContent?.match(/22\.0 °C/g)).toHaveLength(2)
    expect(tooltip.textContent).toContain('active-current · exact')
    expect(tooltip.textContent).toContain('selected-draft · exact')
    expect(tooltip.textContent).toContain('12:00:15.000 UTC')
    setCursor(400, 200, 1)
    expect(tooltip.textContent).not.toContain('active-current')
    expect(tooltip.textContent).toContain('selected-draft · estimated')
  })

  it('finds a nearby sparse observation without bridging forecast gaps or carrying expired facts', () => {
    const meta = [
      ...timelineSeriesMeta(['heating_setpoint:effective'], 'active-current', 'Current effective · '),
      ...timelineSeriesMeta(['heating_setpoint:effective'], 'active-future', 'Running forecast · '),
      ...timelineSeriesMeta(['heating_setpoint:scheduled'], 'selected-saved', 'Saved Drying · '),
    ]
    let snapshot: TimelineTooltipState = {
      data: [[0.249983333333, 0.25, 1], [null, 22, null], [null, 24, null], [18, 18, 18]],
      meta,
      windowStartMs: Date.parse('2026-01-01T12:00:00.000Z'),
      qualities: new Map([[meta[0]!.key, ['unavailable', 'exact', 'unavailable'] as const]]),
    }
    const { tooltip, setCursor } = harness(() => snapshot)
    setCursor(99.99, 200, 0)
    expect(tooltip.textContent).toContain('22.0 °C')
    expect(tooltip.textContent).toContain('active-current · exact · 12:00:15.000 UTC')
    expect(tooltip.textContent).not.toContain('active-future')
    expect(tooltip.textContent).toContain('18.0 °C')
    setCursor(104, 200, 1)
    expect(tooltip.textContent).not.toContain('active-current')
    expect(tooltip.textContent).toContain('24.0 °C')
    snapshot = { ...snapshot, data: [snapshot.data[0], [null, null, null], snapshot.data[2], snapshot.data[3]] }
    setCursor(99.99, 200, 0)
    expect(tooltip.textContent).not.toContain('active-current')
    expect(tooltip.textContent).toContain('18.0 °C')
  })

  it('retains distinct authority and kind provenance even when values coincide', () => {
    const { tooltip, setCursor } = harness(() => ({
      data: [[0], [25], [25], [25], [1], [800]],
      meta: timelineSeriesMeta(KEYS, 'selected-saved', 'Saved Flower · '),
    }))
    setCursor(400, 200, 0)

    const text = tooltip.textContent ?? ''
    expect(text.match(/25\.0 °C/g)).toHaveLength(3)
    expect(text).toContain('1.00 kPa')
    expect(text).toContain('800 ppm')
  })

  it('lists non-null series values with units at the cursor', () => {
    const { tooltip, setCursor } = harness()
    setCursor(400, 200, 0)

    expect(tooltip.style.display).toBe('block')
    const text = tooltip.textContent ?? ''
    expect(text).toContain('23.5 °C')
    expect(text).toContain('22.9 °C')
    expect(text).toContain('27.5 °C')
    expect(text).toContain('1.02 kPa')
    expect(text).toContain('810 ppm')
  })

  it('skips null series values without dropping the rest', () => {
    const { tooltip, setCursor } = harness()
    setCursor(400, 200, 1)

    const text = tooltip.textContent ?? ''
    expect(text).toContain('22.5 °C')
    expect(text).toContain('28.0 °C')
    expect(text).not.toContain('kPa')
  })

  it('hides when the cursor leaves the plot or no index is hit', () => {
    const { tooltip, setCursor } = harness()
    setCursor(400, 200, 0)
    expect(tooltip.style.display).toBe('block')

    setCursor(-10, -10, null)
    expect(tooltip.style.display).toBe('none')
  })
})
