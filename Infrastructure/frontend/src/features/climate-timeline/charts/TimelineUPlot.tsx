import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { photoperiodPlugin } from '../../monitoring/charts/plugins/photoperiodPlugin'
import { nowDividerPlugin } from '../../monitoring/charts/plugins/nowDividerPlugin'
import type { TimelinePhotoperiodInterval } from './envelopeSeries'
import { buildTimelineOptions, type TimelineSeriesMeta, type TimelineWindowMs } from './timelineOptions'
import { readTimelineToken } from './tokens'

/** Owner's original timeline colors — the two documented hardcoded exceptions. */
const SUN_BG = 'rgba(234, 179, 8, 0.45)'
const MOON_BG = 'rgba(168, 85, 247, 0.35)'

export interface TimelineUPlotProps {
  readonly data: uPlot.AlignedData
  readonly meta: readonly TimelineSeriesMeta[]
  readonly windowMs: TimelineWindowMs
  readonly photoperiod: readonly TimelinePhotoperiodInterval[]
  readonly nowX: number | null
  readonly revision: number
  readonly plugins?: readonly uPlot.Plugin[]
  readonly ariaLabel: string
}

export function TimelineUPlot({
  data,
  meta,
  windowMs,
  photoperiod,
  nowX,
  revision,
  plugins = [],
  ariaLabel,
}: TimelineUPlotProps) {
  const frameRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const plotRef = useRef<uPlot | null>(null)
  const lastRevisionRef = useRef<number | null>(null)
  const programmaticScaleTokenRef = useRef(0)
  const pendingProgrammaticScaleTokensRef = useRef<Set<number>>(new Set())
  const photoperiodRef = useRef(photoperiod)
  photoperiodRef.current = photoperiod
  const nowXRef = useRef(nowX)
  nowXRef.current = nowX
  const metaRef = useRef(meta)
  metaRef.current = meta

  const withProgrammaticScale = <T,>(action: () => T): T => {
    const token = programmaticScaleTokenRef.current + 1
    programmaticScaleTokenRef.current = token
    pendingProgrammaticScaleTokensRef.current.add(token)
    return action()
  }

  const consumeProgrammaticScale = (): boolean => {
    const pending = pendingProgrammaticScaleTokensRef.current
    const first = pending.values().next().value
    if (first === undefined) return false
    pending.delete(first)
    return true
  }

  const shapeKey = meta.map((entry) => entry.key).join('|')

  useEffect(() => {
    const frameElement = frameRef.current
    const container = containerRef.current
    if (frameElement === null || container === null) return

    const basePlugins: uPlot.Plugin[] = [
      photoperiodPlugin(() => photoperiodRef.current, { sunBg: SUN_BG, moonBg: MOON_BG }),
      nowDividerPlugin(() => nowXRef.current, readTimelineToken('now')),
      ...plugins,
    ]

    let frame: number | null = null
    const applyFrameSize = (): void => {
      const { clientWidth, clientHeight } = frameElement
      if (clientWidth <= 0 || clientHeight <= 0) return
      const existingPlot = plotRef.current
      if (existingPlot !== null) {
        withProgrammaticScale(() => existingPlot.setSize({ width: clientWidth, height: clientHeight }))
        return
      }
      const plot = withProgrammaticScale(() => new uPlot(
        buildTimelineOptions(
          metaRef.current,
          clientWidth,
          clientHeight,
          windowMs,
          { onSetScale: () => { consumeProgrammaticScale() } },
          basePlugins,
        ),
        data,
        container,
      ))
      plotRef.current = plot
      lastRevisionRef.current = revision
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
      plotRef.current?.destroy()
      plotRef.current = null
      lastRevisionRef.current = null
    }
    // Re-init only on rare structural changes (series shape or window); data
    // revisions take the setData path below, never a destroy.
  }, [shapeKey, windowMs.start, windowMs.end]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const plot = plotRef.current
    if (plot === null) return
    if (lastRevisionRef.current !== null && revision <= lastRevisionRef.current) return
    lastRevisionRef.current = revision
    plot.setData(data, false)
    plot.redraw()
  }, [data, revision])

  return (
    <div ref={frameRef} className="relative h-full w-full">
      <div
        ref={containerRef}
        role="img"
        aria-label={ariaLabel}
        data-testid="control-timeline-uplot"
        style={{ position: 'absolute', inset: 0 }}
      />
    </div>
  )
}

export { SUN_BG, MOON_BG }
