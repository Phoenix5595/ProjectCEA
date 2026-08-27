import type { ThemeName } from '../../../contexts/ThemeContext'
import type { TimeseriesPanelSpec, SeriesSpec } from '../config'
import { alignSeries } from '../data'
import type { AlignInput, AlignedData, AlignedSeries, PanelAlignment } from '../data'
import type { MonitoringRange, StoreState } from '../state'
import {
  beginMonitoringPerfTick,
  finishMonitoringPerfTick,
  measureMonitoringAlignment,
  PERFORMANCE_MARKS_ENABLED,
} from '../perfMarks'

export interface MonitoringChartStructuralSnapshot {
  readonly revision: number
  readonly range: MonitoringRange
  readonly viewportRevision: number
  readonly series: readonly AlignedSeries[]
  readonly seriesCount: number
  readonly theme: ThemeName | null
}

export interface MonitoringChartFeed {
  getData(): AlignedData
  getStructuralSnapshot(): MonitoringChartStructuralSnapshot
  subscribe(listener: () => void): () => void
  publish(data: AlignedData, range: MonitoringRange, viewportRevision?: number): void
  setTheme(theme: ThemeName): void
}

export interface MonitoringChartFeedSource {
  readonly getSnapshot?: () => StoreState
  readonly subscribe?: (listener: () => void) => () => void
}

export interface MonitoringPanelChartFeed extends MonitoringChartFeed {
  connect(source: MonitoringChartFeedSource, initialSnapshot: StoreState): () => void
}

export interface MonitoringPanelChartFeedOptions {
  readonly alignment: PanelAlignment
  readonly panel?: TimeseriesPanelSpec
  readonly seriesSpecs: SeriesSpec[]
  readonly now?: () => Date
}

class ChartFeed implements MonitoringChartFeed {
  private data: AlignedData
  private structural: MonitoringChartStructuralSnapshot
  private readonly listeners = new Set<() => void>()

  constructor(data: AlignedData, range: MonitoringRange) {
    this.data = data
    this.structural = structuralSnapshot(data, range, null, 0, 0)
  }

  getData(): AlignedData {
    return this.data
  }

  getStructuralSnapshot(): MonitoringChartStructuralSnapshot {
    return this.structural
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  publish(data: AlignedData, range: MonitoringRange, viewportRevision?: number): void {
    const previous = this.structural
    if (viewportRevision !== undefined && viewportRevision < previous.viewportRevision) return
    this.data = data
    const sameShape = sameSeriesShape(previous.series, data.series)
    const sameRange = previous.range === range
    const nextViewportRevision = viewportRevision ?? (sameRange
      ? previous.viewportRevision
      : previous.viewportRevision + 1)
    if (!sameShape || !sameRange || nextViewportRevision !== previous.viewportRevision) {
      this.structural = structuralSnapshot(
        data,
        range,
        previous.theme,
        sameShape ? previous.revision : previous.revision + 1,
        nextViewportRevision,
      )
    }
    this.emit()
  }

  setTheme(theme: ThemeName): void {
    const previous = this.structural
    if (previous.theme === theme) return
    this.structural = structuralSnapshot(this.data, previous.range, theme, previous.revision + 1, previous.viewportRevision)
    this.emit()
  }

  private emit(): void {
    for (const listener of this.listeners) listener()
  }
}

class PanelChartFeed extends ChartFeed implements MonitoringPanelChartFeed {
  private readonly alignment: PanelAlignment
  private readonly panel: TimeseriesPanelSpec | undefined
  private readonly seriesSpecs: SeriesSpec[]
  private readonly now: () => Date

  constructor(options: MonitoringPanelChartFeedOptions) {
    const initialRange = { kind: 'live', duration: 3_600_000 } as const
    super(emptyData(), initialRange)
    this.alignment = options.alignment
    this.panel = options.panel
    this.seriesSpecs = options.seriesSpecs
    this.now = options.now ?? (() => new Date())
  }

  connect(source: MonitoringChartFeedSource, initialSnapshot: StoreState): () => void {
    const update = (snapshot: StoreState): void => {
      const fulfilled = snapshot.fulfilledRange
      if (fulfilled === null) return
      const input = this.inputFor(snapshot)
      const align = (): AlignedData => this.panel === undefined
        ? alignSeries(input)
        : this.alignment.align({ ...input, panel: this.panel })
      if (PERFORMANCE_MARKS_ENABLED) beginMonitoringPerfTick()
      const data = PERFORMANCE_MARKS_ENABLED ? measureMonitoringAlignment(align) : align()
      const fulfilledRevision = snapshot.fulfilledRange?.revision
      this.publish(data, fulfilled?.range ?? snapshot.range, fulfilledRevision)
      if (PERFORMANCE_MARKS_ENABLED) finishMonitoringPerfTick(undefined)
    }
    update(source.getSnapshot?.() ?? initialSnapshot)
    if (source.subscribe === undefined || source.getSnapshot === undefined) return () => undefined
    return source.subscribe(() => update(source.getSnapshot?.() ?? initialSnapshot))
  }

  private inputFor(snapshot: StoreState): AlignInput {
    const fulfilled = snapshot.fulfilledRange
    if (fulfilled === null) {
      return {
        ...snapshot.data,
        range: snapshot.range,
        now: this.now(),
        seriesSpecs: this.seriesSpecs,
      }
    }
    return {
      ...snapshot.data,
      range: fulfilled.range,
      now: fulfilled.end,
      seriesSpecs: this.seriesSpecs,
    }
  }
}

export function createMonitoringChartFeed(data: AlignedData, range: MonitoringRange): MonitoringChartFeed {
  return new ChartFeed(data, range)
}

export function createMonitoringPanelChartFeed(
  options: MonitoringPanelChartFeedOptions,
): MonitoringPanelChartFeed {
  return new PanelChartFeed(options)
}

function structuralSnapshot(
  data: AlignedData,
  range: MonitoringRange,
  theme: ThemeName | null,
  revision: number,
  viewportRevision: number,
): MonitoringChartStructuralSnapshot {
  return { revision, range, viewportRevision, series: data.series, seriesCount: data.series.length, theme }
}

function sameSeriesShape(previous: readonly AlignedSeries[], next: readonly AlignedSeries[]): boolean {
  return previous.length === next.length && previous.every((series, index) => series.key === next[index]?.key)
}

function emptyData(): AlignedData {
  return {
    x: [],
    series: [],
    bands: [],
    photoperiod: [],
    nowIndex: -1,
    aggregated: false,
  }
}
