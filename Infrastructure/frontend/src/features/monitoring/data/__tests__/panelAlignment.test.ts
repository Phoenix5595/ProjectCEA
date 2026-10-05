import { describe, expect, it } from 'vitest'

import type {
  ClimateTimelineSeries,
  ControlMonitoringResponse,
  DeviceTimelineSeries,
  LightTimelineSeries,
  PidTimelineSeries,
  SensorSeries,
} from '../../api'
import { flowerManifest, vegManifest } from '../../config'

import type { TimeseriesPanelSpec } from '../../config'
import type { MonitoringRange } from '../../state'
import { alignSeries, alignSeriesBase, applyLiveTail } from '../alignSeries'
import { windowBounds } from '../alignSeries.grid'
import { seriesKey } from '../alignSeries.types'
import type {
  AlignInput,
  AlignedData,
  AlignedSeries,
  SeriesKind,
  SeriesRole,
} from '../alignSeries.types'
import { splitChartGroups } from '../../pages/chartGroups'
import { lightValueAt } from '../lightTrajectory'
import { createPanelAlignment } from '../panelAlignment'

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

function controls(
  points: Array<{ timestamp: Date; value: number | null }>
): ControlMonitoringResponse {
  const climate: ClimateTimelineSeries = {
    name: 'Heating Setpoint',
    provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
    projection: null,
    warnings: [],
    points: points.map(point => ({
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
  const series: SensorSeries[] = [
    {
      sensor: 'dry_bulb',
      node: 'front',
      unit_family: 'celsius',
      unit: '°C',
      points: [{ timestamp: START, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 1 }],
    },
  ]
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
  it('allocates one tenth of the visible live window after its contained now boundary', () => {
    // Given: a one-hour live range fulfilled at a captured instant.
    const fulfilledEnd = new Date('2026-08-02T15:00:00.000Z')
    const historicalDuration = 3_600_000
    const range: MonitoringRange = { kind: 'live', duration: historicalDuration }

    // When: its bounds are reconstructed for alignment.
    const bounds = windowBounds(range, fulfilledEnd)

    // Then: H history and H/9 future make future exactly 10% of the displayed interval.
    const historicalWidth = fulfilledEnd.getTime() - bounds.start
    const futureWidth = bounds.end - fulfilledEnd.getTime()
    const displayedWidth = bounds.end - bounds.start
    expect(historicalWidth).toBe(historicalDuration)
    expect(futureWidth).toBe(historicalDuration / 9)
    expect(futureWidth * 10).toBe(displayedWidth)
  })

  it('leaves a fully historical fixed window unchanged at its now boundary', () => {
    // Given: a fixed range that ends exactly at now.
    const range: MonitoringRange = { kind: 'fixed', start: START, end: NOW }

    // When: its bounds are reconstructed for alignment.
    const bounds = windowBounds(range, NOW)

    // Then: its inclusive chart boundary receives no future extension.
    expect(bounds).toEqual({ start: START.getTime(), end: NOW.getTime() })
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
    const mean = result.series.find(
      series => series.metric === 'dry_bulb' && series.role === 'mean'
    )

    expect(result.x).toContain(liveNow.getTime())
    expect(result.x.indexOf(liveNow.getTime())).toBeLessThan(result.x.indexOf(FUTURE.getTime()))
    expect(result.x).toEqual([...result.x].sort((left, right) => left - right))
    expect(result.series.every(series => series.y.length === result.x.length)).toBe(true)
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
    const mean = result.series.find(
      series => series.metric === 'dry_bulb' && series.role === 'mean'
    )
    const heating = result.series.find(series => series.metric === 'heating_setpoint')

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

  it('rebuilds the rolling live frame for 120 ticks and bounds tail updates', () => {
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

    const mean = result.series.find(
      series => series.metric === 'dry_bulb' && series.role === 'mean'
    )
    expect(alignment.counts).toEqual({ baseAlignments: 121, liveTailUpdates: 120 })
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
      points: [
        {
          timestamp: START,
          value: 600,
          nominal_value: 600,
          metric: 'co2_setpoint',
          provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
        },
      ],
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
    const co2Point = co2Panel.series.find(s => s.metric === 'co2_setpoint')
    expect(co2Point).toBeDefined()
    expect(co2Point?.family).toBe('co2')

    const tempPanel = createPanelAlignment().align({ ...source, panel: CLIMATE_PANEL })
    expect(tempPanel.series.some(s => s.metric === 'co2_setpoint')).toBe(false)
    const tempPoint = tempPanel.series.find(
      s => s.family === 'temperature' && s.metric.includes('setpoint')
    )
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
          points: [
            { timestamp: unrelated, average: 1010, minimum: 1009, maximum: 1011, sample_count: 1 },
          ],
        },
      ],
    })
    const filtered = createPanelAlignment().align({ ...source, panel: CLIMATE_PANEL })
    const dense = Array.from({ length: 20_100 }, (_, index) => ({
      timestamp: new Date(START.getTime() + index * 1000),
      average: 24,
      minimum: 23,
      maximum: 25,
      sample_count: 1,
    }))
    const legacyCapped = alignSeries(
      input({
        series: [{ ...input().series[0], points: dense }],
        range: { kind: 'fixed', start: START, end: new Date(START.getTime() + 20_100 * 1000) },
      })
    )
    const budgeted = alignSeries(
      input({
        series: [{ ...input().series[0], points: dense }],
        range: { kind: 'fixed', start: START, end: new Date(START.getTime() + 20_100 * 1000) },
        maxPoints: 25_000,
      })
    )

    expect(filtered.x).not.toContain(unrelated.getTime())
    expect(filtered.bands).toHaveLength(1)
    expect(legacyCapped.x.length).toBeLessThanOrEqual(20_000)
    expect(budgeted.x.length).toBeGreaterThan(20_000)
  })
  it('keeps explicit climate setpoints out of both room equipment panels', () => {
    const history = controls([{ timestamp: START, value: 22 }])
    const heating = history.climate[0]
    if (heating === undefined) throw new Error('heating fixture is required')
    heating.name = 'heating'
    const source = input({ controlHistory: history })

    for (const room of [
      { manifest: flowerManifest, equipmentId: 'flower-systems', climateId: 'flower-climate' },
      { manifest: vegManifest, equipmentId: 'veg-systems', climateId: 'veg-climate' },
    ]) {
      const equipmentPanel = room.manifest.panels.find(
        (panel): panel is TimeseriesPanelSpec =>
          panel.kind === 'timeseries' && panel.id === room.equipmentId
      )
      const climatePanel = room.manifest.panels.find(
        (panel): panel is TimeseriesPanelSpec =>
          panel.kind === 'timeseries' && panel.id === room.climateId
      )
      if (equipmentPanel === undefined || climatePanel === undefined) {
        throw new Error('room climate and equipment panels are required')
      }

      const equipment = createPanelAlignment().align({ ...source, panel: equipmentPanel })
      expect(equipment.series.some(series => series.source === 'climate')).toBe(false)

      const climate = createPanelAlignment().align({ ...source, panel: climatePanel })
      const target = climate.series.find(series => series.metric === 'heating_setpoint')
      expect(target?.y[climate.x.indexOf(START.getTime())]).toBe(22)
    }
  })

  it('suppresses light state/PID overlays but keeps non-light equipment measurements', () => {
    const provenance = { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false }
    const projectedProvenance = {
      origin: 'projected' as const,
      quality: 'estimated' as const,
      is_aggregated: false,
    }
    const unavailableProvenance = { ...projectedProvenance, quality: 'unavailable' as const }
    const device = (deviceName: string): DeviceTimelineSeries => ({
      name: deviceName,
      provenance,
      warnings: [],
      points: [
        {
          timestamp: START,
          provenance,
          device_name: deviceName,
          device_state: 1,
          device_mode: 'AUTO',
          control_reason: 'schedule',
        },
      ],
    })
    const pid = (deviceName: string): PidTimelineSeries => ({
      name: deviceName,
      provenance,
      warnings: [],
      points: [
        {
          timestamp: START,
          provenance,
          device_name: deviceName,
          pid_output: 15,
          duty_cycle_percent: 40,
        },
      ],
    })

    const history = controls([])
    history.range = { start: START, end: NOW }
    history.lights = [
      {
        name: 'light_f_1',
        metric: 'light_f_1',
        provenance,
        warnings: [],
        points: [
          {
            timestamp: START,
            value: 40,
            nominal_value: 40,
            device_name: 'light_f_1',
            provenance,
          },
        ],
        steps: [],
        linear: [],
      },
    ]
    history.devices = [device('light_f_1'), device('light_f_2'), device('heater_f_1')]
    history.pid = [pid('light_f_1'), pid('light_f_2'), pid('heater_f_1')]
    const projection = controls([])
    projection.range = { start: NOW, end: END }
    const rampEnd = new Date(FUTURE.getTime() + 30_000)
    const gapStart = new Date(rampEnd.getTime() + 30_000)
    const effectiveLight: LightTimelineSeries = {
      name: 'Effective projection',
      metric: 'light.intensity.light_f_1',
      trajectory_kind: 'effective',
      provenance: projectedProvenance,
      warnings: [],
      points: [],
      steps: [
        { timestamp: NOW, value: 40, provenance: projectedProvenance },
        { timestamp: gapStart, value: null, provenance: unavailableProvenance },
        {
          timestamp: new Date(gapStart.getTime() + 30_000),
          value: 0,
          provenance: projectedProvenance,
        },
      ],
      linear: [
        {
          start: FUTURE,
          end: rampEnd,
          start_value: 40,
          end_value: 80,
          provenance: projectedProvenance,
        },
      ],
    }
    const scheduledLight: LightTimelineSeries = {
      ...effectiveLight,
      name: 'Scheduled projection',
      metric: 'light_f_1',
      trajectory_kind: 'scheduled',
      steps: [{ timestamp: NOW, value: 95, provenance: projectedProvenance }],
      linear: [],
    }
    projection.lights = [effectiveLight, scheduledLight]
    const equipmentPanel = flowerManifest.panels.find(
      (panel): panel is TimeseriesPanelSpec =>
        panel.kind === 'timeseries' && panel.id === 'flower-systems'
    )
    if (equipmentPanel === undefined) throw new Error('Flower equipment panel is required')

    const aligned = createPanelAlignment().align({
      ...input({
        controlHistory: history,
        projectionHistory: projection,
        lightRegistry: [
          { device_name: 'light_f_1', display_name: 'Chilled Front QA', per_room_index: 1 },
          { device_name: 'light_f_2', display_name: 'Renamed', per_room_index: 2 },
        ],
      }),
      panel: equipmentPanel,
    })

    const intensities = aligned.series.filter(series => series.source === 'light')
    expect(intensities.map(series => series.key)).toEqual([
      seriesKey('light', 'light_f_1', 'linear'),
    ])
    const intensity = intensities[0]
    expect(intensity?.label).toBe('Chilled Front QA - Intensity')
    expect(lightValueAt(intensity?.lightTrajectory ?? [], NOW.getTime())).toBe(40)
    expect(lightValueAt(intensity?.lightTrajectory ?? [], FUTURE.getTime() + 15_000)).toBe(60)
    expect(lightValueAt(intensity?.lightTrajectory ?? [], gapStart.getTime() + 10_000)).toBeNull()

    expect(aligned.series.some(series => series.source === 'device' && series.metric === 'light_f_1')).toBe(
      false
    )
    expect(aligned.series.some(series => series.source === 'device' && series.metric === 'light_f_2')).toBe(
      false
    )
    expect(aligned.series.some(series => series.source === 'pid' && series.metric === 'light_f_1')).toBe(
      false
    )
    expect(aligned.series.some(series => series.source === 'pid' && series.metric === 'light_f_2')).toBe(
      false
    )
    expect(aligned.series.some(series => series.source === 'device' && series.metric === 'heater_f_1')).toBe(
      true
    )
    expect(
      aligned.series.some(
        series =>
          series.source === 'pid' && series.metric === 'heater_f_1_pid' && series.role === 'pid_output'
      )
    ).toBe(true)
    expect(
      aligned.series.some(
        series => series.source === 'pid' && series.metric === 'heater_f_1_pid' && series.role === 'duty'
      )
    ).toBe(true)
    expect(aligned.series.some(series => series.source === 'device' && series.role === 'duty')).toBe(false)
  })

  it('filters light state/PID overlays in the chart-group fallback', () => {
    const makeSeries = (
      source: 'light' | 'device' | 'pid',
      metric: string,
      kind: SeriesKind,
      role: SeriesRole
    ): AlignedSeries => ({
      key: seriesKey(source, metric, role),
      label: metric,
      kind,
      source,
      metric,
      family: source === 'light' ? 'light' : 'device',
      role,
      y: [],
      origin: 'recorded',
      quality: 'exact',
      isAggregated: false,
    })
    const aligned: AlignedData = {
      x: [],
      series: [
        makeSeries('light', 'light_f_1', 'linear', 'linear'),
        makeSeries('device', 'light_f_1', 'step', 'state'),
        makeSeries('pid', 'light_f_2_pid', 'point', 'duty'),
        makeSeries('device', 'heater_f_1', 'step', 'state'),
        makeSeries('pid', 'heater_f_1_pid', 'point', 'pid_output'),
      ],
      bands: [],
      photoperiod: [],
      nowIndex: -1,
      aggregated: false,
    }

    const groups = splitChartGroups(flowerManifest, aligned, [
      { device_name: 'light_f_2', display_name: null, per_room_index: 2 },
    ])

    expect(groups.device.series.map(series => series.key)).toEqual([
      seriesKey('light', 'light_f_1', 'linear'),
      seriesKey('device', 'heater_f_1', 'state'),
      seriesKey('pid', 'heater_f_1_pid', 'pid_output'),
    ])
  })

})
