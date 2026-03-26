import { useMemo } from 'react'
import type { ClimatePeriod } from '../types/climatePeriod'
import {
  sampleMetricSeries,
  timeToMinutes,
} from '../utils/climatePeriodTimeline'

export interface ClimatePeriodTimelineProps {
  periods?: ClimatePeriod[]
  lightDayStart: string
  lightDayEnd: string
  compact?: boolean
  className?: string
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

  const sunColor = 'rgba(234, 179, 8, 0.45)'
  const moonColor = 'rgba(168, 85, 247, 0.35)'

  const sunSegments =
    dayStartMin === dayEndMin ? [] : buildSegments(dayStartMin, dayEndMin)
  const moonSegments =
    dayStartMin === dayEndMin
      ? [{ startMin: 0, endMin: 1440 }]
      : buildSegments(dayEndMin, dayStartMin)

  const heatSeries = useMemo(
    () => sampleMetricSeries(periods, 'heating'),
    [periods]
  )
  const coolSeries = useMemo(
    () => sampleMetricSeries(periods, 'cooling'),
    [periods]
  )
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
    { value: 20, top: (200 / 3) },
    { value: 15, top: 100 },
  ]

  const vpdScalePositions = [
    { value: 2, top: 0 },
    { value: 1.5, top: 100 / 3 },
    { value: 1, top: (200 / 3) },
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

        <div className="relative h-full pt-2 pl-7 pr-7">
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
                  className="absolute h-full"
                  style={{
                    left: `${getPosition(segment.startMin)}%`,
                    width: `${getPosition(segment.endMin - segment.startMin)}%`,
                    backgroundColor: moonColor,
                  }}
                />
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

            <div
              className="absolute top-0 bottom-4 left-0 right-0 z-[7] flex items-start justify-end gap-2 pointer-events-none pr-1 pt-0.5"
            >
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
    </div>
  )
}
