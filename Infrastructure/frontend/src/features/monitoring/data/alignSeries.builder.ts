/**
 * Series and band builders for the alignment layer.
 *
 * Produces `AlignedSeries` and `AlignedBand` objects from aligned y arrays,
 * chart families, manifest specs, and provenance metadata.
 */
import type { Origin, Quality, UnitFamily } from '../api'
import type { ChartFamily } from '../charts/options/family'
import type { SeriesSpec } from '../config'

import {
  alignControlPoints,
  alignDeviceStates,
  alignLinear,
  alignPid,
  alignStepsWithBounds,
  type ScalarAlignment,
  type SensorBuckets,
} from './alignSeries.series'
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

export function controlFamily(cs: Pick<NormControlSeries, 'kind' | 'metric'>): ChartFamily {
  if (cs.kind === 'light') return 'light'
  const m = cs.metric
  if (m.includes('setpoint')) {
    if (m.includes('co2')) return 'co2'
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

export function findSpec(
  seriesSpecs: SeriesSpec[] | undefined,
  name: string
): SeriesSpec | undefined {
  if (seriesSpecs === undefined) return undefined
  return seriesSpecs.find(s => s.name === name || s.displayName === name)
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
  buckets: SensorBuckets,
  isAggregated: boolean,
  node?: string,
  unit?: string,
  unitFamily?: UnitFamily,
  presentation?: SeriesPresentation
): AlignedSeries[] {
  const { mean, min, max, count } = buckets
  const base = { kind: 'sensor' as const, metric, family, isAggregated, node, unit, unitFamily }
  const label = presentation?.label ?? (metric === 'vpd' ? 'VPD' : metric)
  return [
    mkSeries({
      ...base,
      key: seriesKey('sensor', metric, 'mean'),
      label,
      role: 'mean',
      y: mean,
      origin: 'recorded',
      quality: 'exact',
      presentation,
      // R4: bucket detail only where the bucket holds more than one sample;
      // raw slots stay null so no band or bucket tooltip is fabricated.
      minimum: count.map((samples, index) =>
        samples !== null && samples > 1 ? (min[index] ?? null) : null
      ),
      maximum: count.map((samples, index) =>
        samples !== null && samples > 1 ? (max[index] ?? null) : null
      ),
      sampleCount: count.map(samples => (samples !== null && samples > 1 ? samples : null)),
    }),
    mkSeries({
      ...base,
      key: seriesKey('sensor', metric, 'min'),
      label: `${metric} min`,
      role: 'min',
      y: min,
      origin: 'recorded',
      quality: 'exact',
    }),
    mkSeries({
      ...base,
      key: seriesKey('sensor', metric, 'max'),
      label: `${metric} max`,
      role: 'max',
      y: max,
      origin: 'recorded',
      quality: 'exact',
    }),
  ]
}

export function bandFor(source: SeriesSource, metric: string): AlignedBand {
  return {
    key: seriesKey(source, metric, 'band'),
    minKey: seriesKey(source, metric, 'min'),
    maxKey: seriesKey(source, metric, 'max'),
  }
}

export function bandForSensor(metric: string): AlignedBand {
  return bandFor('sensor', metric)
}

/** True when at least one slot of the alignment carries a known extremum. */
function hasKnownExtrema(aligned: ScalarAlignment): boolean {
  return aligned.min.some(value => value !== null) || aligned.max.some(value => value !== null)
}

/**
 * Emit the two invisible bound series plus the shared min/max band for one
 * bucketed scalar control/PID series, matching the sensor-envelope
 * conventions exactly: the scale width-0 strokes, the band fills with the
 * shared envelope token, and the min/max roles keep them out of the legend
 * and the cursor tooltip so the envelope stays subordinate to the primary
 * series. The caller only emits this when the display grid rebuckets on the
 * client or the source itself is aggregated — a raw exact series keeps
 * rendering its line alone.
 */
function buildEnvelopeSeries(input: {
  source: SeriesSource
  metric: string
  kind: SeriesKind
  family: ChartFamily
  label: string
  aligned: ScalarAlignment
  origin: Origin
  quality: Quality
  isAggregated: boolean
  unit?: string
  unitFamily?: UnitFamily
}): { series: AlignedSeries[]; band: AlignedBand } {
  return {
    series: [
      mkSeries({
        key: seriesKey(input.source, input.metric, 'min'),
        label: `${input.label} min`,
        kind: input.kind,
        metric: input.metric,
        role: 'min',
        family: input.family,
        y: input.aligned.min,
        origin: input.origin,
        quality: input.quality,
        isAggregated: input.isAggregated,
        unit: input.unit,
        unitFamily: input.unitFamily,
      }),
      mkSeries({
        key: seriesKey(input.source, input.metric, 'max'),
        label: `${input.label} max`,
        kind: input.kind,
        metric: input.metric,
        role: 'max',
        family: input.family,
        y: input.aligned.max,
        origin: input.origin,
        quality: input.quality,
        isAggregated: input.isAggregated,
        unit: input.unit,
        unitFamily: input.unitFamily,
      }),
    ],
    band: bandFor(input.source, input.metric),
  }
}

/** One drawable series plus the optional envelope that explains its buckets. */
interface ControlCandidate {
  series: AlignedSeries
  envelope?: { series: AlignedSeries[]; band: AlignedBand }
}

export function buildControlSeries(
  cs: NormControlSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
  presentation?: SeriesPresentation
): { series: AlignedSeries[]; bands: AlignedBand[] } {
  const source: SeriesSource = cs.kind
  const family = controlFamily(cs)
  // The envelope only appears for genuinely bucketed series: a client
  // rebucketed grid, or source buckets that carry recorded extrema.
  const isAgg = aggregated || cs.seriesIsAggregated
  const displayName = presentation?.label ?? cs.name
  const candidates: ControlCandidate[] = []
  if (cs.metric.endsWith('_setpoint')) {
    // One line per target: held steps everywhere, replaced by the exact
    // sampled ramp wherever a linear segment covers the slot. Drawing the
    // step and ramp as two series showed a held value running alongside every
    // ramp. Setpoint targets are lines only; recorded bucket extrema must not
    // create sensor-style min–max shading.
    const linearY =
      cs.linear.length > 0 ? alignLinear(cs.linear, x, start, end, aggregated) : null
    if (cs.steps.length > 0) {
      const aligned = alignStepsWithBounds(cs.steps, x, start, end, aggregated)
      candidates.push({
        series: mkSeries({
          key: seriesKey(source, cs.metric, 'step'),
          label: cs.name,
          kind: 'step',
          metric: cs.metric,
          role: 'step',
          family,
          y: linearY === null ? aligned.y : aligned.y.map((value, index) => linearY[index] ?? value),
          origin: cs.seriesOrigin,
          quality: cs.seriesQuality,
          isAggregated: isAgg,
          presentation,
        }),
      })
    } else if (linearY !== null) {
      candidates.push({
        series: mkSeries({
          key: seriesKey(source, cs.metric, 'linear'),
          label: cs.name,
          kind: 'linear',
          metric: cs.metric,
          role: 'linear',
          family,
          y: linearY,
          origin: cs.seriesOrigin,
          quality: cs.seriesQuality,
          isAggregated: isAgg,
          presentation,
        }),
      })
    }
    // Bare points are only the fallback when a target has no step or ramp.
    if (candidates.length === 0) {
      const alignedPoints = alignControlPoints(cs, x, start, end, aggregated)
      candidates.push({
        series: mkSeries({
          key: seriesKey(source, cs.metric, 'point'),
          label: cs.name,
          kind: 'point',
          metric: cs.metric,
          role: 'point',
          family,
          y: alignedPoints.y,
          origin: cs.seriesOrigin,
          quality: cs.seriesQuality,
          isAggregated: isAgg,
          presentation,
        }),
      })
    }
    const surviving = candidates.filter(candidate =>
      candidate.series.y.some(value => value !== null && Number.isFinite(value))
    )
    return { series: surviving.map(candidate => candidate.series), bands: [] }
  }
  const aligned = alignControlPoints(cs, x, start, end, aggregated)
  const series: ControlCandidate[] = [
    {
      series: mkSeries({
        key: seriesKey(source, cs.metric, 'point'),
        label: cs.name,
        kind: 'point',
        metric: cs.metric,
        role: 'point',
        family,
        y: aligned.y,
        origin: cs.seriesOrigin,
        quality: cs.seriesQuality,
        isAggregated: isAgg,
        presentation,
      }),
      ...(isAgg && hasKnownExtrema(aligned)
        ? {
            envelope: buildEnvelopeSeries({
              source,
              metric: cs.metric,
              kind: 'point',
              family,
              label: displayName,
              aligned,
              origin: cs.seriesOrigin,
              quality: cs.seriesQuality,
              isAggregated: isAgg,
            }),
          }
        : {}),
    },
  ]
  if (cs.steps.length > 0) {
    series.push({
      series: mkSeries({
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
    })
  }
  if (cs.linear.length > 0) {
    series.push({
      series: mkSeries({
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
    })
  }
  return {
    series: series.flatMap(candidate => [candidate.series, ...(candidate.envelope?.series ?? [])]),
    bands: series.flatMap(candidate => (candidate.envelope ? [candidate.envelope.band] : [])),
  }
}

export function buildDeviceSeries(
  ds: NormDeviceSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
  presentation?: SeriesPresentation
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
  return out
}

export function buildPidSeries(
  ps: NormPidSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
  presentation?: SeriesPresentation
): { series: AlignedSeries[]; bands: AlignedBand[] } {
  const family: ChartFamily = 'device'
  // PID observations are always read from bucketed sources; the envelope
  // only matters once the client rebuckets or the buckets themselves carry
  // recorded extrema. Duty cycle stays exactly duty cycle: no new duty
  // extrema are exposed.
  const isAgg = aggregated || ps.seriesIsAggregated
  const series: AlignedSeries[] = []
  const bands: AlignedBand[] = []
  if (ps.pidOutputs.length > 0) {
    const aligned = alignPid(ps.pidOutputs, x, start, end, aggregated)
    const label = presentation?.label ?? `${ps.name} - PID Output`
    series.push(
      mkSeries({
        key: seriesKey('pid', ps.metric, 'pid_output'),
        label: `${ps.name} - PID Output`,
        kind: 'point',
        metric: ps.metric,
        role: 'pid_output',
        family,
        y: aligned.y,
        origin: ps.seriesOrigin,
        quality: ps.seriesQuality,
        isAggregated: isAgg,
        unit: '%',
        unitFamily: 'percent',
        presentation,
      })
    )
    if (isAgg && hasKnownExtrema(aligned)) {
      const envelope = buildEnvelopeSeries({
        source: 'pid',
        metric: ps.metric,
        kind: 'point',
        family,
        label,
        aligned,
        origin: ps.seriesOrigin,
        quality: ps.seriesQuality,
        isAggregated: isAgg,
        unit: '%',
        unitFamily: 'percent',
      })
      series.push(...envelope.series)
      bands.push(envelope.band)
    }
  }
  if (ps.dutyCycles.length > 0) {
    series.push(
      mkSeries({
        key: seriesKey('pid', ps.metric, 'duty'),
        label: `${ps.name} - Duty Cycle`,
        kind: 'point',
        metric: ps.metric,
        role: 'duty',
        family,
        y: alignPid(ps.dutyCycles, x, start, end, aggregated).y,
        origin: ps.seriesOrigin,
        quality: ps.seriesQuality,
        isAggregated: isAgg,
        unit: '%',
        unitFamily: 'percent',
        presentation,
      })
    )
  }
  return { series, bands }
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
  minimum?: (number | null)[]
  maximum?: (number | null)[]
  sampleCount?: (number | null)[]
  liveTail?: boolean[]
}

function mkSeries(input: MkSeriesInput): AlignedSeries {
  const {
    key,
    label,
    kind,
    metric,
    role,
    family,
    y,
    origin,
    quality,
    isAggregated,
    node,
    unit,
    unitFamily,
    presentation,
    minimum,
    maximum,
    sampleCount,
    liveTail,
  } = input
  const source = key.slice(0, key.indexOf(':')) as SeriesSource
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
  if (minimum !== undefined) s.minimum = minimum
  if (maximum !== undefined) s.maximum = maximum
  if (sampleCount !== undefined) s.sampleCount = sampleCount
  if (liveTail !== undefined) s.liveTail = liveTail
  return s
}
