import { useMemo } from 'react'

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

export default function ClimatePeriodTimeline({
  periods: _periods,
  lightDayStart,
  lightDayEnd,
  compact: _compact = false,
  className = ''
}: ClimatePeriodTimelineProps) {
  const { dayStartMin, dayEndMin, nowMin } = useMemo(() => {
    const dayStart = timeToMinutes(lightDayStart)
    const dayEnd = timeToMinutes(lightDayEnd)
    const now = getCurrentTimeMinutes()
    return { dayStartMin: dayStart, dayEndMin: dayEnd, nowMin: now }
  }, [lightDayStart, lightDayEnd])

  const getPosition = (minutes: number): number => (minutes / 1440) * 100
  const nowPercent = (nowMin / 1440) * 100

  const sunColor = 'rgba(234, 179, 8, 0.85)'
  const moonColor = 'rgba(168, 85, 247, 0.85)'

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

            <div className="absolute bottom-4 left-6 right-6 z-[1] h-6">
              <div className="relative w-full h-full">
                <div
                  className="absolute rounded-sm"
                  style={{
                    top: 0,
                    height: '24px',
                    left: `${getPosition(dayStartMin)}%`,
                    width: `${100 - getPosition(dayStartMin)}%`,
                    backgroundColor: sunColor,
                    zIndex: 1,
                  }}
                />
                <div
                  className="absolute rounded-sm"
                  style={{
                    top: 0,
                    height: '24px',
                    left: '0%',
                    width: `${getPosition(dayEndMin)}%`,
                    backgroundColor: sunColor,
                    zIndex: 1,
                  }}
                />
                <div
                  className="absolute rounded-sm"
                  style={{
                    top: 0,
                    height: '24px',
                    left: `${getPosition(dayEndMin)}%`,
                    width: `${getPosition(dayStartMin) - getPosition(dayEndMin)}%`,
                    backgroundColor: moonColor,
                    zIndex: 2,
                  }}
                />
              </div>
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
