import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import uPlot from 'uplot'

import 'uplot/dist/uPlot.min.css'
import { useTheme } from '../../../contexts/ThemeContext'
import { measureMonitoringConversion, measureMonitoringResize, measureMonitoringSetData, PERFORMANCE_MARKS_ENABLED, registerMonitoringChart, removeMonitoringChart, updateMonitoringChart } from '../perfMarks'

import type { MonitoringChartFeed } from './MonitoringChartFeed'
import { measureChartContainer } from './chartSizing'
import { ExternalLegend, type LegendEntry } from './legend/ExternalLegend'
import { isEnvelopeSeries, seriesColor } from './options/seriesOptions'
import { buildOptions, toUPlotData } from './uPlotOptions'
import { useRequestBudgetReporter } from './useRequestBudgetReporter'

export interface UPlotChartProps {
  readonly feed: MonitoringChartFeed
  readonly className?: string
  readonly onZoom?: (range: { start: Date; end: Date }) => void
  readonly title?: string
  readonly description?: string
  readonly onRequestBudgetChange?: (maxPoints: number) => void
}

function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

export interface UPlotChartHandle {
  resetZoom: () => void
}

export const UPlotChart = memo(
  forwardRef<UPlotChartHandle, UPlotChartProps>(function UPlotChart(
    { feed, className, onZoom, title, description, onRequestBudgetChange },
    ref,
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
    const chartDebugRef = useRef<number | null>(null)
    const chartResizeCountRef = useRef(0)
    const suppressProgrammaticXScaleRef = useRef(false)
    const readyRef = useRef(false)
    const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(new Set())
    const onZoomRef = useRef(onZoom)
    onZoomRef.current = onZoom
    const reportRequestBudget = useRequestBudgetReporter(onRequestBudgetChange)

    useEffect(() => {
      feed.setTheme(theme)
    }, [feed, theme])

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
          visible: !hiddenKeys.has(series.key),
        }))
    }, [hiddenKeys, structural.series])

    const handleToggle = (index: number, show: boolean): void => {
      const key = structuralRef.current.series[index - 1]?.key
      if (key !== undefined) {
        setHiddenKeys((previous) => {
          const next = new Set(previous)
          if (show) next.delete(key)
          else next.add(key)
          return next
        })
      }
      plotRef.current?.setSeries(index, { show })
    }

    const handleReset = (): void => {
      setHiddenKeys(new Set())
      const plot = plotRef.current
      if (plot === null) return
      structuralRef.current.series.forEach((_series, index) => {
        plot.setSeries(index + 1, { show: true }, false)
      })
    }

    useImperativeHandle(ref, () => ({
      resetZoom: () => {
        const plot = plotRef.current
        if (plot === null) return
        const range = structuralRef.current.range
        const bounds = range.kind === 'fixed'
          ? { min: range.start.getTime(), max: range.end.getTime() }
          : fullDataRange(feed)
        if (bounds === null) return
        suppressProgrammaticXScaleRef.current = true
        plot.setScale('x', bounds)
        suppressProgrammaticXScaleRef.current = false
           },
    }), [feed])

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
        reportRequestBudget(width)
        const existingPlot = plotRef.current
        if (existingPlot !== null) {
          chartResizeCountRef.current += 1
          if (PERFORMANCE_MARKS_ENABLED) {
            measureMonitoringResize(() => existingPlot.setSize({ width, height }))
          } else {
            existingPlot.setSize({ width, height })
          }
          const chartDebug = chartDebugRef.current
          if (chartDebug !== null) updateMonitoringChart(chartDebug, { width, height, resizeCount: chartResizeCountRef.current })
          return
        }
        const data = feed.getData()
        const plot = new uPlot(
          buildOptions(data, width, height, {
            onSetScale: (self, scaleKey) => {
              if (scaleKey !== 'x') return
              const chartDebug = chartDebugRef.current
              const { min, max } = self.scales.x
              if (chartDebug !== null) {
                updateMonitoringChart(chartDebug, { xScaleMin: min ?? null, xScaleMax: max ?? null })
              }
              if (!readyRef.current) {
                readyRef.current = true
                return
              }
              if (suppressProgrammaticXScaleRef.current) return
              if (min === undefined || max === undefined) return
              const data = feed.getData()
              const recordedEnd = data.x[data.nowIndex]
              const selectedEnd = recordedEnd !== undefined && min < recordedEnd && recordedEnd < max
                ? recordedEnd
                : max
              onZoomRef.current?.({ start: new Date(min), end: new Date(selectedEnd) })
             },
            onSetSeries: (_self, seriesIndex, options) => {
              if (seriesIndex === null || options.show === undefined) return
              const key = structuralRef.current.series[seriesIndex - 1]?.key
              if (key === undefined) return
              visibilityRef.current.set(key, options.show)
              setHiddenKeys((previous) => {
                const next = new Set(previous)
                if (options.show) next.delete(key)
                else next.add(key)
                return next
              })
            },
          }),
          PERFORMANCE_MARKS_ENABLED ? measureMonitoringConversion(() => toUPlotData(data)) : toUPlotData(data),
          container,
        )
        plotRef.current = plot
        structural.series.forEach((series, index) => {
          const show = visibilityRef.current.get(series.key)
          if (show !== undefined) plot.setSeries(index + 1, { show }, false)
        })
        lastAppliedViewportRevisionRef.current = structural.viewportRevision
        chartResizeCountRef.current = 0
        chartDebugRef.current = registerMonitoringChart({ title: title ?? 'Monitoring chart', width, height, xScaleMin: plot.scales.x.min ?? null, xScaleMax: plot.scales.x.max ?? null, viewportRevision: structural.viewportRevision, destroyCount: 0, resizeCount: 0 })
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

    const updateData = useCallback((): void => {
      if (feed.getStructuralSnapshot() !== structuralRef.current) return
      const plot = plotRef.current
      if (plot === null) return
      const currentStructural = feed.getStructuralSnapshot()
      const data = feed.getData()
      suppressProgrammaticXScaleRef.current = true
      if (PERFORMANCE_MARKS_ENABLED) {
        const converted = measureMonitoringConversion(() => toUPlotData(data))
        measureMonitoringSetData(() => plot.setData(converted, false))
      } else {
        plot.setData(toUPlotData(data), false)
      }
      if (currentStructural.viewportRevision !== lastAppliedViewportRevisionRef.current) {
        const rangeBounds = fullDataRange(feed)
        if (rangeBounds !== null) plot.setScale('x', rangeBounds)
        lastAppliedViewportRevisionRef.current = currentStructural.viewportRevision
      }
      const chartDebug = chartDebugRef.current
      if (chartDebug !== null) updateMonitoringChart(chartDebug, { viewportRevision: currentStructural.viewportRevision })
      suppressProgrammaticXScaleRef.current = false
    }, [feed])

    useEffect(() => feed.subscribe(updateData), [feed, updateData])

    const descId = title === undefined ? undefined : `mon-chart-desc-${slugify(title)}`
    return (
      <div className="mon-chart">
        <div ref={frameRef} className="mon-chart__frame">
          <div ref={containerRef} className={className} role="img" aria-label={title ?? 'Monitoring chart'} aria-describedby={descId} style={{ position: 'absolute', inset: 0 }} />
        </div>
        {description !== undefined && descId !== undefined && <p id={descId} className="mon-chart__desc">{description}</p>}
        <ExternalLegend entries={legendEntries} onToggle={handleToggle} onReset={handleReset} />
      </div>
    )
  }),
)

function fullDataRange(feed: MonitoringChartFeed): { min: number; max: number } | null {
  const x = feed.getData().x
  const start = x[0]
  const end = x[x.length - 1]
  return start === undefined || end === undefined ? null : { min: start, max: end }
}
