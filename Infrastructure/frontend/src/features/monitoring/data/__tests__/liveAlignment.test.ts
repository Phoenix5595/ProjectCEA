import { describe, expect, it } from 'vitest'
import type { ClimateTimelineSeries, ControlMonitoringResponse, SensorSeries } from '../../api'
import type { TimeseriesPanelSpec } from '../../config'
import { alignSeriesBase, applyLiveTail } from '../alignSeries'
import { createPanelAlignment } from '../panelAlignment'
import type { AlignInput } from '../alignSeries.types'

const START = new Date('2026-08-02T11:00:00.000Z')
const NOW = new Date('2026-08-02T12:00:00.000Z')
const LIVE_RANGE = { kind: 'live', duration: 60 * 60 * 1000 } as const
const CLIMATE_PANEL: TimeseriesPanelSpec = {
  kind: 'timeseries', id: 'climate', title: 'Climate', sources: ['sensor', 'climate'], families: ['temperature'], series: [],
}

function controlWithSteps(steps: ClimateTimelineSeries['steps'], end: Date): ControlMonitoringResponse {
  return {
    range: { start: START, end }, runtime_snapshot_version: 1, cursors: [], flush_health: [],
    climate: [{
      name: 'Heating Setpoint',
      provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false }, projection: null, warnings: [], points: [], steps, linear: [],
    }],
    lights: [], devices: [], pid: [], photoperiod: [],
  }
}

function input(overrides: Partial<AlignInput> = {}): AlignInput {
  const series: SensorSeries[] = [{
    sensor: 'dry_bulb', node: 'front', unit_family: 'celsius', unit: '°C',
    points: [{ timestamp: START, average: 24, minimum: 23, maximum: 25, sample_count: 1 }],
  }]
  return {
    series,
    controlHistory: controlWithSteps([{ timestamp: START, value: 22, provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false } }], NOW),
    projectionHistory: null,
    photoperiod: [], live: [], range: LIVE_RANGE, now: NOW, ...overrides,
  }
}

describe('live alignment', () => {
  it('rolls the future horizon when only a sensor tail update advances now', () => {
    // Given: a projection that extends beyond two live horizons.
    const nextNow = new Date(NOW.getTime() + 60_000)
    const source = input({ projectionHistory: controlWithSteps([], new Date(nextNow.getTime() + 10 * 60_000)) })
    const alignment = createPanelAlignment()

    // When: a sensor-only update advances the current instant.
    alignment.align({ ...source, panel: CLIMATE_PANEL })
    const result = alignment.align({
      ...source,
      panel: CLIMATE_PANEL,
      now: nextNow,
      live: [{ sensor: 'dry_bulb', value: 24.5, timestamp: nextNow }],
    })

    // Then: every live frame has the same [now-duration, now+duration/3] horizon.
    expect(result.x[0]).toBe(nextNow.getTime() - LIVE_RANGE.duration)
    expect(result.x.at(-1)).toBe(nextNow.getTime() + LIVE_RANGE.duration / 3)
    expect(result.x[result.nowIndex]).toBe(nextNow.getTime())
  })

  it('holds a step setpoint at an inserted sensor-tail timestamp', () => {
    // Given: an active target step before the incoming sensor update.
    const base = alignSeriesBase(input())
    const tailNow = new Date(NOW.getTime() + 1_000)

    // When: the shared grid receives a sensor-only tail timestamp.
    const result = applyLiveTail(base, [{ sensor: 'dry_bulb', value: 24.5, timestamp: tailNow }], tailNow)
    const target = result.series.find((series) => series.metric === 'heating_setpoint' && series.role === 'step')

    // Then: the active target remains held rather than blinking into a fabricated gap.
    expect(target?.y[result.nowIndex]).toBe(22)
  })

  it('keeps an explicit null step as a genuine setpoint gap', () => {
    // Given: an explicit unavailable control fact at the live timestamp.
    const unavailableNow = new Date(NOW.getTime() + 1_000)
    const base = alignSeriesBase(input({
      now: unavailableNow,
      controlHistory: controlWithSteps([
        { timestamp: START, value: 22, provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false } },
        { timestamp: unavailableNow, value: null, provenance: { origin: 'recorded', quality: 'unavailable', is_aggregated: false } },
      ], unavailableNow),
    }))

    // When: the sensor update occupies that same timestamp.
    const result = applyLiveTail(base, [{ sensor: 'dry_bulb', value: 24.5, timestamp: unavailableNow }], unavailableNow)
    const target = result.series.find((series) => series.metric === 'heating_setpoint' && series.role === 'step')

    // Then: the explicit control null remains a gap.
    expect(target?.y[result.nowIndex]).toBeNull()
  })
})
