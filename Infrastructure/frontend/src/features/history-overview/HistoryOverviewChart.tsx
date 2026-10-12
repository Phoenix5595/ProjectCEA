/**
 * Overview chart + combined overview view.
 *
 * `HistoryOverviewChart` wraps the monitoring uPlot chart with one guaranteed
 * invariant: the visible x scale always equals the original fixed overview
 * request, because any user zoom or click immediately hands the parent a
 * drill-down detail range (`onDetail`). That lets click mapping use the
 * chart's `.u-over` bounding box without touching uPlot internals.
 *
 * `HistoryOverviewView` is the full overview page section: it owns the
 * overview fetch generation, renders the monitoring room layout
 * (`mon-layout`/side summary tables + climate/device cards for sensor rooms,
 * one "Aggregated relay time" card for `controlOnly`) and delegates
 * drill-down to the page through `onDetail`.
 */
import {
  useCallback,
  useMemo,
  useRef,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'

import '../monitoring/styles/monitoring.css'
import { UPlotChart, createMonitoringChartFeed } from '../monitoring/charts'
import type { AlignedData } from '../monitoring/data'
import type { MonitoringManifest } from '../monitoring/config'
import type { MonitoringRequestContext } from '../monitoring/api'
import type { FixedRange } from '../monitoring/state'
import { splitChartGroups } from '../monitoring/pages/chartGroups'

import { buildHistoryOverviewAlignedData } from './aligned'
import { drillDownRange } from './buckets'
import { HistoryOverviewTables } from './HistoryOverviewTables'
import { useHistoryOverview } from './useHistoryOverview'

export interface HistoryOverviewChartProps {
  readonly data: AlignedData
  readonly range: FixedRange
  readonly onDetail: (start: Date, end: Date) => void
  readonly historicalDrillDown?: boolean
  /** Fallback height when the caller container does not fix one. */
  readonly height?: number
  /** Accessible chart title/label. */
  readonly label?: string
}

/** Click selections carry zero width; `drillDownRange` widens them to a day. */
const CLICK_DRILL_SELECTION_MS = 0

export function HistoryOverviewChart({
  data,
  range,
  onDetail,
  historicalDrillDown = false,
  height,
  label,
}: HistoryOverviewChartProps) {
  const feed = useMemo(() => createMonitoringChartFeed(data, range), [data, range])
  const frameRef = useRef<HTMLDivElement | null>(null)
  const pointerDownRef = useRef<{ x: number; y: number } | null>(null)

  const handleZoom = useCallback(
    (zoom: { start: Date; end: Date }) => {
      const detail = drillDownRange(range, zoom, historicalDrillDown ? 'historical' : 'detail')
      onDetail(detail.start, detail.end)
    },
    [onDetail, range, historicalDrillDown]
  )

  const handlePointerDown = useCallback((event: ReactPointerEvent) => {
    pointerDownRef.current = { x: event.clientX, y: event.clientY }
  }, [])

  const handleClick = useCallback(
    (event: ReactMouseEvent) => {
      const pointerDown = pointerDownRef.current
      pointerDownRef.current = null
      if (
        pointerDown !== null &&
        Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y) > 4
      ) {
        // A real drag zoomed the chart; the setScale path drives onDetail.
        return
      }
      const overlay = frameRef.current?.querySelector('.u-over')
      if (!(overlay instanceof HTMLElement)) return
      const bounds = overlay.getBoundingClientRect()
      if (bounds.width <= 0) return
      if (event.clientY < bounds.top || event.clientY > bounds.bottom) return
      const fraction = (event.clientX - bounds.left) / bounds.width
      if (fraction < 0 || fraction > 1) return
      const startMs = range.start.getTime()
      const clickMs = startMs + fraction * (range.end.getTime() - startMs)
      const detail = drillDownRange(
        range,
        {
          start: new Date(clickMs),
          end: new Date(clickMs + CLICK_DRILL_SELECTION_MS),
        },
        historicalDrillDown ? 'historical' : 'detail'
      )
      onDetail(detail.start, detail.end)
    },
    [onDetail, range, historicalDrillDown]
  )

  const description =
    'Aggregated history overview. Click or drag to open a detail window in this interval.'
  return (
    <div
      ref={frameRef}
      className="relative"
      style={height === undefined ? { height: '100%' } : { height }}
      onPointerDown={handlePointerDown}
      onClick={handleClick}
    >
      <UPlotChart feed={feed} onZoom={handleZoom} title={label} description={description} />
    </div>
  )
}

const DEFAULT_CLIMATE_HEIGHT = 780
const DEFAULT_DEVICE_HEIGHT = 320

export interface HistoryOverviewViewProps {
  readonly room: string
  readonly range: FixedRange
  readonly requestContext?: MonitoringRequestContext
  readonly manifest?: MonitoringManifest
  /** Load only the control overview (relay/PID-lane automation page). */
  readonly controlOnly?: boolean
  readonly onDetail: (start: Date, end: Date) => void
  /** Historical range owners apply the two-stage seven-day drill-down rule. */
  readonly historicalDrillDown?: boolean
  readonly climateHeight?: number
  readonly deviceHeight?: number
}

function rangeLabel(range: FixedRange): string {
  return `${range.start.toISOString()} to ${range.end.toISOString()}`
}

function retainSeries(aligned: AlignedData, series: AlignedData['series']): AlignedData {
  const keep = new Set(series.map(item => item.key))
  return {
    x: aligned.x,
    series,
    bands: aligned.bands.filter(band => keep.has(band.minKey) && keep.has(band.maxKey)),
    photoperiod: [],
    nowIndex: aligned.nowIndex,
    aggregated: aligned.aggregated,
    overview: aligned.overview,
  }
}

/** Manifest panels own the canonical climate/device split; sensor source otherwise. */
function paneGroups(
  manifest: MonitoringManifest | undefined,
  aligned: AlignedData
): {
  climate: AlignedData
  device: AlignedData
} {
  const panels = manifest?.panels.filter(panel => panel.kind === 'timeseries') ?? []
  if (panels.length >= 2 && manifest !== undefined) {
    return splitChartGroups(manifest, aligned)
  }
  const sensors = aligned.series.filter(candidate => candidate.source === 'sensor')
  return {
    climate: retainSeries(aligned, sensors),
    device: retainSeries(
      aligned,
      aligned.series.filter(item => item.source !== 'sensor')
    ),
  }
}

export function HistoryOverviewView({
  room,
  range,
  requestContext,
  manifest,
  controlOnly = false,
  onDetail,
  historicalDrillDown = false,
  climateHeight,
  deviceHeight,
}: HistoryOverviewViewProps) {
  const { sensor, control, loading, error, retry } = useHistoryOverview({
    room,
    range,
    requestContext,
    controlOnly,
  })
  const aligned = useMemo(
    () =>
      buildHistoryOverviewAlignedData({
        sensor,
        control,
        manifest,
        includeAutomation: controlOnly,
      }),
    [sensor, control, manifest, controlOnly]
  )

  if (loading) {
    return (
      <div className="mon-page">
        <div role="status" className="mon-banner">
          Loading history overview for {rangeLabel(range)}…
        </div>
      </div>
    )
  }
  if (error !== null) {
    return (
      <div className="mon-page">
        <div role="alert" className="mon-banner mon-banner--error">
          <span className="mon-status__error-text">History overview failed: {error}</span>
          <button type="button" onClick={retry}>
            Retry
          </button>
        </div>
      </div>
    )
  }
  if (aligned.series.length === 0) {
    return (
      <div className="mon-page">
        <div role="status" className="mon-banner">
          No recorded chart or sensor statistics inside {rangeLabel(range)}.
        </div>
      </div>
    )
  }

  if (controlOnly) {
    const relaySeries = aligned.series.filter(candidate => candidate.source !== 'light')
    const relayData = retainSeries(aligned, relaySeries)
    return (
      <div className="mon-page">
        <section className="mon-card" aria-label="Aggregated relay time">
          <h2 className="mon-card__title">Aggregated relay time</h2>
          <HistoryOverviewChart
            data={relayData}
            range={range}
            onDetail={onDetail}
            height={deviceHeight ?? DEFAULT_DEVICE_HEIGHT}
            label="Aggregated relay time"
          />
        </section>
      </div>
    )
  }

  const { climate, device } = paneGroups(manifest, aligned)
  const panels = timeseriesTitles(manifest)
  const climateTitle = panels[0] ?? 'Climate conditions'
  const deviceTitle = panels[1] ?? 'Relay & device activity'

  return (
    <div className="mon-page">
      <div className="mon-main">
        <section className="mon-card" aria-label={climateTitle}>
          <h2 className="mon-card__title">{climateTitle}</h2>
          <HistoryOverviewChart
            data={climate}
            range={range}
            onDetail={onDetail}
            historicalDrillDown={historicalDrillDown}
            height={climateHeight ?? DEFAULT_CLIMATE_HEIGHT}
            label={climateTitle}
          />
        </section>
        <section className="mon-card" aria-label={deviceTitle}>
          <h2 className="mon-card__title">{deviceTitle}</h2>
          <HistoryOverviewChart
            data={device}
            range={range}
            onDetail={onDetail}
            historicalDrillDown={historicalDrillDown}
            height={deviceHeight ?? DEFAULT_DEVICE_HEIGHT}
            label={deviceTitle}
          />
        </section>
        {(sensor?.statistics.length ?? 0) > 0 && (
          <section className="mon-card" aria-label="Selected range summary">
            <HistoryOverviewTables sensor={sensor} manifest={manifest} />
          </section>
        )}
      </div>
    </div>
  )
}

function timeseriesTitles(manifest: MonitoringManifest | undefined): string[] {
  if (manifest === undefined) return []
  return manifest.panels.filter(panel => panel.kind === 'timeseries').map(panel => panel.title)
}
