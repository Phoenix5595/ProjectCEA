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
import { measureMonitoringConversion, measureMonitoringResize, measureMonitoringSetData, PERFORMANCE_MARKS_ENABLED } from '../perfMarks'
import { panelBudget, shouldReportBudget } from '../data/pointBudget'

import type { MonitoringChartFeed } from './MonitoringChartFeed'
import { measureChartContainer } from './chartSizing'
import { ExternalLegend, type LegendEntry } from './legend/ExternalLegend'
import { isEnvelopeSeries, seriesColor } from './options/seriesOptions'
import { buildOptions, toUPlotData } from './uPlotOptions'

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
    const containerRef = useRef<HTMLDivElement>(null)
    const plotRef = useRef<uPlot | null>(null)
    const visibilityRef = useRef<Map<string, boolean>>(new Map())
    const xRangeRef = useRef<{ min: number; max: number } | null>(null)
    const suppressProgrammaticXScaleRef = useRef(false)
    const readyRef = useRef(false)
    const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(new Set())
    const reportedRequestBudgetRef = useRef<number | null>(null)
    const onZoomRef = useRef(onZoom)
    onZoomRef.current = onZoom
    const onRequestBudgetChangeRef = useRef(onRequestBudgetChange)
    onRequestBudgetChangeRef.current = onRequestBudgetChange

    const reportRequestBudget = useCallback((width: number): void => {
      const nextBudget = panelBudget(width)
      if (!shouldReportBudget(reportedRequestBudgetRef.current, nextBudget)) return
      reportedRequestBudgetRef.current = nextBudget
      onRequestBudgetChangeRef.current?.(nextBudget)
    }, [])

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
      const container = containerRef.current
      if (container === null) return
      readyRef.current = false
      const { width, height } = measureChartContainer(container)
      reportRequestBudget(width)
      const data = feed.getData()
      const plot = new uPlot(
        buildOptions(data, width, height, {
          onSetScale: (self, scaleKey) => {
            if (scaleKey !== 'x') return
            if (suppressProgrammaticXScaleRef.current) {
              suppressProgrammaticXScaleRef.current = false
              return
            }
            if (!readyRef.current) {
              readyRef.current = true
              return
            }
            const { min, max } = self.scales.x
            if (min === undefined || max === undefined) return
            onZoomRef.current?.({ start: new Date(min), end: new Date(max) })
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
          onDraw: () => {
            suppressProgrammaticXScaleRef.current = false
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
      if (xRangeRef.current !== null) plot.setScale('x', xRangeRef.current)

      let frame: number | null = null
      const observer = new ResizeObserver(() => {
        if (frame !== null) return
        frame = requestAnimationFrame(() => {
          frame = null
          const next = measureChartContainer(container)
          reportRequestBudget(next.width)
          if (PERFORMANCE_MARKS_ENABLED) {
            measureMonitoringResize(() => plot.setSize({ width: next.width, height: next.height }))
          } else {
            plot.setSize({ width: next.width, height: next.height })
          }
        })
      })
      observer.observe(container)

      return () => {
        const scale = plot.scales.x
        if (scale.min !== undefined && scale.max !== undefined) xRangeRef.current = { min: scale.min, max: scale.max }
        if (frame !== null) cancelAnimationFrame(frame)
        observer.disconnect()
        plot.destroy()
        plotRef.current = null
      }
    }, [feed, reportRequestBudget, structural, theme])

    const updateData = useCallback((): void => {
      if (feed.getStructuralSnapshot() !== structuralRef.current) return
      const plot = plotRef.current
      if (plot === null) return
      suppressProgrammaticXScaleRef.current = true
      if (PERFORMANCE_MARKS_ENABLED) {
        const data = measureMonitoringConversion(() => toUPlotData(feed.getData()))
        measureMonitoringSetData(() => plot.setData(data, false))
      } else {
        plot.setData(toUPlotData(feed.getData()), false)
      }
      suppressProgrammaticXScaleRef.current = false
    }, [feed])

    useEffect(() => feed.subscribe(updateData), [feed, updateData])

    const descId = title === undefined ? undefined : `mon-chart-desc-${slugify(title)}`
    return (
      <div className="mon-chart">
        <div ref={containerRef} className={className} role="img" aria-label={title ?? 'Monitoring chart'} aria-describedby={descId} style={{ width: '100%', height: '100%' }} />
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
