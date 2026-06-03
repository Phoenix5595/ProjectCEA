import { useMemo, useState, useEffect, useRef } from 'react'
import type { ClimatePeriod } from '../types/climatePeriod'
import { sampleMetricSeries, timeToMinutes } from '../utils/climatePeriodTimeline'
import { minutesToTime } from '../utils/timeMath'

export interface ClimatePeriodTimelineProps {
  periods?: ClimatePeriod[]
  lightDayStart: string
  lightDayEnd: string
  compact?: boolean
  className?: string
  onDayStartChange?: (time: string) => void
  onDayEndChange?: (time: string) => void
  lockedPhotoperiodHours?: number | null
  rampUpDuration?: number | null
  rampDownDuration?: number | null
  onRampUpChange?: (minutes: number | null) => void
  onRampDownChange?: (minutes: number | null) => void
}

interface TimeSegment {
  startMin: number
  endMin: number
}

function buildSegments(startMin: number, endMin: number): TimeSegment[] {
  if (startMin === endMin) return []
  if (endMin > startMin) return [{ startMin, endMin }]
  return [
    { startMin, endMin: 1440 },
    { startMin: 0, endMin },
  ]
}

function buildPolylineSegments(
  series: (number | null)[],
  vmin: number,
  vmax: number
): string[] {
  const segments: string[][] = []
  let current: string[] = []
  const span = Math.max(vmax - vmin, 1e-6)
  for (let m = 0; m <= 1440; m++) {
    const v = m === 1440 ? series[0] : series[m]
    if (v == null) {
      if (current.length) {
        segments.push(current)
        current = []
      }
      continue
    }
    const x = (m / 1440) * 100
    const y = 100 * (1 - (v - vmin) / span)
    current.push(`${x},${y}`)
  }
  if (current.length) segments.push(current)
  return segments.map((s) => s.join(' '))
}

export default function ClimatePeriodTimeline({
  periods = [],
  lightDayStart,
  lightDayEnd,
  compact: _compact = false,
  className = '',
  onDayStartChange,
  onDayEndChange,
  lockedPhotoperiodHours = null,
  rampUpDuration = null,
  rampDownDuration = null,
  onRampUpChange,
  onRampDownChange,
}: ClimatePeriodTimelineProps) {
  const { dayStartMin, dayEndMin, nowMin } = useMemo(() => {
    const dayStart = timeToMinutes(lightDayStart)
    const dayEnd = timeToMinutes(lightDayEnd)
    const now = new Date()
    return {
      dayStartMin: dayStart,
      dayEndMin: dayEnd,
      nowMin: now.getHours() * 60 + now.getMinutes(),
    }
  }, [lightDayStart, lightDayEnd])

  const getPosition = (minutes: number): number => (minutes / 1440) * 100

  const timelineRef = useRef<HTMLDivElement>(null)
  const [dragTarget, setDragTarget] = useState<'start' | 'end' | 'body' | null>(null)
  const [dragOffsetMinutes, setDragOffsetMinutes] = useState(0)
  const [showRampPopover, setShowRampPopover] = useState(false)
  const [popoverPosition, setPopoverPosition] = useState({ x: 0, y: 0 })
  const [rampUpInput, setRampUpInput] = useState(rampUpDuration ?? 0)
  const [rampDownInput, setRampDownInput] = useState(rampDownDuration ?? 0)

  const snapTo5 = (minutes: number): number => Math.round(minutes / 5) * 5
  const clampMinutes = (m: number): number => Math.max(0, Math.min(1435, m))

  const handleEdgeMouseDown = (target: 'start' | 'end') => (e: React.MouseEvent) => {
    e.stopPropagation()
    const rect = timelineRef.current?.getBoundingClientRect()
    if (!rect) return
    const fractionX = (e.clientX - rect.left) / rect.width
    const rawMinutes = Math.round(fractionX * 1440)
    setDragTarget(target)
    setDragOffsetMinutes(0)
    const snappedTarget = clampMinutes(snapTo5(rawMinutes))
    const time = minutesToTime(snappedTarget)

    if (lockedPhotoperiodHours != null) {
      const lockedDuration = lockedPhotoperiodHours * 60
      if (target === 'start') {
        onDayStartChange?.(time)
        const newEndMinutes = (snappedTarget + lockedDuration) % 1440
        onDayEndChange?.(minutesToTime(newEndMinutes))
      } else {
        onDayEndChange?.(time)
        const newStartMinutes = (snappedTarget - lockedDuration + 1440) % 1440
        onDayStartChange?.(minutesToTime(newStartMinutes))
      }
    } else {
      if (target === 'start') onDayStartChange?.(time)
      else onDayEndChange?.(time)
    }
  }

  const handleBodyMouseDown = (e: React.MouseEvent) => {
    const rect = timelineRef.current?.getBoundingClientRect()
    if (!rect) return
    const fractionX = (e.clientX - rect.left) / rect.width
    const clickMinutes = Math.round(fractionX * 1440)
    setDragTarget('body')
    setDragOffsetMinutes(clickMinutes - dayStartMin)
  }

  useEffect(() => {
    if (!dragTarget) return

    const handleMouseMove = (e: MouseEvent) => {
      const rect = timelineRef.current?.getBoundingClientRect()
      if (!rect) return
      const fractionX = (e.clientX - rect.left) / rect.width
      const rawMinutes = Math.round(fractionX * 1440)
      const snapped = clampMinutes(snapTo5(rawMinutes))
      const time = minutesToTime(snapped)

      if (dragTarget === 'start') {
        if (lockedPhotoperiodHours != null) {
          const dur = lockedPhotoperiodHours * 60
          onDayStartChange?.(time)
          onDayEndChange?.(minutesToTime((snapped + dur) % 1440))
        } else {
          onDayStartChange?.(time)
        }
      } else if (dragTarget === 'end') {
        if (lockedPhotoperiodHours != null) {
          const dur = lockedPhotoperiodHours * 60
          onDayEndChange?.(time)
          onDayStartChange?.(minutesToTime((snapped - dur + 1440) % 1440))
        } else {
          onDayEndChange?.(time)
        }
      } else if (dragTarget === 'body') {
        const newStart = clampMinutes(snapTo5(snapped - dragOffsetMinutes))
        const startTime = minutesToTime(newStart)
        if (lockedPhotoperiodHours != null) {
          const dur = lockedPhotoperiodHours * 60
          onDayStartChange?.(startTime)
          onDayEndChange?.(minutesToTime((newStart + dur) % 1440))
        } else {
          onDayStartChange?.(startTime)
          const delta = newStart - dayStartMin
          const newEnd = clampMinutes((dayEndMin + delta + 1440) % 1440)
          onDayEndChange?.(minutesToTime(newEnd))
        }
      }
    }

    const handleMouseUp = () => setDragTarget(null)

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [dragTarget, dragOffsetMinutes, dayStartMin, dayEndMin, lockedPhotoperiodHours, onDayStartChange, onDayEndChange])

  useEffect(() => {
    if (!showRampPopover) return
    const handleClickOutside = (e: MouseEvent) => {
      const popover = document.querySelector('[data-testid="timeline-ramp-popover"]')
      if (popover && !popover.contains(e.target as Node)) {
        setShowRampPopover(false)
      }
    }
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowRampPopover(false)
    }
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleEscape)
    }, 0)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [showRampPopover])

  const sunColor = 'rgba(234, 179, 8, 0.45)'
  const moonColor = 'rgba(168, 85, 247, 0.35)'

  const sunSegments =
    dayStartMin === dayEndMin ? [] : buildSegments(dayStartMin, dayEndMin)
  const moonSegments =
    dayStartMin === dayEndMin
      ? [{ startMin: 0, endMin: 1440 }]
      : buildSegments(dayEndMin, dayStartMin)

  const heatSeries = useMemo(() => sampleMetricSeries(periods, 'heating'), [periods])
  const coolSeries = useMemo(() => sampleMetricSeries(periods, 'cooling'), [periods])
  const vpdSeries = useMemo(() => sampleMetricSeries(periods, 'vpd'), [periods])

  const tempRange = { min: 15, max: 30 }
  const vpdRange = { min: 0.5, max: 2.0 }

  const heatPolylines = useMemo(() => {
    if (!tempRange) return []
    return buildPolylineSegments(heatSeries, tempRange.min, tempRange.max)
  }, [heatSeries, tempRange])

  const coolPolylines = useMemo(() => {
    if (!tempRange) return []
    return buildPolylineSegments(coolSeries, tempRange.min, tempRange.max)
  }, [coolSeries, tempRange])

  const vpdPolylines = useMemo(() => {
    if (!vpdRange) return []
    return buildPolylineSegments(vpdSeries, vpdRange.min, vpdRange.max)
  }, [vpdSeries, vpdRange])

  const tempScalePositions = [
    { value: 30, top: 0 },
    { value: 25, top: 100 / 3 },
    { value: 20, top: 200 / 3 },
    { value: 15, top: 100 },
  ]

  const vpdScalePositions = [
    { value: 2, top: 0 },
    { value: 1.5, top: 100 / 3 },
    { value: 1, top: 200 / 3 },
    { value: 0.5, top: 100 },
  ]

  const hasPeriods = periods.length > 0

  return (
    <div className={`w-full ${className}`}>
      <div className="relative bg-surface-base border border-border-subtle rounded-lg overflow-hidden h-full">
        <div className="absolute left-0 top-2 bottom-4 w-7 z-20 pointer-events-none">
          {tempScalePositions.map(({ value, top }) => (
            <div
              key={`temp-${value}-${top}`}
              className="absolute text-[9px] text-text-muted font-medium text-right pr-0.5 bg-surface-base/80 px-0.5 rounded-r font-mono tabular-nums"
              style={{ top: `${top}%`, transform: 'translateY(-50%)', left: 0 }}
            >
              {value}&deg;
            </div>
          ))}
        </div>

        <div className="absolute right-0 top-2 bottom-4 w-7 z-20 pointer-events-none">
          {vpdScalePositions.map(({ value, top }) => (
            <div
              key={`vpd-${value}-${top}`}
              className="absolute text-[9px] text-accent-data font-medium text-left pl-0.5 bg-surface-base/80 px-0.5 rounded-l font-mono tabular-nums"
              style={{ top: `${top}%`, transform: 'translateY(-50%)', right: 0 }}
            >
              {value}
            </div>
          ))}
        </div>

        <div
          ref={timelineRef}
          className="relative h-full pt-2 pl-7 pr-7"
          style={{ cursor: dragTarget === 'body' ? 'grabbing' : undefined }}
        >
          <div className="relative h-full">
            {Array.from({ length: 25 }).map((_, i) => (
              <div
                key={`hour-${i}`}
                className="absolute top-0 bottom-0 w-px bg-surface-secondary z-[2]"
                style={{ left: `${(i / 24) * 100}%` }}
              />
            ))}

            <div className="absolute top-0 bottom-4 left-0 right-0 z-[1]">
              {moonSegments.map((segment, index) => (
                <div
                  key={`moon-${index}-${segment.startMin}-${segment.endMin}`}
                  className="absolute h-full cursor-grab"
                  data-testid="timeline-night-band"
                  style={{
                    left: `${getPosition(segment.startMin)}%`,
                    width: `${getPosition(segment.endMin - segment.startMin)}%`,
                    backgroundColor: moonColor,
                  }}
                  onMouseDown={handleBodyMouseDown}
                  onContextMenu={(e) => {
                    e.preventDefault()
                    setRampUpInput(rampUpDuration ?? 0)
                    setRampDownInput(rampDownDuration ?? 0)
                    setPopoverPosition({ x: e.clientX + 8, y: e.clientY + 8 })
                    setShowRampPopover(true)
                  }}
                >
                  <div
                    className="absolute left-0 top-0 h-full w-5 cursor-ew-resize z-10 opacity-60 hover:opacity-100 transition-opacity"
                    style={{
                      borderLeft: '2px solid #06b6d4',
                      backgroundColor: 'rgba(6, 182, 212, 0.1)',
                      cursor: 'ew-resize',
                    }}
                    data-testid="timeline-handle-night-start"
                    onMouseDown={handleEdgeMouseDown('end')}
                  />
                  <div
                    className="absolute right-0 top-0 h-full w-5 cursor-ew-resize z-10 opacity-60 hover:opacity-100 transition-opacity"
                    style={{
                      borderRight: '2px solid #06b6d4',
                      backgroundColor: 'rgba(6, 182, 212, 0.1)',
                      cursor: 'ew-resize',
                    }}
                    data-testid="timeline-handle-night-end"
                    onMouseDown={handleEdgeMouseDown('start')}
                  />
                  {rampUpDuration != null && rampUpDuration > 0 && (
                    <div
                      data-testid="timeline-ramp-up-gradient"
                      className="absolute left-0 top-0 h-full pointer-events-none"
                      style={{
                        width: `${(rampUpDuration / 1440) * 100}%`,
                        background: 'linear-gradient(to right, rgba(234,179,8,0), rgba(234,179,8,0.45))',
                      }}
                    />
                  )}
                  {rampDownDuration != null && rampDownDuration > 0 && (
                    <div
                      data-testid="timeline-ramp-down-gradient"
                      className="absolute right-0 top-0 h-full pointer-events-none"
                      style={{
                        width: `${(rampDownDuration / 1440) * 100}%`,
                        background: 'linear-gradient(to left, rgba(234,179,8,0), rgba(234,179,8,0.45))',
                      }}
                    />
                  )}
                </div>
              ))}

              {sunSegments.map((segment, index) => (
                <div
                  key={`sun-${index}-${segment.startMin}-${segment.endMin}`}
                  className="absolute h-full"
                  style={{
                    left: `${getPosition(segment.startMin)}%`,
                    width: `${getPosition(segment.endMin - segment.startMin)}%`,
                    backgroundColor: sunColor,
                  }}
                />
              ))}
            </div>

            {hasPeriods && (
              <div className="absolute top-0 bottom-4 left-0 right-0 z-[5] pointer-events-none">
                <svg
                  className="w-full h-full overflow-visible"
                  viewBox="0 0 100 100"
                  preserveAspectRatio="none"
                  aria-hidden
                >
                  {heatPolylines.map((points, i) => (
                    <polyline
                      key={`heat-${i}`}
                      fill="none"
                      stroke="rgb(234 88 12)"
                      strokeWidth={0.9}
                      vectorEffect="non-scaling-stroke"
                      points={points}
                    />
                  ))}
                  {coolPolylines.map((points, i) => (
                    <polyline
                      key={`cool-${i}`}
                      fill="none"
                      stroke="rgb(59 130 246)"
                      strokeWidth={0.9}
                      vectorEffect="non-scaling-stroke"
                      points={points}
                    />
                  ))}
                  {vpdPolylines.map((points, i) => (
                    <polyline
                      key={`vpd-${i}`}
                      fill="none"
                      stroke="rgb(34 197 94)"
                      strokeWidth={0.9}
                      vectorEffect="non-scaling-stroke"
                      points={points}
                    />
                  ))}
                </svg>
              </div>
            )}

            <div className="absolute top-0 bottom-4 left-0 right-0 z-[7] flex items-start justify-end gap-2 pointer-events-none pr-1 pt-0.5">
              {hasPeriods && (
                <>
                  <span className="text-[8px] font-mono text-orange-600 dark:text-orange-400">
                    heat
                  </span>
                  <span className="text-[8px] font-mono text-blue-500">cool</span>
                  <span className="text-[8px] font-mono text-green-500">VPD</span>
                </>
              )}
            </div>

            <div
              className="absolute top-0 bottom-0 w-0.5 bg-status-danger-vivid z-10"
              style={{ left: `${getPosition(nowMin)}%` }}
            />
          </div>

          <div className="absolute bottom-0 left-7 right-7 h-4">
            {Array.from({ length: 13 }).map((_, i) => {
              const hour = i * 2
              return (
                <div
                  key={`label-${i}`}
                  className="absolute bottom-0 text-[10px] text-text-subtle font-medium font-mono tabular-nums"
                  style={{
                    left: `${(hour / 24) * 100}%`,
                    transform: 'translateX(-50%)',
                  }}
                >
                  {String(hour).padStart(2, '0')}
                </div>
              )
            })}
          </div>
        </div>
      </div>
      {showRampPopover && (
        <div
          data-testid="timeline-ramp-popover"
          className="fixed z-50 bg-surface-primary border border-border-default rounded-lg shadow-lg p-3 flex flex-col gap-2 min-w-[180px]"
          style={{ left: popoverPosition.x, top: popoverPosition.y }}
        >
          <div className="flex flex-col gap-1">
            <label className="text-[12px] text-text-muted">Ramp up (min)</label>
            <input
              aria-label="Ramp up (min)"
              type="number"
              min={0}
              max={180}
              value={rampUpInput}
              onChange={(e) => setRampUpInput(parseInt(e.target.value) || 0)}
              className="w-full h-6 px-1 text-center bg-surface-secondary border border-border-default rounded text-[14px] text-text-input [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[12px] text-text-muted">Ramp down (min)</label>
            <input
              aria-label="Ramp down (min)"
              type="number"
              min={0}
              max={180}
              value={rampDownInput}
              onChange={(e) => setRampDownInput(parseInt(e.target.value) || 0)}
              className="w-full h-6 px-1 text-center bg-surface-secondary border border-border-default rounded text-[14px] text-text-input [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            />
          </div>
          <button
            onClick={() => {
              onRampUpChange?.(rampUpInput)
              onRampDownChange?.(rampDownInput)
              setShowRampPopover(false)
            }}
            className="mt-1 px-3 py-1 bg-btn-primary text-white rounded text-xs font-medium hover:opacity-90"
          >
            Done
          </button>
        </div>
      )}
    </div>
  )
}
