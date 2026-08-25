import { describe, expect, it } from 'vitest'
import type { ClimateTimelineSeries, ControlMonitoringResponse, SensorSeries } from '../../api'
import type { TimeseriesPanelSpec } from '../../config'
import type { MonitoringRange } from '../../state'
import { alignSeries, alignSeriesBase, applyLiveTail } from '../alignSeries'
import { createPanelAlignment } from '../panelAlignment'
import type { AlignInput } from '../alignSeries.types'

const START = new Date('2026-08-02T11:00:00.000Z')
const NOW = new Date('2026-08-02T12:00:00.000Z')
const END = new Date('2026-08-02T13:00:00.000Z')

const CLIMATE_PANEL: TimeseriesPanelSpec = {
  kind: 'timeseries',
  id: 'climate',
  title: 'Climate',
  sources: ['sensor', 'climate'],
  families: ['temperature'],
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
    const capped = alignSeries(input({
      series: [{ ...input().series[0], points: dense }],
      range: { kind: 'fixed', start: START, end: new Date(START.getTime() + 20_100 * 1000) },
      maxPoints: 100_000,
    }))

    expect(filtered.x).not.toContain(unrelated.getTime())
    expect(filtered.bands).toHaveLength(1)
    expect(capped.x.length).toBeLessThanOrEqual(20_000)
  })
})
