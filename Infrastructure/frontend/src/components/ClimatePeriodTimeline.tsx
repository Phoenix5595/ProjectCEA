import { useMemo, useState, useEffect } from 'react'

export interface ClimatePeriod {
  period_name: string
  start_time: string
  end_time: string
  ramp_minutes: number
  heating_setpoint: number | null
  cooling_setpoint: number | null
  vpd_setpoint: number | null
  co2_setpoint: number | null
  details: string
}

export interface ClimatePeriodTimelineProps {
  periods: ClimatePeriod[]
  lightDayStart: string
  lightDayEnd: string
  compact?: boolean
  className?: string
}

function timeToMinutes(time: string): number {
  const [hours, minutes] = time.split(':').map(Number)
  return hours * 60 + minutes
}

function getCurrentTimeMinutes(): number {
  const now = new Date()
  return now.getHours() * 60 + now.getMinutes()
}

function drawSunBand(
  startMin: number,
  endMin: number,
  _color: string,
  getPosition: (m: number) => number
): { left: number; width: number }[] {
  const segments: { left: number; width: number }[] = []
  const wrappedEnd = endMin < startMin ? endMin + 1440 : endMin
  const actualEnd = Math.min(wrappedEnd, 1440)
  const left = getPosition(startMin % 1440)
  const width = getPosition(actualEnd) - left
  if (width > 0) segments.push({ left, width })
  if (wrappedEnd > 1440) {
    const left2 = 0
    const width2 = getPosition(endMin - 1440)
    if (width2 > 0) segments.push({ left: left2, width: width2 })
  }
  return segments
}

export default function ClimatePeriodTimeline({
  periods: _periods,
  lightDayStart,
  lightDayEnd,
  compact: _compact = false,
  className = ''
}: ClimatePeriodTimelineProps) {
  const [isDarkMode, setIsDarkMode] = useState(false)

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    setIsDarkMode(mediaQuery.matches)
    const handleChange = (e: MediaQueryListEvent) => setIsDarkMode(e.matches)
    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [])

  const { dayStartMin, dayEndMin, nowMin } = useMemo(() => {
    const dayStart = timeToMinutes(lightDayStart)
    const dayEnd = timeToMinutes(lightDayEnd)
    const now = getCurrentTimeMinutes()

    return { dayStartMin: dayStart, dayEndMin: dayEnd, nowMin: now }
  }, [lightDayStart, lightDayEnd])

  const getPosition = (minutes: number): number => (minutes / 1440) * 100
  const nowPercent = (nowMin / 1440) * 100

  const sunColor = isDarkMode ? 'rgba(154, 52, 18, 0.3)' : 'rgba(251, 146, 60, 0.3)'
  const sunRampColor = isDarkMode ? 'rgba(154, 52, 18, 0.5)' : 'rgba(251, 146, 60, 0.4)'
  const lightRampUp = 15
  const lightRampDown = 15

  const tempScalePositions: { value: number; top: number }[] = [
    { value: 30, top: ((30 - 30) / (30 - 15)) * 100 },
    { value: 25, top: ((30 - 25) / (30 - 15)) * 100 },
    { value: 20, top: ((30 - 20) / (30 - 15)) * 100 },
    { value: 15, top: ((30 - 15) / (30 - 15)) * 100 },
  ]

  const vpdScalePositions: { value: number; top: number }[] = [
    { value: 2, top: ((2 - 2) / (2 - 0.5)) * 100 },
    { value: 1.5, top: ((2 - 1.5) / (2 - 0.5)) * 100 },
    { value: 1, top: ((2 - 1) / (2 - 0.5)) * 100 },
    { value: 0.5, top: ((2 - 0.5) / (2 - 0.5)) * 100 },
  ]

  const tempGridPositions: number[] = [15, 20, 25, 30]

  return (
    <div className={`w-full ${className}`}>
      <div className="relative bg-surface-base border border-border-subtle rounded-lg overflow-hidden h-full">
        <div className="absolute left-0 top-2 bottom-4 w-6 z-20 pointer-events-none">
          {tempScalePositions.map(({ value, top }) => (
            <div
              key={`temp-${value}`}
              className="absolute text-[9px] text-text-muted font-medium text-right pr-0.5 bg-surface-base/80 px-0.5 rounded-r font-mono tabular-nums"
              style={{ top: `${top}%`, transform: 'translateY(-50%)', left: 0 }}
            >
              {value}&deg;
            </div>
          ))}
        </div>

        <div className="absolute right-0 top-2 bottom-4 w-6 z-20 pointer-events-none">
          {vpdScalePositions.map(({ value, top }) => (
            <div
              key={`vpd-${value}`}
              className="absolute text-[9px] text-accent-data font-medium text-left pl-0.5 bg-surface-base/80 px-0.5 rounded-l font-mono tabular-nums"
              style={{ top: `${top}%`, transform: 'translateY(-50%)', right: 0 }}
            >
              {value}
            </div>
          ))}
        </div>

        <div className="relative h-full pt-2 pl-6 pr-6">
          <div className="relative h-full">
            {Array.from({ length: 25 }).map((_, i) => (
              <div
                key={`hour-${i}`}
                className="absolute top-0 bottom-0 w-px bg-surface-secondary"
                style={{ left: `${(i / 24) * 100}%` }}
              />
            ))}

            {tempGridPositions.map((value) => {
              const top = ((30 - value) / (30 - 15)) * 100
              return (
                <div
                  key={`grid-${value}`}
                  className="absolute left-0 right-0 h-px bg-surface-secondary opacity-50"
                  style={{ top: `${top}%` }}
                />
              )
            })}

            <div className="absolute bottom-4 left-0 right-0 z-[1]" style={{ height: '4px' }}>
              {lightRampUp > 0 && (
                <div
                  className="absolute top-0 bottom-0 pointer-events-none"
                  style={{
                    left: `${getPosition(dayStartMin - lightRampUp)}%`,
                    width: `${getPosition(lightRampUp)}%`,
                    backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 4px, ${sunRampColor} 4px, ${sunRampColor} 8px)`,
                    backgroundColor: 'transparent',
                  }}
                />
              )}
              {(() => {
                const segments = drawSunBand(dayStartMin, dayEndMin, sunColor, getPosition)
                return segments.map((seg, i) => (
                  <div
                    key={`sun-${i}`}
                    className="absolute top-0 bottom-0 pointer-events-none"
                    style={{ left: `${seg.left}%`, width: `${seg.width}%`, backgroundColor: sunColor }}
                  />
                ))
              })()}
              {lightRampDown > 0 && (
                <div
                  className="absolute top-0 bottom-0 pointer-events-none"
                  style={{
                    left: `${getPosition(dayEndMin)}%`,
                    width: `${getPosition(lightRampDown)}%`,
                    backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 4px, ${sunRampColor} 4px, ${sunRampColor} 8px)`,
                    backgroundColor: 'transparent',
                  }}
                />
              )}
            </div>

            <div
              className="absolute top-0 bottom-0 w-0.5 bg-status-danger-vivid z-10"
              style={{ left: `${nowPercent}%` }}
            />
          </div>

          <div className="absolute bottom-0 left-6 right-6 h-4">
            {Array.from({ length: 13 }).map((_, i) => {
              const hour = i * 2
              return (
                <div
                  key={`label-${i}`}
                  className="absolute bottom-0 text-[10px] text-text-subtle font-medium font-mono tabular-nums"
                  style={{ left: `${(hour / 24) * 100}%`, transform: 'translateX(-50%)' }}
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
