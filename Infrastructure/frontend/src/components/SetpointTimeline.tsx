import { useMemo, useEffect, useState } from 'react'

interface SetpointTimelineProps {
 dayStartTime: string
 dayEndTime: string
 preDayDuration: number
 preNightDuration: number
 currentPreDayDuration?: number
 currentPreNightDuration?: number
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
 compact?: boolean
 className?: string
}

export default function SetpointTimeline({
 dayStartTime,
 dayEndTime,
 preDayDuration,
 preNightDuration,
 currentPreDayDuration: _currentPreDayDuration,
 currentPreNightDuration: _currentPreNightDuration,
 onDayStartChange: _onDayStartChange,
 onDayEndChange: _onDayEndChange,
 onPreDayDurationChange: _onPreDayDurationChange,
 onPreNightDurationChange: _onPreNightDurationChange,
 lightPhotoperiod,
 setpoints: _setpoints,
 compact: _compact = false,
 className = ""
}: SetpointTimelineProps) {
 // Track dark mode for hatch pattern styling
 const [isDarkMode, setIsDarkMode] = useState(false)
 
 useEffect(() => {
 const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
 setIsDarkMode(mediaQuery.matches)
 
 const handleChange = (e: MediaQueryListEvent) => setIsDarkMode(e.matches)
 mediaQuery.addEventListener('change', handleChange)
 return () => mediaQuery.removeEventListener('change', handleChange)
 }, [])
 
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
 // PRE_NIGHT happens during day, ending at day end
 const preNightStartMin = (dayEndMin - preNightDuration + 1440) % 1440
 const preNightEndMin = dayEndMin
 const nightStartMin = dayEndMin
 const nightEndMin = preDayStartMin

 return {
 preDay: { start: preDayStartMin, end: preDayEndMin },
 day: { start: dayStartMin, end: dayEndMin },
 preNight: { start: preNightStartMin, end: preNightEndMin },
 night: { start: nightStartMin, end: nightEndMin }
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
 <div className={`w-full ${className}`}>
 <div className="relative bg-surface-base border border-border-subtle rounded-lg overflow-hidden h-full">
 {/* Y-axis labels - Temperature (left) - OVERLAY */}
 <div className="absolute left-0 top-2 bottom-4 w-6 z-20 pointer-events-none">
 {[35, 25, 15].map((value) => (
 <div
 key={value}
 className="absolute text-[9px] text-text-muted font-medium text-right pr-0.5 bg-surface-base/80 px-0.5 rounded-r"
 style={{ 
 top: `${((35 - value) / (35 - 15)) * 100}%`,
 transform: 'translateY(-50%)',
 left: 0
 }}
 >
 {value}°
 </div>
 ))}
 </div>
 
 {/* Timeline content area - FULL WIDTH */}
 <div className="relative h-full pt-2">
 <div className="relative h-full">
 {/* Mode period backgrounds - Climate periods (independent of light overlay) */}
 <div
 className="absolute top-0 bottom-0 bg-timeline-night/30"
 style={{
 left: `${getPosition(periods.preDay.start)}%`,
 width: `${getPosition((periods.preDay.end - periods.preDay.start + 1440) % 1440)}%`,
 zIndex: 1
 }}
 />
 <div
 className="absolute top-0 bottom-0 bg-timeline-day/30"
 style={{
 left: `${getPosition(periods.day.start)}%`,
 width: `${getPosition(periods.day.end - periods.day.start)}%`,
 zIndex: 1
 }}
 />
 {preNightDuration > 0 && (
 <div
 className="absolute top-0 bottom-0 bg-timeline-pre/30"
 style={{
 left: `${getPosition(periods.preNight.start)}%`,
 width: periods.preNight.end >= periods.preNight.start
 ? `${getPosition(periods.preNight.end - periods.preNight.start)}%`
 : `${getPosition((periods.preNight.end - periods.preNight.start + 1440) % 1440)}%`,
 zIndex: 1
 }}
 />
 )}
 <div
 className="absolute top-0 bottom-0"
 style={{
 left: `${getPosition(periods.night.start)}%`,
 width: `${getPosition((periods.night.end - periods.night.start + 1440) % 1440)}%`,
 backgroundColor: 'rgba(75, 85, 200, 0.3)', // Purplish blue
 zIndex: 1
 }}
 />

 {/* Light photoperiod overlay - Visual only, does not affect climate period calculations */}
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
 
 // Render ramp up period with hatch pattern - at the beginning of photoperiod
 const renderRampUp = () => {
 if (rampUpDuration === 0) return null
 
 // Hatch pattern using repeating linear gradient
 const hatchPattern = isDarkMode
 ? 'repeating-linear-gradient(45deg, transparent, transparent 4px, rgba(154, 52, 18, 0.5) 4px, rgba(154, 52, 18, 0.5) 8px)'
 : 'repeating-linear-gradient(45deg, transparent, transparent 4px, rgba(251, 146, 60, 0.4) 4px, rgba(251, 146, 60, 0.4) 8px)'
 
 if (rampUpEndMin >= rampUpStartMin) {
 // Ramp up doesn't cross midnight
 return (
 <div
 key="ramp-up"
 className="absolute top-0 bottom-0 pointer-events-none"
 style={{
 left: `${getPosition(rampUpStartMin)}%`,
 width: `${getPosition(rampUpDuration)}%`,
 zIndex: 5,
 backgroundImage: hatchPattern
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
 className="absolute top-0 bottom-0 pointer-events-none"
 style={{
 left: `${getPosition(rampUpStartMin)}%`,
 width: `${getPosition(firstPart)}%`,
 zIndex: 5,
 backgroundImage: hatchPattern
 }}
 />
 <div
 key="ramp-up-2"
 className="absolute top-0 bottom-0 pointer-events-none"
 style={{
 left: `${getPosition(0)}%`,
 width: `${getPosition(secondPart)}%`,
 zIndex: 5,
 backgroundImage: hatchPattern
 }}
 />
 </>
 )
 }
 }
 
 // Render ramp down period with hatch pattern - at the end of photoperiod
 const renderRampDown = () => {
 if (rampDownDuration === 0) return null
 
 // Hatch pattern using repeating linear gradient
 const hatchPattern = isDarkMode
 ? 'repeating-linear-gradient(45deg, transparent, transparent 4px, rgba(154, 52, 18, 0.5) 4px, rgba(154, 52, 18, 0.5) 8px)'
 : 'repeating-linear-gradient(45deg, transparent, transparent 4px, rgba(251, 146, 60, 0.4) 4px, rgba(251, 146, 60, 0.4) 8px)'
 
 if (rampDownEndMin >= rampDownStartMin) {
 // Ramp down doesn't cross midnight
 return (
 <div
 key="ramp-down"
 className="absolute top-0 bottom-0 pointer-events-none"
 style={{
 left: `${getPosition(rampDownStartMin)}%`,
 width: `${getPosition(rampDownDuration)}%`,
 zIndex: 5,
 backgroundImage: hatchPattern
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
 className="absolute top-0 bottom-0 pointer-events-none"
 style={{
 left: `${getPosition(rampDownStartMin)}%`,
 width: `${getPosition(firstPart)}%`,
 zIndex: 5,
 backgroundImage: hatchPattern
 }}
 />
 <div
 key="ramp-down-2"
 className="absolute top-0 bottom-0 pointer-events-none"
 style={{
 left: `${getPosition(0)}%`,
 width: `${getPosition(secondPart)}%`,
 zIndex: 5,
 backgroundImage: hatchPattern
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
 className="absolute top-0 bottom-0 bg-timeline-ramp/50 opacity-40 pointer-events-none"
 style={{
 left: `${getPosition(photoperiodStart)}%`,
 width: `${getPosition(duration)}%`,
 zIndex: 0
 }}
 />
 )
 } else {
 // Photoperiod crosses midnight - split into two divs
 const firstPartDuration = 1440 - photoperiodStart // From start to midnight
 const secondPartDuration = photoperiodEnd // From midnight to end
 return (
 <>
 {/* First part: from start to midnight */}
 <div
 key="photoperiod-1"
 className="absolute top-0 bottom-0 bg-timeline-ramp/50 opacity-40 pointer-events-none"
 style={{
 left: `${getPosition(photoperiodStart)}%`,
 width: `${getPosition(firstPartDuration)}%`,
 zIndex: 0
 }}
 />
 {/* Second part: from midnight to end */}
 <div
 key="photoperiod-2"
 className="absolute top-0 bottom-0 bg-timeline-ramp/50 opacity-40 pointer-events-none"
 style={{
 left: `${getPosition(0)}%`,
 width: `${getPosition(secondPartDuration)}%`,
 zIndex: 0
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
 
 // If it's a diagonal line (ramp), use SVG
 if (Math.abs(y2 - y1) > 0.1) {
 return (
 <svg
 key={key}
 className="absolute pointer-events-none z-10"
 style={{
 left: 0,
 top: 0,
 width: '100%',
 height: '100%',
 overflow: 'visible'
 }}
 >
 <line
 x1={`${x1}%`}
 y1={`${y1}%`}
 x2={`${x2}%`}
 y2={`${y2}%`}
 stroke={color}
 strokeWidth="2"
 />
 </svg>
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
 
 // Helpers to handle intervals that may wrap midnight
 const normalizeInterval = (start: number, end: number): Array<[number, number]> => {
 if (end >= start) return [[start, end]]
 return [[start, 1440], [0, end]]
 }

 const subtractInterval = (
 baseStart: number,
 baseEnd: number,
 cutStart: number,
 cutEnd: number
 ): Array<{ start: number; end: number }> => {
 let result = normalizeInterval(baseStart, baseEnd)
 const cuts = normalizeInterval(cutStart, cutEnd)

 for (const [cs, ce] of cuts) {
 const next: Array<[number, number]> = []
 for (const [bs, be] of result) {
 // no overlap
 if (be <= cs || bs >= ce) {
 next.push([bs, be])
 continue
 }
 // overlap: keep left part
 if (bs < cs) next.push([bs, cs])
 // keep right part
 if (be > ce) next.push([ce, be])
 }
 result = next
 }

 return result.map(([s, e]) => ({ start: s % 1440, end: e % 1440 }))
 }

 // Create interpolated line for a setpoint type across all periods
 const renderInterpolatedLine = (setpointType: 'heating_setpoint' | 'cooling_setpoint' | 'vpd' | 'co2', color: string, key: string) => {
 const isVPD = setpointType === 'vpd'
 
 // Get values for all periods in chronological order
 // PRE_DAY takes precedence over DAY during PRE_DAY period
 // PRE_NIGHT takes precedence over DAY during PRE_NIGHT period
 const periodValues: Array<{ 
 period: { start: number; end: number }, 
 value: number,
 rampInDuration: number
 }> = []
 
 // Add PRE_DAY if it has setpoints (takes precedence during PRE_DAY period)
 if (_setpoints.PRE_DAY && preDayDuration > 0) {
 const val = typeof _setpoints.PRE_DAY[setpointType] === 'number' ? _setpoints.PRE_DAY[setpointType] : null
 if (val !== null) {
 const rampIn = _setpoints.PRE_DAY.ramp_in_duration || 0
 periodValues.push({ period: periods.preDay, value: val, rampInDuration: rampIn })
 }
 }
 
 // Add DAY setpoints (only for the actual DAY period, NOT during PRE_DAY or PRE_NIGHT)
 if (_setpoints.DAY) {
 const val = typeof _setpoints.DAY[setpointType] === 'number' ? _setpoints.DAY[setpointType] : null
 if (val !== null) {
 const rampIn = _setpoints.DAY.ramp_in_duration || 0

 // Start with full DAY, then subtract PRE_NIGHT if present
 let daySegments: Array<{ start: number; end: number }> = [
 { start: periods.day.start, end: periods.day.end }
 ]

 if (preNightDuration > 0) {
 daySegments = subtractInterval(
 periods.day.start,
 periods.day.end,
 periods.preNight.start,
 periods.preNight.end
 )
 }

 daySegments.forEach((segment, idx) => {
 periodValues.push({
 period: { start: segment.start, end: segment.end },
 value: val,
 rampInDuration: idx === 0 ? rampIn : 0 // ramp applies at day start only
 })
 })
 }
 }
 
 // Add PRE_NIGHT if it has setpoints (takes precedence during PRE_NIGHT period, which is during DAY)
 if (_setpoints.PRE_NIGHT && preNightDuration > 0) {
 const val = typeof _setpoints.PRE_NIGHT[setpointType] === 'number' ? _setpoints.PRE_NIGHT[setpointType] : null
 if (val !== null) {
 const rampIn = _setpoints.PRE_NIGHT.ramp_in_duration || 0
 periodValues.push({ period: periods.preNight, value: val, rampInDuration: rampIn })
 }
 }
 
 // Add NIGHT setpoints
 if (_setpoints.NIGHT) {
 const val = typeof _setpoints.NIGHT[setpointType] === 'number' ? _setpoints.NIGHT[setpointType] : null
 if (val !== null) {
 const rampIn = _setpoints.NIGHT.ramp_in_duration || 0
 periodValues.push({ period: periods.night, value: val, rampInDuration: rampIn })
 }
 }
 
 if (periodValues.length === 0) return null
 
 // Sort periods chronologically by start time
 // PRE_DAY comes before DAY, so it should be rendered first
 periodValues.sort((a, b) => {
 const aStart = a.period.start
 const bStart = b.period.start
 const aEnd = a.period.end
 const bEnd = b.period.end
 
 // Handle midnight wrapping
 const aWraps = aEnd < aStart
 const bWraps = bEnd < bStart
 
 if (aWraps && !bWraps) {
 // a wraps, b doesn't - a comes after b
 return 1
 }
 if (!aWraps && bWraps) {
 // b wraps, a doesn't - b comes after a
 return -1
 }
 
 // Both wrap or neither wraps - compare start times
 // For wrapping periods, we want the one that starts later in the day to come first
 // (since it represents the earlier part of the 24h cycle)
 if (aWraps && bWraps) {
 // Both wrap - the one with later start time comes first (it's earlier in the cycle)
 return bStart - aStart
 }
 
 // Neither wraps - simple comparison
 return aStart - bStart
 })
 
 // Use computed periods directly (already split to avoid overlaps)
 const finalPeriodValues = periodValues
 
 const segments: JSX.Element[] = []
 
 for (let i = 0; i < finalPeriodValues.length; i++) {
 const current = finalPeriodValues[i]
 const isLast = i === finalPeriodValues.length - 1
 let next = isLast ? null : finalPeriodValues[i + 1]
 let previous = i > 0 ? finalPeriodValues[i - 1] : null
 
 // For NIGHT period (last in array), check if it should connect to PRE_DAY (first in array)
 // This handles the wrap-around: NIGHT -> PRE_DAY
 if (isLast && finalPeriodValues.length > 0) {
 const firstPeriod = finalPeriodValues[0]
 const currentEnd = current.period.end
 const firstStart = firstPeriod.period.start
 
 if (currentEnd === firstStart) {
 next = firstPeriod
 }
 // Previous for NIGHT is the period before it in the array
 }
 
 // For PRE_DAY (first in array), previous should be NIGHT (last in array) if they connect
 if (i === 0 && finalPeriodValues.length > 0) {
 const lastPeriod = finalPeriodValues[finalPeriodValues.length - 1]
 const currentStart = current.period.start
 const lastEnd = lastPeriod.period.end
 
 if (lastEnd === currentStart) {
 previous = lastPeriod
 }
 }
 
 const currentStart = current.period.start
 const currentEnd = current.period.end
 const currentValue = current.value
 const rampInDuration = current.rampInDuration
 
 // Get previous value for ramp calculation
 // If previous period exists and ends where current starts, use previous value
 // Otherwise, if there's a next period that ends where current starts, use that
 // Otherwise, use current value (no ramp)
 let previousValue = currentValue
 if (previous && previous.period.end === currentStart) {
 previousValue = previous.value
 } else if (i > 0) {
 // Check if the period before in array ends where current starts
 const prevInArray = finalPeriodValues[i - 1]
 if (prevInArray && prevInArray.period.end === currentStart) {
 previousValue = prevInArray.value
 }
 }
 
 // Calculate ramp end position
 const rampEndMin = (currentStart + rampInDuration) % 1440
 const rampEnd = rampInDuration > 0 ? rampEndMin : currentStart
 
 // Draw period line(s) - handle midnight crossing
 if (currentEnd >= currentStart) {
 // Period doesn't cross midnight
 const x1 = getPosition(currentStart)
 const x2 = getPosition(currentEnd)
 const y = getYPosition(currentValue, isVPD)
 
 // Draw ramp if ramp_in_duration > 0
 if (rampInDuration > 0 && previousValue !== currentValue) {
 const rampStartX = getPosition(currentStart)
 const rampEndX = getPosition(rampEnd)
 const rampStartY = getYPosition(previousValue, isVPD)
 const rampEndY = y
 
 // Draw diagonal ramp line
 segments.push(renderLineSegment(rampStartX, rampStartY, rampEndX, rampEndY, color, `${key}-ramp-${i}`))
 
 // Draw horizontal line for rest of period (after ramp)
 if (rampEnd < currentEnd) {
 const steadyStartX = getPosition(rampEnd)
 const steadyEndX = x2
 segments.push(renderLineSegment(steadyStartX, y, steadyEndX, y, color, `${key}-period-${i}`))
 }
 } else {
 // No ramp, draw horizontal line for entire period
 segments.push(renderLineSegment(x1, y, x2, y, color, `${key}-period-${i}`))
 }
 
 // Transition to next period
 // If next period starts immediately after current (or at same time), check if it has a ramp
 // If next has a ramp, the ramp will handle the transition
 // Otherwise, draw a transition line
 if (next) {
 const nextStart = next.period.start
 const nextValue = next.value
 const nextRampIn = next.rampInDuration || 0
 
 // Only draw transition if next period doesn't start immediately with a ramp
 // or if there's a gap between periods
 if (nextStart > currentEnd) {
 // There's a gap, draw transition line
 const nextY = getYPosition(nextValue, isVPD)
 const nextX = getPosition(nextStart)
 segments.push(renderLineSegment(x2, y, nextX, nextY, color, `${key}-transition-${i}`))
 } else if (nextStart === currentEnd && nextRampIn === 0) {
 // Next period starts immediately but has no ramp, draw transition
 const nextY = getYPosition(nextValue, isVPD)
 const nextX = getPosition(nextStart)
 segments.push(renderLineSegment(x2, y, nextX, nextY, color, `${key}-transition-${i}`))
 }
 // If nextStart === currentEnd && nextRampIn > 0, the ramp will be drawn in next iteration
 }
 } else {
 // Period crosses midnight - draw two segments
 const x1 = getPosition(currentStart)
 const x2 = 100
 const x3 = 0
 const x4 = getPosition(currentEnd)
 const y = getYPosition(currentValue, isVPD)
 
 // Handle ramp for midnight-crossing period
 if (rampInDuration > 0 && previousValue !== currentValue) {
 const rampStartX = getPosition(currentStart)
 const rampEndX = getPosition(rampEnd)
 const rampStartY = getYPosition(previousValue, isVPD)
 const rampEndY = y
 
 if (rampEnd >= currentStart) {
 // Ramp doesn't cross midnight
 segments.push(renderLineSegment(rampStartX, rampStartY, rampEndX, rampEndY, color, `${key}-ramp-${i}`))
 // Rest of period after ramp
 if (rampEnd < 1440) {
 segments.push(renderLineSegment(rampEndX, y, x2, y, color, `${key}-period-${i}-1`))
 segments.push(renderLineSegment(x3, y, x4, y, color, `${key}-period-${i}-2`))
 } else {
 // Ramp crosses midnight
 const rampMidnightX = 100
 const rampMidnightY = getYPosition(
 previousValue + (currentValue - previousValue) * ((1440 - currentStart) / rampInDuration),
 isVPD
 )
 segments.push(renderLineSegment(rampStartX, rampStartY, rampMidnightX, rampMidnightY, color, `${key}-ramp-${i}-1`))
 const rampAfterMidnightX = getPosition(rampEnd)
 segments.push(renderLineSegment(x3, rampMidnightY, rampAfterMidnightX, y, color, `${key}-ramp-${i}-2`))
 if (rampEnd < currentEnd) {
 segments.push(renderLineSegment(rampAfterMidnightX, y, x4, y, color, `${key}-period-${i}-2`))
 }
 }
 }
 } else {
 // No ramp, draw horizontal lines for entire period
 segments.push(renderLineSegment(x1, y, x2, y, color, `${key}-period-${i}-1`))
 segments.push(renderLineSegment(x3, y, x4, y, color, `${key}-period-${i}-2`))
 }
 
 // Transition to next period
 if (next) {
 const nextStart = next.period.start
 const nextValue = next.value
 const nextY = getYPosition(nextValue, isVPD)
 const nextX = getPosition(nextStart)
 
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
 className="absolute top-0 bottom-0 w-0.5 bg-status-danger-vivid z-10"
 style={{ left: `${getPosition(currentTime)}%` }}
 />

 {/* Hour markers - fixed from midnight (00:00) to midnight (24:00) */}
 {Array.from({ length: 25 }).map((_, i) => (
 <div
 key={i}
 className="absolute top-0 bottom-0 w-px bg-surface-secondary"
 style={{ left: `${(i / 24) * 100}%` }}
 />
 ))}
 
 {/* Y-axis grid lines - Temperature */}
 {[15, 20, 25, 30, 35].map((value) => (
 <div
 key={`grid-${value}`}
 className="absolute left-0 right-0 h-px bg-surface-secondary opacity-50"
 style={{
 top: `${((35 - value) / (35 - 15)) * 100}%`
 }}
 />
 ))}
 </div>
 
 {/* Hour labels - fixed from midnight (00:00) to midnight (24:00) */}
 <div className="absolute bottom-0 left-0 right-0 h-4 pointer-events-none">
 {Array.from({ length: 13 }).map((_, i) => {
 // Show every 2 hours
 const hour = i * 2
 return (
 <div
 key={i}
 className="absolute bottom-0 text-[10px] text-text-subtle font-medium"
 style={{ left: `${(hour / 24) * 100}%`, transform: 'translateX(-50%)' }}
 >
 {String(hour).padStart(2, '0')}
 </div>
 )
 })}
 </div>
 </div>
 
 {/* Y-axis labels - VPD (right) - OVERLAY */}
 <div className="absolute right-0 top-2 bottom-4 w-6 z-20 pointer-events-none">
 {[2, 1, 0.5].map((value) => (
 <div
 key={`vpd-${value}`}
 className="absolute text-[9px] text-accent-data font-medium text-left pl-0.5 bg-surface-base/80 px-0.5 rounded-l"
 style={{ 
 top: `${((2 - value) / (2 - 0.5)) * 100}%`,
 transform: 'translateY(-50%)',
 right: 0
 }}
 >
 {value}
 </div>
 ))}
 </div>
 </div>
 </div>
 )
}

