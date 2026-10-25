/**
 * Per-panel alignment cache. Inputs build one static grid; the live adapter
 * appends the raw tail points without ever re-keying the base on `now`, so a
 * live tick costs one merge of the (≤ one point per bucket) tail instead of a
 * full-grid rebuild or a replay of every historical snapshot.
 */
import type { ControlMonitoringResponse } from '../api'
import type { TimeseriesPanelSpec } from '../config'

import { alignSeriesBase, applyLiveTail } from './alignSeries'
import type { BaseAlignment } from './alignSeries'
import { controlFamily, familyForUnit } from './alignSeries.builder'
import { metricFromName } from './alignSeries.control'
import { windowBounds } from './alignSeries.grid'
import type { AlignInput, AlignedData } from './alignSeries.types'
import type { LiveTailBuffer } from './liveTail'

export interface PanelAlignmentInput extends AlignInput {
  readonly panel: TimeseriesPanelSpec
}

export interface PanelAlignmentCounts {
  readonly baseAlignments: number
  readonly liveTailUpdates: number
}

type BaseAlignInput = Omit<AlignInput, 'live' | 'liveTail'>

interface BaseKey {
  readonly series: AlignInput['series']
  readonly controlHistory: AlignInput['controlHistory']
  readonly projectionHistory: AlignInput['projectionHistory']
  readonly lightRegistry: AlignInput['lightRegistry']
  readonly photoperiod: AlignInput['photoperiod']
  readonly range: AlignInput['range']
  readonly maxPoints: AlignInput['maxPoints']
  readonly sensorIntervalSeconds: AlignInput['sensorIntervalSeconds']
  readonly panel: TimeseriesPanelSpec
}

interface TailKey {
  readonly live: AlignInput['live']
  readonly liveTail: LiveTailBuffer | undefined
  readonly revision: number
  readonly second: number
}

export interface PanelAlignment {
  readonly counts: PanelAlignmentCounts
  align(input: PanelAlignmentInput): AlignedData
}

class CachedPanelAlignment implements PanelAlignment {
  private baseKey: BaseKey | null = null
  private base: BaseAlignment | null = null
  private tailKey: TailKey | null = null
  private tailResult: AlignedData | null = null
  private baseAlignments = 0
  private liveTailUpdates = 0

  get counts(): PanelAlignmentCounts {
    return { baseAlignments: this.baseAlignments, liveTailUpdates: this.liveTailUpdates }
  }

  align(input: PanelAlignmentInput): AlignedData {
    const key = baseKeyFor(input)
    if (!sameBaseKey(this.baseKey, key)) {
      this.base = alignSeriesBase(filterPanelInput(input))
      this.baseKey = key
      this.tailKey = null
      this.tailResult = null
      this.baseAlignments++
    }

    const base = this.base
    if (base === null) return applyLiveTail(alignSeriesBase(filterPanelInput(input)), input, input.now)

    const tailKey = {
      live: input.live,
      liveTail: input.liveTail,
      revision: input.liveTail?.revision ?? -1,
      second: Math.floor(input.now.getTime() / 1000),
    }
    if (this.tailResult !== null && sameTailKey(this.tailKey, tailKey)) {
      return { ...this.tailResult, nowMs: input.now.getTime() }
    }
    const result = applyLiveTail(base, input, input.now)
    const tailPresent =
      (input.liveTail?.size ?? 0) > 0 || input.live.length > 0
    if (tailPresent && result.x !== base.data.x) this.liveTailUpdates++
    this.tailKey = tailKey
    this.tailResult = result
    return result
  }
}

export function createPanelAlignment(): PanelAlignment {
  return new CachedPanelAlignment()
}

function baseKeyFor(input: PanelAlignmentInput): BaseKey {
  return {
    series: input.series,
    controlHistory: input.controlHistory,
    projectionHistory: input.projectionHistory,
    photoperiod: input.photoperiod,
    lightRegistry: input.lightRegistry,
    range: input.range,
    maxPoints: input.maxPoints,
    sensorIntervalSeconds: input.sensorIntervalSeconds,
    panel: input.panel,
  }
}

function sameBaseKey(previous: BaseKey | null, next: BaseKey): boolean {
  return (
    previous !== null &&
    previous.series === next.series &&
    previous.controlHistory === next.controlHistory &&
    previous.projectionHistory === next.projectionHistory &&
    previous.photoperiod === next.photoperiod &&
    previous.range === next.range &&
    previous.maxPoints === next.maxPoints &&
    previous.sensorIntervalSeconds === next.sensorIntervalSeconds &&
    previous.lightRegistry === next.lightRegistry &&
    previous.panel === next.panel
  )
}

function sameTailKey(previous: TailKey | null, next: TailKey): boolean {
  return (
    previous !== null &&
    previous.live === next.live &&
    previous.liveTail === next.liveTail &&
    previous.revision === next.revision &&
    previous.second === next.second
  )
}

function filterPanelInput(input: PanelAlignmentInput): BaseAlignInput {
  const { panel } = input
  const bounds = windowBounds(input.range, input.now)
  const climatePanel = panel.id === 'flower-climate' || panel.id === 'veg-climate'
  const series = input.series.filter(candidate => {
    if (!accepts(panel, 'sensor', familyForUnit(candidate.unit_family))) return false
    if (!climatePanel || !/^(?:dry_bulb|wet_bulb)(?:_[fbv])?$/.test(candidate.sensor)) return true
    return candidate.points.some(
      point =>
        point.timestamp.getTime() >= bounds.start &&
        point.timestamp.getTime() <= bounds.end &&
        point.sample_count > 0 &&
        Number.isFinite(point.average) &&
        Number.isFinite(point.minimum) &&
        Number.isFinite(point.maximum)
    )
  })
  return {
    series,
    controlHistory: filterControlResponse(input.controlHistory, panel),
    projectionHistory: filterControlResponse(input.projectionHistory, panel),
    photoperiod: input.photoperiod,
    range: input.range,
    now: input.now,
    maxPoints: input.maxPoints,
    sensorIntervalSeconds: input.sensorIntervalSeconds,
    seriesSpecs: panel.series,
    scaleDefaults: panel.defaults,
    lightRegistry: input.lightRegistry,
  }
}

/**
 * Prune one control response to the families a panel accepts before
 * alignment, so rejected automation families are never normalized into
 * curves only to be dropped afterwards.
 */
function filterControlResponse(
  response: ControlMonitoringResponse | null,
  panel: TimeseriesPanelSpec
): ControlMonitoringResponse | null {
  if (response === null) return null
  return {
    ...response,
    climate: response.climate.filter(series => {
      const metric = series.metric ?? series.points[0]?.metric ?? metricFromName(series.name)
      return accepts(panel, 'climate', controlFamily({ kind: 'climate', metric }))
    }),
    lights: accepts(panel, 'light', 'light') ? response.lights : [],
    devices: accepts(panel, 'device', 'device') ? response.devices : [],
    pid: accepts(panel, 'pid', 'device') || accepts(panel, 'device', 'device') ? response.pid : [],
  }
}

function accepts(
  panel: TimeseriesPanelSpec,
  source: TimeseriesPanelSpec['sources'][number],
  family: TimeseriesPanelSpec['families'][number]
): boolean {
  return panel.sources.includes(source) && panel.families.includes(family)
}
