import type {
  ClimateTimelineSeries,
  ControlMonitoringResponse,
  FutureProjection,
  LightTimelineSeries,
  PhotoperiodTimelinePoint,
  ProjectionPublicationResponse,
  Quality,
  RichTrajectoryEnvelope,
  TrajectorySegment,
} from '../api'

export interface ProjectionTimeline {
  readonly history: ControlMonitoringResponse | null
  readonly quality: Quality
  readonly revision: string | null
  readonly version: number | null
  readonly validUntil: Date | null
}

const PROJECTED = { origin: 'projected' as const, quality: 'estimated' as const, is_aggregated: false }

const CLIMATE_SERIES = new Map<string, { readonly name: string; readonly metric: string }>([
  ['climate.heating_setpoint_target', { name: 'Heating Setpoint', metric: 'heating_setpoint' }],
  ['climate.cooling_setpoint_target', { name: 'Cooling Setpoint', metric: 'cooling_setpoint' }],
  ['climate.vpd_setpoint_target', { name: 'VPD Setpoint', metric: 'vpd_setpoint' }],
  ['climate.co2_setpoint_target', { name: 'CO2 Setpoint', metric: 'co2_setpoint' }],
])

export function projectionTimeline(publication: ProjectionPublicationResponse): ProjectionTimeline {
  if (publication.trajectory?.revision_scope === 'saved') {
    return richProjectionTimeline(publication.trajectory)
  }
  if (publication.quality !== 'estimated' || publication.value.length === 0) {
    return { history: null, quality: publication.quality, revision: null, version: null, validUntil: null }
  }
  const first = publication.value[0]
  if (!first) return { history: null, quality: publication.quality, revision: null, version: null, validUntil: null }

  const climate = new Map<string, ClimateTimelineSeries>()
  const lights = new Map<string, LightTimelineSeries>()
  const photoperiod: PhotoperiodTimelinePoint[] = []
  for (const interval of publication.value) appendInterval(interval, climate, lights, photoperiod)
  const validUntil = publication.value.at(-1)?.valid_until ?? null
  if (validUntil) {
    for (const series of [...climate.values(), ...lights.values()]) {
      series.steps.push({
        timestamp: validUntil,
        value: null,
        provenance: { ...PROJECTED, quality: 'unavailable' },
      })
    }
  }
  if (validUntil) photoperiod.push({ timestamp: validUntil, phase: 'UNKNOWN', provenance: PROJECTED })

  return {
    history: {
      range: { start: first.valid_from, end: validUntil ?? first.valid_until },
      runtime_snapshot_version: first.version.config_version,
      cursors: [],
      flush_health: [],
      climate: [...climate.values()],
      lights: [...lights.values()],
      devices: [],
      pid: [],
      photoperiod,
    },
    quality: publication.quality,
    revision: first.version.revision,
    version: first.version.config_version,
    validUntil,
  }
}

function richProjectionTimeline(trajectory: RichTrajectoryEnvelope): ProjectionTimeline {
  const climate = new Map<string, ClimateTimelineSeries>()
  const lights = new Map<string, LightTimelineSeries>()
  const warnings = trajectory.warnings.map(({ code, detail }) => ({ code, detail }))
  for (const segment of trajectory.segments) {
    const key = `${segment.metric}:${segment.trajectory_kind}`
    const name = `${displayMetric(segment.metric)} (${segment.trajectory_kind})`
    if (segment.metric.startsWith('light')) {
      const series = lights.get(key) ?? richLightSeries(segment.metric, name, segment.trajectory_kind, warnings)
      appendRichSegment(series, segment)
      lights.set(key, series)
    } else {
      const series = climate.get(key) ?? richClimateSeries(richMetric(segment.metric), name, segment.trajectory_kind, warnings)
      appendRichSegment(series, segment)
      climate.set(key, series)
    }
  }
  const validUntil = trajectory.window.end
  for (const series of [...climate.values(), ...lights.values()]) {
    series.steps.push({ timestamp: validUntil, value: null, provenance: { ...PROJECTED, quality: 'unavailable' } })
  }
  return {
    history: {
      range: { start: trajectory.window.start, end: validUntil },
      runtime_snapshot_version: 0,
      cursors: [],
      flush_health: [],
      climate: [...climate.values()],
      lights: [...lights.values()],
      devices: [],
      pid: [],
      photoperiod: [],
    },
    quality: 'estimated',
    revision: trajectory.base_config_revision,
    version: null,
    validUntil,
  }
}

function appendRichSegment(
  series: ClimateTimelineSeries | LightTimelineSeries,
  segment: TrajectorySegment,
): void {
  const provenance = { ...PROJECTED, quality: segment.quality }
  switch (segment.shape) {
    case 'step':
      series.steps.push({ timestamp: segment.start, value: segment.value, provenance })
      return
    case 'linear':
      series.linear.push({ start: segment.start, end: segment.end, start_value: segment.start_value, end_value: segment.end_value, provenance })
      return
    case 'unavailable':
      series.steps.push({ timestamp: segment.start, value: null, provenance })
      return
    default:
      return assertNever(segment)
  }
}

function assertNever(value: never): never {
  throw new Error(`Unexpected trajectory segment: ${JSON.stringify(value)}`)
}

function displayMetric(metric: string): string {
  return metric
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter: string) => letter.toUpperCase())
}

function richMetric(metric: string): string {
  return metric.endsWith('_setpoint') ? metric : `${metric}_setpoint`
}

function appendInterval(
  interval: FutureProjection,
  climate: Map<string, ClimateTimelineSeries>,
  lights: Map<string, LightTimelineSeries>,
  photoperiod: PhotoperiodTimelinePoint[],
): void {
  for (const point of interval.series) {
    const climateDefinition = CLIMATE_SERIES.get(point.series_id.value)
    if (climateDefinition) {
      const series = climate.get(climateDefinition.metric) ?? climateSeries(climateDefinition.name)
      series.steps.push({ timestamp: point.valid_from, value: point.value, provenance: { ...PROJECTED, quality: point.quality } })
      climate.set(climateDefinition.metric, series)
      continue
    }
    const lightName = point.series_id.value.startsWith('light.intensity.')
      ? point.series_id.value.slice('light.intensity.'.length)
      : null
    if (lightName) {
      const series = lights.get(lightName) ?? lightSeries(lightName)
      series.steps.push({ timestamp: point.valid_from, value: point.value, provenance: { ...PROJECTED, quality: point.quality } })
      lights.set(lightName, series)
      continue
    }
    if (point.series_id.value === 'light.photoperiod') {
      photoperiod.push({
        timestamp: point.valid_from,
        phase: point.quality === 'estimated' && point.value === 1 ? 'SUN' : point.quality === 'estimated' ? 'MOON' : 'UNKNOWN',
        provenance: { ...PROJECTED, quality: point.quality },
      })
    }
  }
}

function richClimateSeries(
  metric: string,
  name: string,
  trajectoryKind: 'scheduled' | 'effective',
  warnings: { code: string; detail: string }[],
): ClimateTimelineSeries {
  return { name, metric, trajectory_kind: trajectoryKind, provenance: PROJECTED, warnings, points: [], steps: [], linear: [] }
}

function richLightSeries(
  metric: string,
  name: string,
  trajectoryKind: 'scheduled' | 'effective',
  warnings: { code: string; detail: string }[],
): LightTimelineSeries {
  return { name, metric, trajectory_kind: trajectoryKind, provenance: PROJECTED, warnings, points: [], steps: [], linear: [] }
}

function climateSeries(name: string): ClimateTimelineSeries {
  return { name, provenance: PROJECTED, warnings: [], points: [], steps: [], linear: [] }
}

function lightSeries(deviceName: string): LightTimelineSeries {
  return { name: `Light ${deviceName}`, provenance: PROJECTED, warnings: [], points: [], steps: [], linear: [] }
}
