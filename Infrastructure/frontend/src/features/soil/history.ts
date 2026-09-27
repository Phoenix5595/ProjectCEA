/**
 * Historical soil feed: adapt `SoilHistoryResponse` into the proven
 * monitoring `AlignedData`/`MonitoringChartFeed` interface for the one
 * multi-axis plot.
 *
 * Probe identity is a stable eight-color palette keyed by numeric Modbus
 * address; metric identity is a line pattern (Water content solid, EC
 * dashed, pH dotted, temperature dash-dot) so color is never the only
 * distinction.
 */
import type { AlignedData, AlignedSeries } from '../monitoring/data'
import type { SoilHistoryResponse } from './api'

const PROBE_COLORS: readonly string[] = [
  'var(--mon-family-temperature)',
  'var(--mon-family-water-content)',
  'var(--mon-family-conductivity)',
  'var(--mon-family-ph)',
  'var(--mon-node-front)',
  'var(--mon-node-back)',
  'var(--mon-focus-ring)',
  'var(--mon-family-light)',
]

/** Metric identity line pattern: Water content solid, EC dashed, pH dotted, temperature dash-dot. */
const METRIC_DASH: Readonly<Record<string, readonly number[] | undefined>> = {
  water_content: [], // solid
  ec: [6, 4], // dashed
  ph: [1, 4], // dotted
  temperature: [8, 4, 1, 4], // dash-dot
}

function familyForMetric(metric: string): AlignedSeries['family'] {
  switch (metric) {
    case 'temperature':
      return 'temperature'
    case 'water_content':
      return 'water_content'
    case 'ec':
      return 'conductivity'
    case 'ph':
      return 'ph'
    default:
      return 'device'
  }
}

/** Stable legend label: bed, probe id, metric, unit. */
export function probeMetricLabel(history: {
  bed: string
  hardware_address: number
  metric: string
  unit: string
}): string {
  return `${history.bed} #${history.hardware_address} ${history.metric} (${history.unit})`
}

/** Stable palette slot in a numeric-address ordering. */
function probeColor(orderedIds: readonly number[], registryId: number): string {
  const slot = orderedIds.indexOf(registryId)
  return PROBE_COLORS[(slot < 0 ? 0 : slot) % PROBE_COLORS.length] ?? PROBE_COLORS[0] ?? ''
}

/**
 * Adapt the API response into the aligned feed: timestamps are the union of
 * all bucket anchors (ascending); each probe/metric series carries `null`
 * for buckets it does not cover (null = gap, never interpolated).
 */
export function adaptSoilHistory(response: SoilHistoryResponse): AlignedData {
  const anchors = new Set<number>()
  for (const history of response.series) {
    for (const point of history.points) {
      anchors.add(point.bucket_start.getTime())
    }
  }
  const x = [...anchors].sort((left, right) => left - right)
  const orderedIds = [...new Set(response.series.map((entry) => entry.registry_id))].sort(
    (left, right) => left - right,
  )
  const series: AlignedSeries[] = response.series.map((history) => {
    const y: Array<number | null> = x.map((anchor) => {
      const point = history.points.find((candidate) => candidate.bucket_start.getTime() === anchor)
      return point === undefined ? null : point.average
    })
    return {
      key: `soil:${history.registry_id}:${history.metric}` as AlignedSeries['key'],
      label: probeMetricLabel(history),
      kind: 'sensor',
      source: 'sensor',
      metric: `${history.registry_id}:${history.metric}`,
      family: familyForMetric(history.metric),
      role: 'mean',
      y,
      origin: 'recorded',
      quality: 'exact',
      isAggregated: true,
      unit: history.unit,
      presentation: {
        color: probeColor(orderedIds, history.registry_id),
        dash: METRIC_DASH[history.metric],
        lineWidth: 1.5,
      },
    }
  })
  return {
    x,
    series,
    bands: [],
    photoperiod: [],
    nowIndex: x.length - 1,
    aggregated: true,
  }
}
