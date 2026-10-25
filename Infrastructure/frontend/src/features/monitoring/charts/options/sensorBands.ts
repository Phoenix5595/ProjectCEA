/**
 * Sensor min–max band companions (R4).
 *
 * A bucketed sensor series carries aligned `minimum` / `maximum` arrays (null
 * where the bucket had one sample, on the live tail, and on non-sensor lines).
 * uPlot needs two real series plus a `bands` entry to paint the envelope, so
 * this module appends hidden companions for every sensor mean series and one
 * band between them. Synthetic companions are appended after the model's own
 * series, which keeps every base series index stable for visibility toggles
 * and stays out of the legend/tooltip/table (they are not part of the model's
 * `series` array).
 *
 * Overview payloads already publish explicit `min`/`max` series and bands;
 * those keep their own companion-free rendering, so synthesis skips any
 * sensor identity that already has a min series.
 */
import type { AlignedBand, AlignedData, AlignedSeries, SeriesKey } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'

export interface BandCompanionIndexes {
  /** 1-based uPlot series index of the minimum companion. */
  readonly minIndex: number
  /** 1-based uPlot series index of the maximum companion. */
  readonly maxIndex: number
}

export interface ChartDataWithBands {
  readonly data: AlignedData
  readonly companionsByParent: ReadonlyMap<string, BandCompanionIndexes>
}

const CACHE = new WeakMap<AlignedData, ChartDataWithBands>()

function emptyColumn(length: number): (number | null)[] {
  return new Array<number | null>(length).fill(null)
}

function companionSeries(
  parent: AlignedSeries,
  role: 'min' | 'max',
  key: SeriesKey,
  label: string,
  y: (number | null)[]
): AlignedSeries {
  return {
    key,
    label,
    kind: parent.kind,
    source: parent.source,
    metric: parent.metric,
    ...(parent.node === undefined ? {} : { node: parent.node }),
    family: parent.family,
    role,
    y,
    origin: parent.origin,
    quality: parent.quality,
    isAggregated: parent.isAggregated,
    ...(parent.unit === undefined ? {} : { unit: parent.unit }),
    ...(parent.unitFamily === undefined ? {} : { unitFamily: parent.unitFamily }),
    ...(parent.presentation === undefined ? {} : { presentation: parent.presentation }),
  }
}

/** Expand model data with sensor band companions; cached per aligned object. */
export function chartDataWithBands(data: AlignedData): ChartDataWithBands {
  const cached = CACHE.get(data)
  if (cached !== undefined) return cached

  const legacyEnvelopeIdentities = new Set(
    data.series
      .filter(series => series.role === 'min')
      .map(series => `${series.source}\u0000${series.metric}`)
  )
  const companions: AlignedSeries[] = []
  const bands: AlignedBand[] = [...data.bands]
  const companionsByParent = new Map<string, BandCompanionIndexes>()

  for (const parent of data.series) {
    if (parent.source !== 'sensor') continue
    if (parent.role === 'min' || parent.role === 'max') continue
    if (legacyEnvelopeIdentities.has(`${parent.source}\u0000${parent.metric}`)) continue

    const metric = `${parent.metric}#band`
    const minKey = seriesKey('sensor', metric, 'min') as SeriesKey
    const maxKey = seriesKey('sensor', metric, 'max') as SeriesKey
    const minIndex = data.series.length + companions.length + 1
    companions.push(
      companionSeries(parent, 'min', minKey, `${parent.label} min`, parent.minimum ?? emptyColumn(data.x.length)),
      companionSeries(parent, 'max', maxKey, `${parent.label} max`, parent.maximum ?? emptyColumn(data.x.length))
    )
    bands.push({
      key: seriesKey('sensor', metric, 'band') as SeriesKey,
      minKey,
      maxKey,
    })
    companionsByParent.set(parent.key, { minIndex, maxIndex: minIndex + 1 })
  }

  const expanded: ChartDataWithBands = {
    data:
      companions.length === 0
        ? data
        : { ...data, series: [...data.series, ...companions], bands },
    companionsByParent,
  }
  CACHE.set(data, expanded)
  return expanded
}
