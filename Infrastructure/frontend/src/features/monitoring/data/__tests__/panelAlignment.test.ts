import { describe, expect, it } from 'vitest'
import type { ClimateTimelineSeries, ControlMonitoringResponse, SensorSeries } from '../../api'
import type { TimeseriesPanelSpec } from '../../config'
import type { MonitoringRange } from '../../state'
import { alignSeries, alignSeriesBase, applyLiveTail } from '../alignSeries'
import { createPanelAlignment } from '../panelAlignment'
import type { AlignInput } from '../alignSeries.types'
import { windowBounds } from '../alignSeries.grid'

const START = new Date('2026-08-02T11:00:00.000Z')
const NOW = new Date('2026-08-02T12:00:00.000Z')
const END = new Date('2026-08-02T13:00:00.000Z')
const FUTURE = new Date(NOW.getTime() + 30_000)

const CLIMATE_PANEL: TimeseriesPanelSpec = {
  kind: 'timeseries',
  id: 'climate',
  title: 'Climate',
  sources: ['sensor', 'climate'],
  families: ['temperature'],
  series: [],
}

const CO2_PANEL: TimeseriesPanelSpec = {
  kind: 'timeseries',
  id: 'co2',
  title: 'CO2',
  sources: ['sensor', 'climate'],
  families: ['co2'],
  series: [],
}

function fixedRange(): MonitoringRange {
  return { kind: 'fixed', start: START, end: END }
}

function controls(points: Array<{ timestamp: Date; value: number | null }>): ControlMonitoringResponse {
  const climate: ClimateTimelineSeries = {
    name: 'Heating Setpoint',
    provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
    projection: null,
    warnings: [],
    points: points.map((point) => ({
      timestamp: point.timestamp,
      value: point.value,
      nominal_value: point.value,
      metric: 'heating_setpoint',
      provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
    })),
    steps: [],
    linear: [],
  }
  return {
    range: { start: START, end: END },
    runtime_snapshot_version: 1,
    cursors: [],
    flush_health: [],
    climate: [climate],
    lights: [],
    devices: [],
    pid: [],
    photoperiod: [],
  }
}

function input(overrides: Partial<AlignInput> = {}): AlignInput {
  const series: SensorSeries[] = [{
    sensor: 'dry_bulb',
    node: 'front',
    unit_family: 'celsius',
    unit: '°C',
    points: [{ timestamp: START, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 1 }],
  }]
  return {
    series,
    controlHistory: controls([{ timestamp: START, value: 22 }]),
    projectionHistory: null,
    photoperiod: [],
    live: [],
    range: fixedRange(),
    now: NOW,
    ...overrides,
  }
}

describe('panel alignment', () => {
  it('uses the fulfilled live range end as the viewport end bound', () => {
    // Given: a one-hour live range fulfilled at a captured instant.
    const fulfilledEnd = new Date('2026-08-02T15:00:00.000Z')
    const range: MonitoringRange = { kind: 'live', duration: 3_600_000 }

    // When: its bounds are reconstructed for alignment.
    const bounds = windowBounds(range, fulfilledEnd)

    // Then: the visible interval exactly matches the captured request interval.
    expect(bounds).toEqual({ start: fulfilledEnd.getTime() - 3_600_000, end: fulfilledEnd.getTime() })
  })

  it('inserts live sensor values before future projection timestamps', () => {
    const source = input({
      projectionHistory: controls([{ timestamp: FUTURE, value: 23 }]),
      range: { kind: 'live', duration: 60 * 60 * 1000 },
    })
    const alignment = createPanelAlignment()
    const liveNow = new Date(NOW.getTime() + 1_000)

    alignment.align({ ...source, panel: CLIMATE_PANEL })
    const result = alignment.align({
      ...source,
      panel: CLIMATE_PANEL,
      live: [{ sensor: 'dry_bulb', value: 24.8, timestamp: liveNow }],
      now: liveNow,
    })
    const mean = result.series.find((series) => series.metric === 'dry_bulb' && series.role === 'mean')

    expect(result.x).toContain(liveNow.getTime())
    expect(result.x.indexOf(liveNow.getTime())).toBeLessThan(result.x.indexOf(FUTURE.getTime()))
    expect(result.x).toEqual([...result.x].sort((left, right) => left - right))
    expect(result.series.every((series) => series.y.length === result.x.length)).toBe(true)
    expect(mean?.y[result.nowIndex]).toBe(24.8)
  })

  it('preserves prior live points when the control-history reference changes', () => {
    const source = input({ range: { kind: 'live', duration: 60 * 60 * 1000 } })
    const alignment = createPanelAlignment()
    const firstNow = new Date(NOW.getTime() + 1_000)
    const secondNow = new Date(NOW.getTime() + 2_000)

    alignment.align({
      ...source,
      panel: CLIMATE_PANEL,
      live: [{ sensor: 'dry_bulb', value: 24.8, timestamp: firstNow }],
      now: firstNow,
    })
    const result = alignment.align({
      ...source,
      panel: CLIMATE_PANEL,
      controlHistory: controls([{ timestamp: firstNow, value: 23 }]),
      live: [{ sensor: 'dry_bulb', value: 24.9, timestamp: secondNow }],
      now: secondNow,
    })
    const mean = result.series.find((series) => series.metric === 'dry_bulb' && series.role === 'mean')
    const heating = result.series.find((series) => series.metric === 'heating_setpoint')

    expect(result.x).toContain(firstNow.getTime())
    expect(result.x).toContain(secondNow.getTime())
    expect(mean?.y[result.x.indexOf(firstNow.getTime())]).toBe(24.8)
    expect(mean?.y[result.nowIndex]).toBe(24.9)
    expect(heating?.y[result.x.indexOf(firstNow.getTime())]).toBe(23)
  })

  it('preserves legacy series, bands, provenance, and null gaps', () => {
    const source = input({
      controlHistory: controls([
        { timestamp: START, value: 22 },
        { timestamp: NOW, value: null },
        { timestamp: END, value: 23 },
      ]),
      live: [{ sensor: 'dry_bulb', value: 24.8, timestamp: NOW }],
    })

    const legacy = alignSeries(source)
    const split = applyLiveTail(alignSeriesBase(source), source.live, source.now)
    const panel = createPanelAlignment().align({ ...source, panel: CLIMATE_PANEL })

    expect(split).toEqual(legacy)
    expect(panel).toEqual(legacy)
  })

  it('uses one base alignment for 120 live ticks and bounded tail updates', () => {
    const source = input({ range: { kind: 'live', duration: 60 * 60 * 1000 } })
    const alignment = createPanelAlignment()
    let result = alignment.align({ ...source, panel: CLIMATE_PANEL })
    for (let tick = 1; tick <= 120; tick++) {
      const now = new Date(NOW.getTime() + tick * 1000)
      result = alignment.align({
        ...source,
        panel: CLIMATE_PANEL,
        live: [{ sensor: 'dry_bulb', value: 24 + tick / 10, timestamp: now }],
        now,
      })
    }

    const mean = result.series.find((series) => series.metric === 'dry_bulb' && series.role === 'mean')
    expect(alignment.counts).toEqual({ baseAlignments: 1, liveTailUpdates: 120 })
    expect(mean?.y[result.nowIndex]).toBe(36)
  })

  it('invalidates the base exactly once when control history changes', () => {
    const source = input()
    const alignment = createPanelAlignment()
    alignment.align({ ...source, panel: CLIMATE_PANEL })
    const changed = { ...source, controlHistory: controls([{ timestamp: START, value: 23 }]) }
    alignment.align({ ...changed, panel: CLIMATE_PANEL })
    alignment.align({ ...changed, panel: CLIMATE_PANEL })

    expect(alignment.counts.baseAlignments).toBe(2)
  })

  it('routes co2_setpoint to the co2 family panel, not temperature', () => {
    const co2Climate: ClimateTimelineSeries = {
      name: 'CO2 Setpoint',
      provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
      projection: null,
      warnings: [],
      points: [{
        timestamp: START,
        value: 600,
        nominal_value: 600,
        metric: 'co2_setpoint',
        provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
      }],
      steps: [],
      linear: [],
    }
    const source: AlignInput = {
      ...input(),
      controlHistory: {
        ...input().controlHistory,
        climate: [co2Climate],
      } as ControlMonitoringResponse,
    }

    const co2Panel = createPanelAlignment().align({ ...source, panel: CO2_PANEL })
    const co2Point = co2Panel.series.find((s) => s.metric === 'co2_setpoint')
    expect(co2Point).toBeDefined()
    expect(co2Point?.family).toBe('co2')

    const tempPanel = createPanelAlignment().align({ ...source, panel: CLIMATE_PANEL })
    expect(tempPanel.series.some((s) => s.metric === 'co2_setpoint')).toBe(false)
    const tempPoint = tempPanel.series.find((s) => s.family === 'temperature' && s.metric.includes('setpoint'))
    expect(tempPoint).toBeUndefined()
  })

  it('filters unrelated inputs before alignment and caps legacy buffers at 20,000 points', () => {
    const unrelated = new Date(NOW.getTime() + 30_000)
    const source = input({
      series: [
        ...input().series,
        {
          sensor: 'room_pressure',
          node: 'front',
          unit_family: 'hpa',
          unit: 'hPa',
          points: [{ timestamp: unrelated, average: 1010, minimum: 1009, maximum: 1011, sample_count: 1 }],
        },
      ],
    })
    const filtered = createPanelAlignment().align({ ...source, panel: CLIMATE_PANEL })
    const dense = Array.from({ length: 20_100 }, (_, index) => ({
      timestamp: new Date(START.getTime() + index * 1000), average: 24, minimum: 23, maximum: 25, sample_count: 1,
    }))
    const legacyCapped = alignSeries(input({
      series: [{ ...input().series[0], points: dense }],
      range: { kind: 'fixed', start: START, end: new Date(START.getTime() + 20_100 * 1000) },
    }))
    const budgeted = alignSeries(input({
      series: [{ ...input().series[0], points: dense }],
      range: { kind: 'fixed', start: START, end: new Date(START.getTime() + 20_100 * 1000) },
      maxPoints: 25_000,
    }))

    expect(filtered.x).not.toContain(unrelated.getTime())
    expect(filtered.bands).toHaveLength(1)
    expect(legacyCapped.x.length).toBeLessThanOrEqual(20_000)
    expect(budgeted.x.length).toBeGreaterThan(20_000)
  })
})
