import { describe, expect, it } from 'vitest'

import type { ClimateTimelineSeries, ControlMonitoringResponse, SensorSeries } from '../../api'
import type { TimeseriesPanelSpec } from '../../config'
import { alignSeriesBase, applyLiveTail } from '../alignSeries'
import type { AlignInput } from '../alignSeries.types'
import { LiveTailBuffer } from '../liveTail'
import { createPanelAlignment } from '../panelAlignment'

const START = new Date('2026-08-02T11:00:00.000Z')
const NOW = new Date('2026-08-02T12:00:00.000Z')
const LIVE_RANGE = { kind: 'live', duration: 60 * 60 * 1000 } as const
const CLIMATE_PANEL: TimeseriesPanelSpec = {
  kind: 'timeseries',
  id: 'climate',
  title: 'Climate',
  sources: ['sensor', 'climate'],
  families: ['temperature'],
  series: [],
}

function controlWithSteps(
  steps: ClimateTimelineSeries['steps'],
  end: Date
): ControlMonitoringResponse {
  return {
    range: { start: START, end },
    runtime_snapshot_version: 1,
    cursors: [],
    flush_health: [],
    climate: [
      {
        name: 'Heating Setpoint',
        provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
        projection: null,
        warnings: [],
        points: [],
        steps,
        linear: [],
      },
    ],
    lights: [],
    devices: [],
    pid: [],
    photoperiod: [],
  }
}

function input(overrides: Partial<AlignInput> = {}): AlignInput {
  const series: SensorSeries[] = [
    {
      sensor: 'dry_bulb',
      node: 'front',
      unit_family: 'celsius',
      unit: '°C',
      points: [{ timestamp: START, average: 24, minimum: 23, maximum: 25, sample_count: 1 }],
    },
  ]
  return {
    series,
    controlHistory: controlWithSteps(
      [
        {
          time: START,
          value: 22,
          provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
        },
      ],
      NOW
    ),
    projectionHistory: null,
    photoperiod: [],
    live: [],
    range: LIVE_RANGE,
    now: NOW,
    ...overrides,
  }
}

describe('live alignment', () => {
  it('keeps the base grid and reports the advancing wall clock', () => {
    // Given: a base alignment captured at the current instant.
    const nextNow = new Date(NOW.getTime() + 60_000)
    const source = input()
    const alignment = createPanelAlignment()
    const first = alignment.align({ ...source, panel: CLIMATE_PANEL })

    // When: only the wall clock advances.
    const result = alignment.align({ ...source, panel: CLIMATE_PANEL, now: nextNow })

    // Then: the grid is not re-keyed on now; only nowMs/nowIndex follow it.
    expect(alignment.counts.baseAlignments).toBe(1)
    expect(result.x).toEqual(first.x)
    expect(result.nowMs).toBe(nextNow.getTime())
    expect(result.nowIndex).toBe(first.x.length - 2)
  })

  it('appends raw tail points at their own timestamp without rebuilding the base', () => {
    // Given: a running live alignment and one raw sample in the next bucket.
    const source = input()
    const alignment = createPanelAlignment()
    alignment.align({ ...source, panel: CLIMATE_PANEL })
    const tailAt = new Date(NOW.getTime() + 1_000)
    const liveTail = new LiveTailBuffer()
    liveTail.push({ sensor: 'dry_bulb', timestamp: tailAt.getTime(), value: 24.5 })

    // When: the next alignment carries the buffer.
    const result = alignment.align({
      ...source,
      panel: CLIMATE_PANEL,
      now: tailAt,
      liveTail,
    })
    const mean = result.series.find(
      series => series.metric === 'dry_bulb' && series.role === 'mean'
    )

    // Then: one tail slot is inserted, marked raw, and the base is reused.
    expect(alignment.counts.baseAlignments).toBe(1)
    expect(result.x).toContain(tailAt.getTime())
    expect(mean?.y[result.nowIndex]).toBe(24.5)
    expect(mean?.liveTail?.[result.nowIndex]).toBe(true)
    expect(mean?.sampleCount?.[result.nowIndex]).toBeNull()
  })

  it('holds a step setpoint at an inserted sensor-tail timestamp', () => {
    // Given: an active target step before the incoming sensor update.
    const base = alignSeriesBase(input())
    const tailNow = new Date(NOW.getTime() + 1_000)

    // When: the shared grid receives a sensor-only tail timestamp.
    const result = applyLiveTail(
      base,
      { live: [{ sensor: 'dry_bulb', value: 24.5, timestamp: tailNow }] },
      tailNow
    )
    const target = result.series.find(
      series => series.metric === 'heating_setpoint' && series.role === 'step'
    )

    // Then: the active target remains held rather than blinking into a fabricated gap.
    expect(target?.y[result.nowIndex]).toBe(22)
  })

  it('keeps an explicit null step as a genuine setpoint gap', () => {
    // Given: an explicit unavailable control fact at the live timestamp.
    const unavailableNow = new Date(NOW.getTime() + 1_000)
    const base = alignSeriesBase(
      input({
        now: unavailableNow,
        controlHistory: controlWithSteps(
          [
            {
              time: START,
              value: 22,
              provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
            },
            {
              time: unavailableNow,
              value: null,
              provenance: { origin: 'recorded', quality: 'unavailable', is_aggregated: false },
            },
          ],
          unavailableNow
        ),
      })
    )

    // When: the sensor update occupies that same timestamp.
    const result = applyLiveTail(
      base,
      { live: [{ sensor: 'dry_bulb', value: 24.5, timestamp: unavailableNow }] },
      unavailableNow
    )
    const target = result.series.find(
      series => series.metric === 'heating_setpoint' && series.role === 'step'
    )

    // Then: the explicit control null remains a gap.
    expect(target?.y[result.nowIndex]).toBeNull()
  })
  it('does not let live-only bulb values recreate an empty climate series', () => {
    const panel = { ...CLIMATE_PANEL, id: 'flower-climate' }
    const emptyBulb: SensorSeries = {
      sensor: 'dry_bulb_f',
      node: 'front',
      unit_family: 'celsius',
      unit: '°C',
      points: [],
    }
    const result = createPanelAlignment().align({
      ...input({
        series: [emptyBulb],
        live: [{ sensor: 'dry_bulb_f', value: 0, timestamp: NOW }],
      }),
      panel,
    })
    expect(result.series.some(series => series.key.includes('dry_bulb_f'))).toBe(false)
  })
})
