/**
 * Direct imperative uPlot adapter for the monitoring feature.
 *
 * Owns one uPlot instance per mount: creates it on the container, resizes it
 * via ResizeObserver, pushes new aligned data with `setData` (never recreating
 * for data), and destroys/recreates only when the theme revision changes —
 * preserving data, x-range/zoom, and series visibility across the recreate.
 *
 * Renders an external semantic legend whose swatches toggle series visibility
 * through `plot.setSeries`, and exposes an imperative `resetZoom` handle that
 * restores the original x range.
 *
 * The uPlot CSS is imported here so monitoring styling stays lazy: it is only
 * loaded when this component is actually rendered.
 */
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { useTheme } from '../../../contexts/ThemeContext'
import type { AlignedData } from '../data'
import { ExternalLegend, type LegendEntry } from './legend/ExternalLegend'
import { isEnvelopeSeries, seriesColor } from './options/seriesOptions'
import { buildOptions, toUPlotData } from './uPlotOptions'

export interface UPlotChartProps {
  data: AlignedData
  className?: string
  onZoom?: (range: { start: Date; end: Date }) => void
  /** Original x range restored by `resetZoom`. */
  range?: { start: Date; end: Date }
}

export interface UPlotChartHandle {
  resetZoom: () => void
}

interface XRange {
  min: number
  max: number
}

function measure(el: HTMLElement): { width: number; height: number } {
  const width = el.clientWidth > 0 ? el.clientWidth : 600
  const height = el.clientHeight > 0 ? el.clientHeight : 300
  return { width, height }
}

export const UPlotChart = forwardRef<UPlotChartHandle, UPlotChartProps>(
  function UPlotChart({ data, className, onZoom, range }, ref) {
    const { theme } = useTheme()

    const containerRef = useRef<HTMLDivElement>(null)
    const plotRef = useRef<uPlot | null>(null)
    const observerRef = useRef<ResizeObserver | null>(null)
    const visibilityRef = useRef<Map<string, boolean>>(new Map())
    const xRangeRef = useRef<XRange | null>(null)
    const readyRef = useRef(false)
    const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(new Set())

    const dataRef = useRef(data)
    dataRef.current = data
    const onZoomRef = useRef(onZoom)
    onZoomRef.current = onZoom
    const rangeRef = useRef(range)
    rangeRef.current = range

    const legendEntries = useMemo<LegendEntry[]>(() => {
      return data.series
        .map((s, i) => ({ s, index: i + 1 }))
        .filter(({ s }) => !isEnvelopeSeries(s))
        .map(({ s, index }) => ({
          key: s.key,
          label: s.label,
          color: seriesColor(s),
          projected: s.origin === 'projected',
          index,
          visible: !hiddenKeys.has(s.key),
        }))
    }, [data, hiddenKeys])

    const handleToggle = (index: number, show: boolean): void => {
      const key = dataRef.current.series[index - 1]?.key
      if (key !== undefined) {
        setHiddenKeys((prev) => {
          const next = new Set(prev)
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
      dataRef.current.series.forEach((_s, idx) => {
        plot.setSeries(idx + 1, { show: true }, false)
      })
    }

    useImperativeHandle(
      ref,
      () => ({
        resetZoom: () => {
          const plot = plotRef.current
          if (plot === null) return
          const r = rangeRef.current
          if (r !== undefined) {
            plot.setScale('x', { min: r.start.getTime(), max: r.end.getTime() })
          } else {
            const x = dataRef.current.x
            if (x.length > 0) plot.setScale('x', { min: x[0], max: x[x.length - 1] })
          }
        },
      }),
      [],
    )

    useEffect(() => {
      const container = containerRef.current
      if (container === null) return

      readyRef.current = false
      const { width, height } = measure(container)

      const plot = new uPlot(
        buildOptions(dataRef.current, width, height, {
          onSetScale: (self, scaleKey) => {
            if (scaleKey !== 'x') return
            if (!readyRef.current) {
              readyRef.current = true
              return
            }
            const { min, max } = self.scales.x
            if (min === undefined || max === undefined) return
            onZoomRef.current?.({ start: new Date(min), end: new Date(max) })
          },
          onSetSeries: (_self, seriesIdx, opts) => {
            if (seriesIdx === null || opts.show === undefined) return
            const key = dataRef.current.series[seriesIdx - 1]?.key
            if (key === undefined) return
            visibilityRef.current.set(key, opts.show)
            setHiddenKeys((prev) => {
              const next = new Set(prev)
              if (opts.show) next.delete(key)
              else next.add(key)
              return next
            })
          },
        }),
        toUPlotData(dataRef.current),
        container,
      )
      plotRef.current = plot

      dataRef.current.series.forEach((s, idx) => {
        const show = visibilityRef.current.get(s.key)
        if (show !== undefined) plot.setSeries(idx + 1, { show }, false)
      })

      const range = xRangeRef.current
      if (range !== null) plot.setScale('x', range)

      const observer = new ResizeObserver(() => {
        const next = measure(container)
        plot.setSize({ width: next.width, height: next.height })
      })
      observer.observe(container)
      observerRef.current = observer

      return () => {
        const current = plotRef.current
        if (current !== null) {
          const sx = current.scales.x
          if (sx.min !== undefined && sx.max !== undefined) {
            xRangeRef.current = { min: sx.min, max: sx.max }
          }
        }
        observer.disconnect()
        observerRef.current = null
        plot.destroy()
        plotRef.current = null
      }
    }, [theme])

    useEffect(() => {
      plotRef.current?.setData(toUPlotData(data), false)
    }, [data])

    return (
      <div className="mon-chart">
        <div
          ref={containerRef}
          className={className}
          role="img"
          aria-label="Monitoring chart"
          style={{ width: '100%', height: '100%' }}
        />
        <ExternalLegend entries={legendEntries} onToggle={handleToggle} onReset={handleReset} />
      </div>
    )
  },
)
