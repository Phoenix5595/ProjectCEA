import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useSyncExternalStore,
} from 'react'
import uPlot from 'uplot'

import 'uplot/dist/uPlot.min.css'
import { useTheme } from '../../../contexts/ThemeContext'
import {
  measureMonitoringConversion,
  measureMonitoringResize,
  measureMonitoringSetData,
  PERFORMANCE_MARKS_ENABLED,
  registerMonitoringChart,
  removeMonitoringChart,
  updateMonitoringChart,
} from '../perfMarks'

import { measureChartContainer } from './chartSizing'
import { BucketBadge } from './BucketBadge'
import { ExternalLegend, type LegendEntry } from './legend/ExternalLegend'
import type { MonitoringChartFeed } from './MonitoringChartFeed'
import type { MonitoringRange } from '../state'
import { isEnvelopeSeries, seriesColor } from './options/seriesOptions'
import { chartDataWithBands } from './options/sensorBands'
import {
  getSeriesVisibilitySnapshot,
  isSeriesHidden,
  registerSeriesEntries,
  subscribeSeriesVisibility,
  toggleSeries,
} from './seriesVisibility'
import { buildOptions, toUPlotData } from './uPlotOptions'
import { useRequestBudgetReporter } from './useRequestBudgetReporter'

export interface UPlotChartProps {
  readonly feed: MonitoringChartFeed
  readonly className?: string
  readonly onZoom?: (range: { start: Date; end: Date }) => void
  readonly title?: string
  readonly description?: string
  readonly onRequestBudgetChange?: (maxPoints: number) => void
  /** Recorded sensor bucket span; `null`/1 renders `Buckets: raw`. */
  readonly bucketIntervalSeconds?: number | null
}

/**
 * R1: report the plot area (not the frame) so a line never exceeds one point
 * per CSS pixel. `bbox` is in device pixels, so divide by uPlot's ratio.
 */
function reportPlotWidth(plot: uPlot, reportRequestBudget: (width: number) => void): void {
  const ratio = uPlot.pxRatio > 0 ? uPlot.pxRatio : 1
  const width = plot.bbox?.width
  if (typeof width !== 'number' || width <= 0) return
  reportRequestBudget(width / ratio)
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

export interface UPlotChartHandle {
  resetZoom: () => void
}

export const UPlotChart = memo(
  forwardRef<UPlotChartHandle, UPlotChartProps>(function UPlotChart(
    { feed, className, onZoom, title, description, onRequestBudgetChange, bucketIntervalSeconds },
    ref
  ) {
    const { theme } = useTheme()
    const subscribe = useCallback((listener: () => void) => feed.subscribe(listener), [feed])
    const structural = useSyncExternalStore(subscribe, () => feed.getStructuralSnapshot())
    const structuralRef = useRef(structural)
    structuralRef.current = structural
    const frameRef = useRef<HTMLDivElement>(null)
    const containerRef = useRef<HTMLDivElement>(null)
    const plotRef = useRef<uPlot | null>(null)
    const visibilityRef = useRef<Map<string, boolean>>(new Map())
    const lastAppliedViewportRevisionRef = useRef<number | null>(null)
    const lastAppliedStructuralRevisionRef = useRef<number | null>(null)
    const chartDebugRef = useRef<number | null>(null)
    const chartResizeCountRef = useRef(0)
    const followLiveViewportRef = useRef(true)
    const readyRef = useRef(false)
    // uPlot fires setSelect (with the box still visible) immediately before a
    // drag-zoom's own scale set, and silently when it hides the box, so this
    // flag is an exact origin gate for the next setScale event. Scale events
    // are batched and coalesced by uPlot's commit(), so counting call tokens
    // misclassified user drags (leaked tokens made zooms snap back to live).
    const dragScaleExpectedRef = useRef(false)
    const visibility = useSyncExternalStore(subscribeSeriesVisibility, getSeriesVisibilitySnapshot)
    const onZoomRef = useRef(onZoom)
    onZoomRef.current = onZoom
    const reportRequestBudget = useRequestBudgetReporter(onRequestBudgetChange)

    useEffect(() => {
      feed.setTheme(theme)
    }, [feed, theme])

    useEffect(() => {
      registerSeriesEntries(
        structural.series.map(series => ({ key: series.key, color: seriesColor(series) }))
      )
    }, [structural.series])

    useEffect(() => {
      const plot = plotRef.current
      if (plot === null) return
      const bandCompanions = chartDataWithBands(feed.getData()).companionsByParent
      structuralRef.current.series.forEach((series, index) => {
        const show = !visibility.hidden.has(series.key)
        if (visibilityRef.current.get(series.key) === show) return
        visibilityRef.current.set(series.key, show)
        plot.setSeries(index + 1, { show })
        const companion = bandCompanions.get(series.key)
        if (companion === undefined) return
        plot.setSeries(companion.minIndex, { show })
        plot.setSeries(companion.maxIndex, { show })
      })
    }, [visibility, structural.revision, feed])

    const legendEntries = useMemo<LegendEntry[]>(() => {
      const seenKeys = new Set<string>()
      return structural.series
        .map((series, index) => ({ series, index: index + 1 }))
        .filter(({ series }) => {
          if (isEnvelopeSeries(series) || seenKeys.has(series.key)) return false
          seenKeys.add(series.key)
          return true
        })
        .map(({ series, index }) => ({
          key: series.key,
          label: series.label,
          color: seriesColor(series),
          projected: series.origin === 'projected',
          index,
          visible: !visibility.hidden.has(series.key),
        }))
    }, [visibility, structural.series])

    useImperativeHandle(
      ref,
      () => ({
        resetZoom: () => {
          const plot = plotRef.current
          if (plot === null) return
          const range = structuralRef.current.range
          followLiveViewportRef.current = true
          if (range.kind === 'fixed') {
            plot.setScale('x', { min: range.start.getTime(), max: range.end.getTime() })
            return
          }
          const data = feed.getData()
          plot.setScale('x', {
            min: data.nowMs - range.duration,
            max: data.nowMs + range.duration / 9,
          })
        },
      }),
      [feed]
    )

    useEffect(() => {
      if (structural.theme !== theme) return
      const frameElement = frameRef.current
      const container = containerRef.current
      if (frameElement === null || container === null) return
      readyRef.current = false
      let frame: number | null = null
      const applyFrameSize = (): void => {
        const { clientWidth, clientHeight } = frameElement
        if (clientWidth <= 0 || clientHeight <= 0) return
        const { width, height } = measureChartContainer(frameElement)
        const existingPlot = plotRef.current
        if (existingPlot !== null) {
          chartResizeCountRef.current += 1
          if (PERFORMANCE_MARKS_ENABLED) {
            measureMonitoringResize(() => existingPlot.setSize({ width, height }))
          } else {
            existingPlot.setSize({ width, height })
          }
          reportPlotWidth(existingPlot, reportRequestBudget)
          const chartDebug = chartDebugRef.current
          if (chartDebug !== null)
            updateMonitoringChart(chartDebug, {
              width,
              height,
              resizeCount: chartResizeCountRef.current,
            })
          return
        }
        const data = feed.getData()
        const plot = new uPlot(
          initializeFixedScales(buildOptions(
            data,
            width,
            height,
            {
              onSetSelect: select => {
                dragScaleExpectedRef.current = select.show === true && (select.width ?? 0) > 0
              },
              onSetScale: (self, scaleKey) => {
                if (scaleKey !== 'x') return
                const chartDebug = chartDebugRef.current
                const { min, max } = self.scales.x
                if (chartDebug !== null) {
                  updateMonitoringChart(chartDebug, {
                    xScaleMin: min ?? null,
                    xScaleMax: max ?? null,
                  })
                }
                const userScale = dragScaleExpectedRef.current
                dragScaleExpectedRef.current = false
                if (userScale) followLiveViewportRef.current = false
                if (!readyRef.current) {
                  readyRef.current = true
                  return
                }
                if (!userScale) return
                if (min === undefined || max === undefined) return
                // R3: the page decides what the selection means. Never clamp
                // the requested end to the last recorded point: a selection
                // ending past now must stay a live window.
                onZoomRef.current?.({ start: new Date(min), end: new Date(max) })
              },
              onSetSeries: (_self, seriesIndex, options) => {
                if (seriesIndex === null || options.show === undefined) return
                const key = structuralRef.current.series[seriesIndex - 1]?.key
                if (key === undefined) return
                visibilityRef.current.set(key, options.show)
                if (isSeriesHidden(key) === options.show) toggleSeries(key)
              },
            },
            () => {
              const current = feed.getData()
              return current.nowIndex >= 0 && current.nowIndex < current.x.length
                ? current.x[current.nowIndex]
                : null
            },
            () => feed.getData().photoperiod,
            () => feed.getData()
          ), feed),
          PERFORMANCE_MARKS_ENABLED
            ? measureMonitoringConversion(() => toUPlotData(data))
            : toUPlotData(data),
          container
        )
        plotRef.current = plot
        reportPlotWidth(plot, reportRequestBudget)
        readyRef.current = true
        lastAppliedStructuralRevisionRef.current = structural.revision
        if (data.overview !== undefined) {
          lastAppliedViewportRevisionRef.current = structural.viewportRevision
        }
        const bandCompanions = chartDataWithBands(data).companionsByParent
        structural.series.forEach((series, index) => {
          const show = visibilityRef.current.get(series.key)
          if (show === undefined) return
          plot.setSeries(index + 1, { show }, false)
          const companion = bandCompanions.get(series.key)
          if (companion === undefined) return
          plot.setSeries(companion.minIndex, { show }, false)
          plot.setSeries(companion.maxIndex, { show }, false)
        })
        lastAppliedViewportRevisionRef.current = structural.viewportRevision
        chartResizeCountRef.current = 0
        chartDebugRef.current = registerMonitoringChart({
          title: title ?? 'Monitoring chart',
          width,
          height,
          xScaleMin: plot.scales.x.min ?? null,
          xScaleMax: plot.scales.x.max ?? null,
          viewportRevision: structural.viewportRevision,
          destroyCount: 0,
          resizeCount: 0,
        })
      }
      const scheduleFrameSize = (): void => {
        if (frame !== null) return
        frame = requestAnimationFrame(() => {
          frame = null
          applyFrameSize()
        })
      }
      const observer = new ResizeObserver(scheduleFrameSize)
      observer.observe(frameElement)
      applyFrameSize()

      return () => {
        if (frame !== null) cancelAnimationFrame(frame)
        observer.disconnect()
        const plot = plotRef.current
        if (plot !== null) plot.destroy()
        const chartDebug = chartDebugRef.current
        if (chartDebug !== null) {
          updateMonitoringChart(chartDebug, { destroyCount: 1 })
          removeMonitoringChart(chartDebug)
          chartDebugRef.current = null
        }
        plotRef.current = null
      }
    }, [feed, reportRequestBudget, structural, theme, title])

    const wasLiveRef = useRef<boolean | null>(null)
    const appliedRangeRef = useRef<MonitoringRange | null>(null)
    const updateData = useCallback((): void => {
      const plot = plotRef.current
      if (plot === null) return
      const currentStructural = feed.getStructuralSnapshot()
      if (currentStructural.revision !== lastAppliedStructuralRevisionRef.current) return
      const data = feed.getData()
      // Applying a new live range (preset or a zoom selection that reaches
      // now) must resume viewport following; otherwise the chart keeps the
      // stale drag window. Fixed ranges never follow.
      const isLive = currentStructural.range.kind === 'live'
      const transitionedToLive = isLive && wasLiveRef.current === false
      const appliedNewRange =
        appliedRangeRef.current !== null && appliedRangeRef.current !== currentStructural.range
      wasLiveRef.current = isLive
      appliedRangeRef.current = currentStructural.range
      if (isLive && (transitionedToLive || appliedNewRange)) {
        followLiveViewportRef.current = true
      }
      const needsViewportScale =
        currentStructural.viewportRevision !== lastAppliedViewportRevisionRef.current ||
        (currentStructural.range.kind === 'live' && followLiveViewportRef.current)

      if (needsViewportScale) {
        if (PERFORMANCE_MARKS_ENABLED) {
          const converted = measureMonitoringConversion(() => toUPlotData(data))
          measureMonitoringSetData(() => plot.setData(converted, false))
        } else {
          plot.setData(toUPlotData(data), false)
        }
        if (currentStructural.viewportRevision !== lastAppliedViewportRevisionRef.current) {
          if (currentStructural.range.kind === 'fixed') {
            const rangeBounds = fullDataRange(feed)
            if (rangeBounds !== null) {
              plot.setScale('x', rangeBounds)
            }
          }
          lastAppliedViewportRevisionRef.current = currentStructural.viewportRevision
        }
        if (currentStructural.range.kind === 'live' && followLiveViewportRef.current) {
          const duration = currentStructural.range.duration
          // R4: follow wall-clock now, not the last recorded x slot, so the
          // future duration/9 area is always reserved even when no raw value
          // has arrived yet.
          plot.setScale('x', {
            min: data.nowMs - duration,
            max: data.nowMs + duration / 9,
          })
        }
      } else {
        if (PERFORMANCE_MARKS_ENABLED) {
          const converted = measureMonitoringConversion(() => toUPlotData(data))
          measureMonitoringSetData(() => plot.setData(converted, false))
        } else {
          plot.setData(toUPlotData(data), false)
        }
      }
      const chartDebug = chartDebugRef.current
      if (chartDebug !== null)
        updateMonitoringChart(chartDebug, { viewportRevision: currentStructural.viewportRevision })
    }, [feed])

    useEffect(() => feed.subscribe(updateData), [feed, updateData])

    const descId = title === undefined ? undefined : `mon-chart-desc-${slugify(title)}`
    const bucketInterval =
      bucketIntervalSeconds !== undefined
        ? bucketIntervalSeconds
        : feed.getData().sensorIntervalSeconds
    const showBucketBadge = structural.series.some(series => series.source === 'sensor')
    return (
      <div className="mon-chart" style={{ position: 'relative' }}>
        {showBucketBadge && <BucketBadge intervalSeconds={bucketInterval} />}
        <div ref={frameRef} className="mon-chart__frame">
          <div
            ref={containerRef}
            className={className}
            role="img"
            aria-label={title ?? 'Monitoring chart'}
            aria-describedby={descId}
            style={{ position: 'absolute', inset: 0 }}
          />
        </div>
        {description !== undefined && descId !== undefined && (
          <p id={descId} className="mon-chart__desc">
            {description}
          </p>
        )}
        <ExternalLegend entries={legendEntries} />
      </div>
    )
  })
)

function initializeFixedScales(options: uPlot.Options, feed: MonitoringChartFeed): uPlot.Options {
  const range = feed.getStructuralSnapshot().range
  if (range.kind === 'fixed') {
    const scales = options.scales ?? (options.scales = {})
    const x = scales.x ?? (scales.x = {})
    // Set before construction: ready/zoom hooks must see the real requested
    // extent, including empty outer buckets, not just populated midpoints. A
    // fixed range is exact on both pages (R2/R3) — no future space on
    // Historical and exact zoom bounds on Monitoring.
    x.min = range.start.getTime()
    x.max = range.end.getTime()
  }
  return options
}

function fullDataRange(feed: MonitoringChartFeed): { min: number; max: number } | null {
  const range = feed.getStructuralSnapshot().range
  if (range.kind === 'fixed') {
    return { min: range.start.getTime(), max: range.end.getTime() }
  }
  const x = feed.getData().x
  const start = x[0]
  const end = x[x.length - 1]
  return start === undefined || end === undefined ? null : { min: start, max: end }
}
