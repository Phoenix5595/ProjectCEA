import { useMemo } from 'react'

interface SetpointTimelineProps {
  dayStartTime: string
  dayEndTime: string
  preDayDuration: number
  preNightDuration: number
  onDayStartChange: (time: string) => void
  onDayEndChange: (time: string) => void
  onPreDayDurationChange: (duration: number) => void
  onPreNightDurationChange: (duration: number) => void
  lightPhotoperiod?: { startTime: string; endTime: string; rampUpDuration?: number; rampDownDuration?: number }
  setpoints?: {
    DAY?: any
    NIGHT?: any
    PRE_DAY?: any
    PRE_NIGHT?: any
  }
}

export default function SetpointTimeline({
  dayStartTime,
  dayEndTime,
  preDayDuration,
  preNightDuration,
  onDayStartChange: _onDayStartChange,
  onDayEndChange: _onDayEndChange,
  onPreDayDurationChange,
  onPreNightDurationChange,
  lightPhotoperiod,
  setpoints: _setpoints
}: SetpointTimelineProps) {
  // Convert time string (HH:MM) to minutes since midnight
  function timeToMinutes(time: string): number {
    const [hours, minutes] = time.split(':').map(Number)
    return hours * 60 + minutes
  }

  // Calculate period positions
  const periods = useMemo(() => {
    const dayStartMin = timeToMinutes(dayStartTime)
    const dayEndMin = timeToMinutes(dayEndTime)
    const preDayStartMin = (dayStartMin - preDayDuration + 1440) % 1440
    const preDayEndMin = dayStartMin
    const nightStartMin = dayEndMin
    const preNightStartMin = nightStartMin
    const preNightEndMin = (nightStartMin + preNightDuration) % 1440
    const nightEndMin = preDayStartMin

    return {
      preDay: { start: preDayStartMin, end: preDayEndMin },
      day: { start: dayStartMin, end: dayEndMin },
      preNight: { start: preNightStartMin, end: preNightEndMin },
      night: { start: preNightEndMin, end: nightEndMin }
    }
  }, [dayStartTime, dayEndTime, preDayDuration, preNightDuration])

  // Get current time
  const currentTime = useMemo(() => {
    const now = new Date()
    return now.getHours() * 60 + now.getMinutes()
  }, [])

  // Calculate percentage positions for 24-hour timeline
  function getPosition(minutes: number): number {
    return (minutes / 1440) * 100
  }

  return (
    <div className="w-full">
      <div className="relative bg-gray-50 border border-gray-300 rounded-lg overflow-hidden flex">
        {/* Y-axis labels - Temperature (left) */}
        <div className="flex-shrink-0 w-12 border-r border-gray-300 bg-gray-50 relative">
          <div className="relative h-64">
            {[35, 30, 25, 20, 15].map((value) => (
              <div
                key={value}
                className="absolute text-xs text-gray-700 font-medium text-right pr-2"
                style={{ 
                  top: `${((35 - value) / (35 - 15)) * 100}%`,
                  transform: 'translateY(-50%)',
                  width: '100%'
                }}
              >
                {value}
              </div>
            ))}
          </div>
          {/* Spacer for hour labels */}
          <div className="h-8"></div>
        </div>
        
        {/* Timeline content area */}
        <div className="flex-1 relative">
          <div className="relative h-64">
          {/* Mode period backgrounds */}
          <div
            className="absolute top-0 bottom-0 bg-blue-100 opacity-30"
            style={{
              left: `${getPosition(periods.preDay.start)}%`,
              width: `${getPosition((periods.preDay.end - periods.preDay.start + 1440) % 1440)}%`
            }}
          />
          <div
            className="absolute top-0 bottom-0 bg-yellow-100 opacity-30"
            style={{
              left: `${getPosition(periods.day.start)}%`,
              width: `${getPosition(periods.day.end - periods.day.start)}%`
            }}
          />
          <div
            className="absolute top-0 bottom-0 bg-purple-100 opacity-30"
            style={{
              left: `${getPosition(periods.preNight.start)}%`,
              width: `${getPosition((periods.preNight.end - periods.preNight.start + 1440) % 1440)}%`
            }}
          />
          <div
            className="absolute top-0 bottom-0 opacity-30"
            style={{
              left: `${getPosition(periods.night.start)}%`,
              width: `${getPosition((periods.night.end - periods.night.start + 1440) % 1440)}%`,
              backgroundColor: 'rgba(75, 85, 200, 0.3)' // Purplish blue
            }}
          />

          {/* Light photoperiod overlay */}
          {lightPhotoperiod && (() => {
            const startMin = timeToMinutes(lightPhotoperiod.startTime)
            const endMin = timeToMinutes(lightPhotoperiod.endTime)
            const rampUpDuration = lightPhotoperiod.rampUpDuration || 0
            const rampDownDuration = lightPhotoperiod.rampDownDuration || 0
            
            // Ramp up happens at the start of photoperiod (within the photoperiod)
            const rampUpStartMin = startMin
            const rampUpEndMin = (startMin + rampUpDuration) % 1440
            
            // Ramp down happens at the end of photoperiod (within the photoperiod)
            const rampDownStartMin = (endMin - rampDownDuration + 1440) % 1440
            const rampDownEndMin = endMin
            
            // Main photoperiod (full period including ramps)
            const photoperiodStart = startMin
            const photoperiodEnd = endMin
            
            // Render ramp up period (yellow) - at the beginning of photoperiod
            const renderRampUp = () => {
              if (rampUpDuration === 0) return null
              
              if (rampUpEndMin >= rampUpStartMin) {
                // Ramp up doesn't cross midnight
                return (
                  <div
                    key="ramp-up"
                    className="absolute top-0 bottom-0 bg-orange-200 opacity-40 pointer-events-none z-5"
                    style={{
                      left: `${getPosition(rampUpStartMin)}%`,
                      width: `${getPosition(rampUpDuration)}%`
                    }}
                  />
                )
              } else {
                // Ramp up crosses midnight
                const firstPart = 1440 - rampUpStartMin
                const secondPart = rampUpEndMin
                return (
                  <>
                    <div
                      key="ramp-up-1"
                      className="absolute top-0 bottom-0 bg-orange-200 opacity-40 pointer-events-none z-5"
                      style={{
                        left: `${getPosition(rampUpStartMin)}%`,
                        width: `${getPosition(firstPart)}%`
                      }}
                    />
                    <div
                      key="ramp-up-2"
                      className="absolute top-0 bottom-0 bg-orange-200 opacity-40 pointer-events-none z-5"
                      style={{
                        left: `${getPosition(0)}%`,
                        width: `${getPosition(secondPart)}%`
                      }}
                    />
                  </>
                )
              }
            }
            
            // Render ramp down period (yellow) - at the end of photoperiod
            const renderRampDown = () => {
              if (rampDownDuration === 0) return null
              
              if (rampDownEndMin >= rampDownStartMin) {
                // Ramp down doesn't cross midnight
                return (
                  <div
                    key="ramp-down"
                    className="absolute top-0 bottom-0 bg-orange-200 opacity-40 pointer-events-none z-5"
                    style={{
                      left: `${getPosition(rampDownStartMin)}%`,
                      width: `${getPosition(rampDownDuration)}%`
                    }}
                  />
                )
              } else {
                // Ramp down crosses midnight
                const firstPart = 1440 - rampDownStartMin
                const secondPart = rampDownEndMin
                return (
                  <>
                    <div
                      key="ramp-down-1"
                      className="absolute top-0 bottom-0 bg-orange-200 opacity-40 pointer-events-none z-5"
                      style={{
                        left: `${getPosition(rampDownStartMin)}%`,
                        width: `${getPosition(firstPart)}%`
                      }}
                    />
                    <div
                      key="ramp-down-2"
                      className="absolute top-0 bottom-0 bg-orange-200 opacity-40 pointer-events-none z-5"
                      style={{
                        left: `${getPosition(0)}%`,
                        width: `${getPosition(secondPart)}%`
                      }}
                    />
                  </>
                )
              }
            }
            
            // Render main photoperiod (light orange) - full period
            const renderPhotoperiod = () => {
              if (photoperiodEnd >= photoperiodStart) {
                // Normal case: photoperiod doesn't cross midnight
                const duration = photoperiodEnd - photoperiodStart
                return (
                  <div
                    key="photoperiod"
                    className="absolute top-0 bottom-0 bg-yellow-200 opacity-40 pointer-events-none z-5"
                    style={{
                      left: `${getPosition(photoperiodStart)}%`,
                      width: `${getPosition(duration)}%`
                    }}
                  />
                )
              } else {
                // Photoperiod crosses midnight - split into two divs
                const firstPartDuration = 1440 - photoperiodStart  // From start to midnight
                const secondPartDuration = photoperiodEnd  // From midnight to end
                return (
                  <>
                    {/* First part: from start to midnight */}
                    <div
                      key="photoperiod-1"
                      className="absolute top-0 bottom-0 bg-yellow-200 opacity-40 pointer-events-none z-5"
                      style={{
                        left: `${getPosition(photoperiodStart)}%`,
                        width: `${getPosition(firstPartDuration)}%`
                      }}
                    />
                    {/* Second part: from midnight to end */}
                    <div
                      key="photoperiod-2"
                      className="absolute top-0 bottom-0 bg-yellow-200 opacity-40 pointer-events-none z-5"
                      style={{
                        left: `${getPosition(0)}%`,
                        width: `${getPosition(secondPartDuration)}%`
                      }}
                    />
                  </>
                )
              }
            }
            
            return (
              <>
                {renderPhotoperiod()}
                {renderRampUp()}
                {renderRampDown()}
              </>
            )
          })()}

          {/* Setpoint lines - interpolated across periods */}
          {_setpoints && (() => {
            // Helper to get value and convert to y position
            const getYPosition = (value: number, isVPD: boolean): number => {
              return isVPD 
                ? ((2 - value) / (2 - 0.5)) * 100
                : ((35 - value) / (35 - 15)) * 100
            }
            
            // Render a line segment (horizontal or vertical transition)
            const renderLineSegment = (
              x1: number, y1: number, x2: number, y2: number, 
              color: string, key: string
            ) => {
              const width = Math.abs(x2 - x1)
              const height = Math.abs(y2 - y1)
              const left = Math.min(x1, x2)
              const top = Math.min(y1, y2)
              
              // If it's a vertical line (transition between periods)
              if (Math.abs(x2 - x1) < 0.1) {
                return (
                  <div
                    key={key}
                    className="absolute pointer-events-none z-10"
                    style={{
                      left: `${x1}%`,
                      top: `${top}%`,
                      width: '2px',
                      height: `${height}%`,
                      backgroundColor: color,
                      transform: 'translateX(-50%)'
                    }}
                  />
                )
              }
              
              // Horizontal line
              return (
                <div
                  key={key}
                  className="absolute pointer-events-none z-10"
                  style={{
                    left: `${left}%`,
                    top: `${y1}%`,
                    width: `${width}%`,
                    height: '2px',
                    backgroundColor: color,
                    transform: 'translateY(-50%)'
                  }}
                />
              )
            }
            
            // Create interpolated line for a setpoint type across all periods
            const renderInterpolatedLine = (setpointType: 'heating_setpoint' | 'cooling_setpoint' | 'vpd' | 'co2', color: string, key: string) => {
              const isVPD = setpointType === 'vpd'
              
              // Get values for all periods in order: PRE_DAY -> DAY -> PRE_NIGHT -> NIGHT
              const periodValues: Array<{ period: { start: number; end: number }, value: number }> = []
              
              if (_setpoints.PRE_DAY) {
                const val = typeof _setpoints.PRE_DAY[setpointType] === 'number' ? _setpoints.PRE_DAY[setpointType] : null
                if (val !== null) periodValues.push({ period: periods.preDay, value: val })
              }
              if (_setpoints.DAY) {
                const val = typeof _setpoints.DAY[setpointType] === 'number' ? _setpoints.DAY[setpointType] : null
                if (val !== null) periodValues.push({ period: periods.day, value: val })
              }
              if (_setpoints.PRE_NIGHT) {
                const val = typeof _setpoints.PRE_NIGHT[setpointType] === 'number' ? _setpoints.PRE_NIGHT[setpointType] : null
                if (val !== null) periodValues.push({ period: periods.preNight, value: val })
              }
              if (_setpoints.NIGHT) {
                const val = typeof _setpoints.NIGHT[setpointType] === 'number' ? _setpoints.NIGHT[setpointType] : null
                if (val !== null) periodValues.push({ period: periods.night, value: val })
              }
              
              if (periodValues.length === 0) return null
              
              const segments: JSX.Element[] = []
              
              for (let i = 0; i < periodValues.length; i++) {
                const current = periodValues[i]
                const isLast = i === periodValues.length - 1
                let next = isLast ? null : periodValues[i + 1]
                
                // For NIGHT period (last in array), check if it should connect to PRE_DAY (first in array)
                // This handles the wrap-around: NIGHT -> PRE_DAY
                // NIGHT always ends at PRE_DAY start (nightEndMin = preDayStartMin)
                if (isLast && periodValues.length > 0) {
                  const firstPeriod = periodValues[0]
                  // Check if current period ends where first period starts (NIGHT ends at PRE_DAY start)
                  const currentEnd = current.period.end
                  const firstStart = firstPeriod.period.start
                  
                  // NIGHT ends at preDayStartMin, PRE_DAY starts at preDayStartMin
                  if (currentEnd === firstStart) {
                    next = firstPeriod
                  }
                }
                
                const currentStart = current.period.start
                const currentEnd = current.period.end
                const currentValue = current.value
                const y = getYPosition(currentValue, isVPD)
                
                // Draw period line(s) - handle midnight crossing
                if (currentEnd >= currentStart) {
                  // Period doesn't cross midnight
                  const x1 = getPosition(currentStart)
                  const x2 = getPosition(currentEnd)
                  segments.push(renderLineSegment(x1, y, x2, y, color, `${key}-period-${i}`))
                  
                  // Transition to next period
                  if (next) {
                    const nextStart = next.period.start
                    const nextValue = next.value
                    const nextY = getYPosition(nextValue, isVPD)
                    const nextX = getPosition(nextStart)
                    
                    // Always connect if next is set (it means they're chronologically consecutive)
                    segments.push(renderLineSegment(x2, y, nextX, nextY, color, `${key}-transition-${i}`))
                  }
                } else {
                  // Period crosses midnight - draw two segments
                  // First part: from start to end of timeline
                  const x1 = getPosition(currentStart)
                  const x2 = 100
                  segments.push(renderLineSegment(x1, y, x2, y, color, `${key}-period-${i}-1`))
                  
                  // Second part: from start of timeline to end
                  const x3 = 0
                  const x4 = getPosition(currentEnd)
                  segments.push(renderLineSegment(x3, y, x4, y, color, `${key}-period-${i}-2`))
                  
                  // Transition to next period (from the end of this period)
                  if (next) {
                    const nextStart = next.period.start
                    const nextValue = next.value
                    const nextY = getYPosition(nextValue, isVPD)
                    const nextX = getPosition(nextStart)
                    
                    // Connect from end of current period to start of next
                    segments.push(renderLineSegment(x4, y, nextX, nextY, color, `${key}-transition-${i}`))
                  }
                }
              }
              
              return <>{segments}</>
            }
            
            const lines: JSX.Element[] = []
            
            // Render interpolated lines for each setpoint type
            if (_setpoints.DAY || _setpoints.NIGHT || _setpoints.PRE_DAY || _setpoints.PRE_NIGHT) {
              // Check if any period has heating setpoint
              const hasHeating = [_setpoints.PRE_DAY, _setpoints.DAY, _setpoints.PRE_NIGHT, _setpoints.NIGHT].some(
                sp => sp && typeof sp.heating_setpoint === 'number'
              )
              if (hasHeating) {
                lines.push(renderInterpolatedLine('heating_setpoint', '#ef4444', 'heating') as JSX.Element)
              }
              
              const hasCooling = [_setpoints.PRE_DAY, _setpoints.DAY, _setpoints.PRE_NIGHT, _setpoints.NIGHT].some(
                sp => sp && typeof sp.cooling_setpoint === 'number'
              )
              if (hasCooling) {
                lines.push(renderInterpolatedLine('cooling_setpoint', '#3b82f6', 'cooling') as JSX.Element)
              }
              
              const hasVPD = [_setpoints.PRE_DAY, _setpoints.DAY, _setpoints.PRE_NIGHT, _setpoints.NIGHT].some(
                sp => sp && typeof sp.vpd === 'number'
              )
              if (hasVPD) {
                lines.push(renderInterpolatedLine('vpd', '#40e0d0', 'vpd') as JSX.Element)
              }
              
              const hasCO2 = [_setpoints.PRE_DAY, _setpoints.DAY, _setpoints.PRE_NIGHT, _setpoints.NIGHT].some(
                sp => sp && typeof sp.co2 === 'number'
              )
              if (hasCO2) {
                lines.push(renderInterpolatedLine('co2', '#6b7280', 'co2') as JSX.Element)
              }
            }
            
            return <>{lines}</>
          })()}

          {/* Current time indicator */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-red-500 z-10"
            style={{ left: `${getPosition(currentTime)}%` }}
          />

          {/* Hour markers - fixed from midnight (00:00) to midnight (24:00) */}
          {Array.from({ length: 25 }).map((_, i) => (
            <div
              key={i}
              className="absolute top-0 bottom-0 w-px bg-gray-300"
              style={{ left: `${(i / 24) * 100}%` }}
            />
          ))}
          
          {/* Y-axis grid lines - Temperature */}
          {[15, 20, 25, 30, 35].map((value) => (
            <div
              key={`grid-${value}`}
              className="absolute left-0 right-0 h-px bg-gray-200 opacity-50"
              style={{
                top: `${((35 - value) / (35 - 15)) * 100}%`
              }}
            />
          ))}
          </div>
          
          {/* Hour labels - fixed from midnight (00:00) to midnight (24:00) */}
          <div className="relative h-8 border-t border-gray-300 bg-gray-50">
            {Array.from({ length: 25 }).map((_, i) => {
              // Show 00:00 to 24:00 (fixed midnight to midnight)
              const hour = i === 24 ? 24 : i
              return (
                <div
                  key={i}
                  className="absolute top-1 text-xs text-gray-700 font-medium"
                  style={{ left: `${(i / 24) * 100}%`, transform: 'translateX(-50%)' }}
                >
                  {String(hour).padStart(2, '0')}:00
                </div>
              )
            })}
          </div>
        </div>
        
        {/* Y-axis labels - VPD (right) */}
        <div className="flex-shrink-0 w-12 border-l border-gray-300 bg-gray-50 relative">
          <div className="relative h-64">
            {[2, 1.5, 1, 0.5].map((value) => (
              <div
                key={`vpd-${value}`}
                className="absolute text-xs text-gray-700 font-medium text-left pl-2"
                style={{ 
                  top: `${((2 - value) / (2 - 0.5)) * 100}%`,
                  transform: 'translateY(-50%)',
                  width: '100%'
                }}
              >
                {value}
              </div>
            ))}
          </div>
          {/* Spacer for hour labels */}
          <div className="h-8"></div>
        </div>
      </div>

      {/* Duration controls */}
      <div className="mt-4 grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Pre-Day Duration (minutes)
          </label>
          <input
            type="number"
            min="0"
            max="240"
            value={preDayDuration}
            onChange={(e) => onPreDayDurationChange(parseInt(e.target.value) || 0)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Pre-Night Duration (minutes)
          </label>
          <input
            type="number"
            min="0"
            max="240"
            value={preNightDuration}
            onChange={(e) => onPreNightDurationChange(parseInt(e.target.value) || 0)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md"
          />
        </div>
      </div>
    </div>
  )
}

