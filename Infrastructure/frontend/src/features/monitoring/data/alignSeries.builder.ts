/**
 * Series and band builders for the alignment layer.
 *
 * Produces `AlignedSeries` and `AlignedBand` objects from aligned y arrays,
 * chart families, manifest specs, and provenance metadata.
 */
import type { UnitFamily } from '../api'
import type { SeriesSpec } from '../config'
import type { ChartFamily } from '../charts/options/family'
import { alignControlPoints, alignDeviceStates, alignLinear, alignPid } from './alignSeries.series'
import type {
  AlignedBand,
  AlignedSeries,
  MutableSeriesPresentation,
  NormControlSeries,
  NormDeviceSeries,
  NormPidSeries,
  SeriesKey,
  SeriesKind,
  SeriesPresentation,
  SeriesRole,
  SeriesSource,
} from './alignSeries.types'
import { seriesKey } from './alignSeries.types'
import type { Origin, Quality } from '../api'

const NODE_SUFFIX: Record<string, string> = {
  _f: 'front',
  _b: 'back',
  _v: 'main',
}

export function nodeFromSensor(sensor: string): string | undefined {
  for (const [suffix, node] of Object.entries(NODE_SUFFIX)) {
    if (sensor.endsWith(suffix)) return node
  }
  return undefined
}

export function familyForUnit(unitFamily: UnitFamily): ChartFamily {
  switch (unitFamily) {
    case 'celsius':
      return 'temperature'
    case 'kpa':
      return 'vpd'
    case 'ppm':
      return 'co2'
    case 'hpa':
      return 'pressure'
    case 'percent':
      return 'rh'
    case 'mm':
      return 'device'
  }
}

export function controlFamily(cs: NormControlSeries): ChartFamily {
  if (cs.kind === 'light') return 'light'
  const m = cs.metric
  if (m.includes('setpoint')) {
    if (m.includes('vpd')) return 'vpd'
    if (m.includes('humid')) return 'rh'
    return 'temperature'
  }
  if (m.includes('light')) return 'light'
  if (m.includes('vpd')) return 'vpd'
  if (m.includes('co2')) return 'co2'
  if (m.includes('rh') || m.includes('humid')) return 'rh'
  if (m.includes('pressure')) return 'pressure'
  if (m.includes('temp')) return 'temperature'
  return 'device'
}

export function findSpec(seriesSpecs: SeriesSpec[] | undefined, name: string): SeriesSpec | undefined {
  if (seriesSpecs === undefined) return undefined
  return seriesSpecs.find((s) => s.name === name || s.displayName === name)
}

export function presentationFromSpec(spec: SeriesSpec | undefined): SeriesPresentation | undefined {
  if (spec === undefined) return undefined
  const { displayName, color, lineStyle, lineWidth, decimals, softMin, softMax } = spec
  const p: MutableSeriesPresentation = {}
  if (displayName !== undefined) p.label = displayName
  if (color !== undefined) p.color = color
  if (lineStyle !== undefined) {
    if (lineStyle === 'dot') p.dash = [0, 5]
    else if (lineStyle === 'dash') p.dash = [4, 4]
  }
  if (lineWidth !== undefined) p.lineWidth = lineWidth
  if (decimals !== undefined) p.decimals = decimals
  if (softMin !== undefined) p.softMin = softMin
  if (softMax !== undefined) p.softMax = softMax
  if (Object.keys(p).length === 0) return undefined
  return Object.freeze(p as SeriesPresentation)
}

export function buildSensorSeries(
  metric: string,
  family: ChartFamily,
  mean: (number | null)[],
  min: (number | null)[],
  max: (number | null)[],
  isAggregated: boolean,
  node?: string,
  unit?: string,
  unitFamily?: UnitFamily,
  presentation?: SeriesPresentation,
): AlignedSeries[] {
  const base = { kind: 'sensor' as const, metric, family, isAggregated, node, unit, unitFamily }
  return [
    mkSeries({ ...base, key: seriesKey('sensor', metric, 'mean'), label: metric, role: 'mean', y: mean, origin: 'recorded', quality: 'exact', presentation }),
    mkSeries({ ...base, key: seriesKey('sensor', metric, 'min'), label: `${metric} min`, role: 'min', y: min, origin: 'recorded', quality: 'exact' }),
    mkSeries({ ...base, key: seriesKey('sensor', metric, 'max'), label: `${metric} max`, role: 'max', y: max, origin: 'recorded', quality: 'exact' }),
  ]
}

export function bandForSensor(metric: string): AlignedBand {
  return {
    key: seriesKey('sensor', metric, 'band'),
    minKey: seriesKey('sensor', metric, 'min'),
    maxKey: seriesKey('sensor', metric, 'max'),
  }
}

export function buildControlSeries(
  cs: NormControlSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
  presentation?: SeriesPresentation,
): AlignedSeries[] {
  const source: SeriesSource = cs.kind
  const family = controlFamily(cs)
  const isAgg = aggregated || cs.seriesIsAggregated
  const out: AlignedSeries[] = [
    mkSeries({
      key: seriesKey(source, cs.metric, 'point'),
      label: cs.name,
      kind: 'point',
      metric: cs.metric,
      role: 'point',
      family,
      y: alignControlPoints(cs, x, start, end, aggregated),
      origin: cs.seriesOrigin,
      quality: cs.seriesQuality,
      isAggregated: isAgg,
      presentation,
    }),
  ]
  if (cs.steps.length > 0) {
    out.push(
      mkSeries({
        key: seriesKey(source, cs.metric, 'step'),
        label: `${cs.name} (step)`,
        kind: 'step',
        metric: cs.metric,
        role: 'step',
        family,
        y: alignDeviceStates(cs.steps, x, start, end, aggregated),
        origin: cs.seriesOrigin,
        quality: cs.seriesQuality,
        isAggregated: isAgg,
        presentation,
      }),
    )
  }
  if (cs.linear.length > 0) {
    out.push(
      mkSeries({
        key: seriesKey(source, cs.metric, 'linear'),
        label: `${cs.name} (ramp)`,
        kind: 'linear',
        metric: cs.metric,
        role: 'linear',
        family,
        y: alignLinear(cs.linear, x, start, end, aggregated),
        origin: cs.seriesOrigin,
        quality: cs.seriesQuality,
        isAggregated: isAgg,
        presentation,
      }),
    )
  }
  return out
}

export function buildDeviceSeries(
  ds: NormDeviceSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
  presentation?: SeriesPresentation,
): AlignedSeries[] {
  const family: ChartFamily = 'device'
  const isAgg = aggregated || ds.seriesIsAggregated
  const out: AlignedSeries[] = [
    mkSeries({
      key: seriesKey('device', ds.metric, 'state'),
      label: `${ds.name} - State`,
      kind: 'step',
      metric: ds.metric,
      role: 'state',
      family,
      y: alignDeviceStates(ds.states, x, start, end, aggregated),
      origin: ds.seriesOrigin,
      quality: ds.seriesQuality,
      isAggregated: isAgg,
      unit: '%',
      unitFamily: 'percent',
      presentation,
    }),
  ]
  if (ds.duties.length > 0) {
    out.push(
      mkSeries({
        key: seriesKey('device', ds.metric, 'duty'),
        label: `${ds.name} - Duty Cycle`,
        kind: 'point',
        metric: ds.metric,
        role: 'duty',
        family,
        y: alignPid(ds.duties, x, start, end, aggregated),
        origin: ds.seriesOrigin,
        quality: ds.seriesQuality,
        isAggregated: isAgg,
        unit: '%',
        unitFamily: 'percent',
        presentation,
      }),
    )
  }
  return out
}

export function buildPidSeries(
  ps: NormPidSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
  presentation?: SeriesPresentation,
): AlignedSeries[] {
  const family: ChartFamily = 'device'
  const isAgg = aggregated || ps.seriesIsAggregated
  const out: AlignedSeries[] = []
  if (ps.pidOutputs.length > 0) {
    out.push(
      mkSeries({
        key: seriesKey('pid', ps.metric, 'pid_output'),
        label: `${ps.name} - PID Output`,
        kind: 'point',
        metric: ps.metric,
        role: 'pid_output',
        family,
        y: alignPid(ps.pidOutputs, x, start, end, aggregated),
        origin: ps.seriesOrigin,
        quality: ps.seriesQuality,
        isAggregated: isAgg,
        unit: '%',
        unitFamily: 'percent',
        presentation,
      }),
    )
  }
  if (ps.dutyCycles.length > 0) {
    out.push(
      mkSeries({
        key: seriesKey('pid', ps.metric, 'duty'),
        label: `${ps.name} - Duty Cycle`,
        kind: 'point',
        metric: ps.metric,
        role: 'duty',
        family,
        y: alignPid(ps.dutyCycles, x, start, end, aggregated),
        origin: ps.seriesOrigin,
        quality: ps.seriesQuality,
        isAggregated: isAgg,
        unit: '%',
        unitFamily: 'percent',
        presentation,
      }),
    )
  }
  return out
}

interface MkSeriesInput {
  key: SeriesKey
  label: string
  kind: SeriesKind
  metric: string
  role: SeriesRole
  family: ChartFamily
  y: (number | null)[]
  origin: Origin
  quality: Quality
  isAggregated: boolean
  node?: string
  unit?: string
  unitFamily?: UnitFamily
  presentation?: SeriesPresentation
}

function mkSeries(input: MkSeriesInput): AlignedSeries {
  const { key, label, kind, metric, role, family, y, origin, quality, isAggregated, node, unit, unitFamily, presentation } = input
  let source: SeriesSource
  if (kind === 'sensor') {
    source = 'sensor'
  } else if (role === 'state') {
    source = 'device'
  } else if (role === 'pid_output') {
    source = 'pid'
  } else if (role === 'duty') {
    source = metric.includes('pid') ? 'pid' : 'device'
  } else {
    source = 'climate'
  }
  const s: AlignedSeries = {
    key,
    label: presentation?.label ?? label,
    kind,
    source,
    metric,
    role,
    family,
    y,
    origin,
    quality,
    isAggregated,
  }
  if (node !== undefined) s.node = node
  if (unit !== undefined) s.unit = unit
  if (unitFamily !== undefined) s.unitFamily = unitFamily
  if (presentation !== undefined) s.presentation = presentation
  return s
}
