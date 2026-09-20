import { describe, expect, it } from 'vitest'
import type uPlot from 'uplot'
import {
  formatTooltipValue,
  unitTooltipPlugin,
  type TimelineTooltipState,
} from '../unitTooltipPlugin'
import { timelineSeriesMeta } from '../timelineOptions'
import type { EnvelopeSeriesKey } from '../envelopeSeries'

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
    meta: timelineSeriesMeta(KEYS),
  }
}

function harness() {
  const over = document.createElement('div')
  Object.defineProperty(over, 'clientWidth', { value: 1440 })
  Object.defineProperty(over, 'clientHeight', { value: 400 })
  document.body.appendChild(over)
  const plugin = unitTooltipPlugin(state)
  const u = {
    over,
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
