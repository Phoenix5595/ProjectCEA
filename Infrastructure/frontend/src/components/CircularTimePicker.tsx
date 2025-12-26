import { useState, useEffect, useRef } from 'react'

interface CircularTimePickerProps {
  dayStartTime: string
  dayEndTime: string
  onDayStartChange: (time: string) => void
  onDayEndChange: (time: string) => void
  label?: string
  period?: 'day' | 'night'
  rampUpDuration?: number | null
  rampDownDuration?: number | null
  onRampUpChange?: (duration: number | null) => void
  onRampDownChange?: (duration: number | null) => void
  showPresetButtons?: boolean
  lockedPhotoperiodHours?: number | null
}

export default function CircularTimePicker({
  dayStartTime,
  dayEndTime,
  onDayStartChange,
  onDayEndChange,
  label,
  period = 'day',
  rampUpDuration,
  rampDownDuration,
  onRampUpChange,
  onRampDownChange,
  showPresetButtons = true,
  lockedPhotoperiodHours = null
}: CircularTimePickerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [isDragging, setIsDragging] = useState<'start' | 'end' | 'period' | null>(null)
  const [dragOffset, setDragOffset] = useState<number>(0) // Offset in minutes when dragging period

  // Convert time string (HH:MM) to minutes since midnight
  function timeToMinutes(time: string): number {
    const [hours, minutes] = time.split(':').map(Number)
    return hours * 60 + minutes
  }

  // Convert minutes since midnight to time string (HH:MM)
  function minutesToTime(minutes: number): string {
    const hours = Math.floor(minutes / 60) % 24
    const mins = minutes % 60
    return `${hours.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}`
  }

  // Calculate photoperiod (day duration) in hours
  function calculatePhotoperiod(): number {
    const startMinutes = timeToMinutes(dayStartTime)
    const endMinutes = timeToMinutes(dayEndTime)
    let duration = endMinutes - startMinutes
    if (duration < 0) {
      duration += 1440 // Add 24 hours if overnight
    }
    return duration / 60 // Convert to hours
  }

  // Convert angle (radians) to minutes (12:00 noon = top, 00:00 midnight = bottom)
  // Input angle is from getAngleFromMouse: -π/2 at top (noon), π/2 at bottom (midnight)
  function angleToMinutes(angle: number): number {
    // Normalize angle to 0-2π range
    let normalizedAngle = angle % (2 * Math.PI)
    if (normalizedAngle < 0) normalizedAngle += 2 * Math.PI
    
    // Convert angle to hours (0-23.99)
    // -π/2 (top/noon) should map to hour 12
    // π/2 (bottom/midnight) should map to hour 0
    // We need to rotate: add π/2 to shift -π/2 to 0, then add 12 hours
    const rotatedAngle = (normalizedAngle + Math.PI / 2) % (2 * Math.PI)
    const hours = (rotatedAngle / (2 * Math.PI)) * 24
    // Shift by 12 hours: hour 0 in rotated = hour 12 (noon)
    const actualHours = (hours + 12) % 24
    // Convert to minutes
    const minutes = actualHours * 60
    return Math.round(minutes)
  }

  // Convert minutes to angle (12:00 noon = top of circle, 00:00 midnight = bottom)
  function minutesToAngle(minutes: number): number {
    // Convert minutes to hours (0-23.99)
    const hours = minutes / 60
    // Rotate so hour 12 (noon) is at top: (hour - 12) / 24 * 2π - π/2
    return ((hours - 12) / 24) * 2 * Math.PI - Math.PI / 2
  }

  // Get angle from mouse position relative to center
  // Returns angle in the same coordinate system as minutesToAngle (noon at top = -Math.PI/2)
  function getAngleFromMouse(event: MouseEvent | React.MouseEvent, rect: DOMRect): number {
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2
    const x = event.clientX - centerX
    const y = event.clientY - centerY
    // atan2(y, x) gives angle where:
    // - 0 is at right (3 o'clock) when x>0, y=0
    // - Math.PI/2 is at bottom (6 o'clock) when x=0, y>0
    // - -Math.PI/2 is at top (12 o'clock) when x=0, y<0
    // We want noon (12) at top = -Math.PI/2
    // minutesToAngle expects: noon = -Math.PI/2, so we need to match that
    // atan2(y, x) already gives us -Math.PI/2 at top, so we just return it
    return Math.atan2(y, x)
  }

  // Get distance from center
  function getDistanceFromCenter(event: MouseEvent | React.MouseEvent, rect: DOMRect): number {
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2
    const x = event.clientX - centerX
    const y = event.clientY - centerY
    return Math.sqrt(x * x + y * y)
  }

  function drawClock() {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const size = 300
    const centerX = size / 2
    const centerY = size / 2
    const radius = size / 2 - 40
    const markerRadius = radius - 10

    // Clear canvas
    ctx.clearRect(0, 0, size, size)

    // Draw outer circle
    ctx.strokeStyle = '#e5e7eb'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI)
    ctx.stroke()

    // Draw hour markers
    ctx.strokeStyle = '#9ca3af'
    ctx.lineWidth = 1
    ctx.font = '12px sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'

    for (let hour = 0; hour < 24; hour++) {
      // 12 (noon) should be at top, so we offset by Math.PI/2 to rotate
      // Hour 12 (noon) = 0 radians (top), Hour 0 (midnight) = Math.PI (bottom)
      const angle = ((hour - 12) / 24) * 2 * Math.PI - Math.PI / 2
      const x1 = centerX + Math.cos(angle) * radius
      const y1 = centerY + Math.sin(angle) * radius
      const x2 = centerX + Math.cos(angle) * markerRadius
      const y2 = centerY + Math.sin(angle) * markerRadius

      ctx.beginPath()
      ctx.moveTo(x1, y1)
      ctx.lineTo(x2, y2)
      ctx.stroke()

      // Draw hour labels
      const labelX = centerX + Math.cos(angle) * (markerRadius - 20)
      const labelY = centerY + Math.sin(angle) * (markerRadius - 20)
      ctx.fillStyle = '#6b7280'
      // Show all 24 hours (0-23)
      ctx.fillText(hour.toString(), labelX, labelY)
    }

    // Draw period arc - always show the DAY period overlay
    const startMinutes = timeToMinutes(dayStartTime)
    const endMinutes = timeToMinutes(dayEndTime)
    const isOvernight = endMinutes < startMinutes
    
    // The dayStartTime and dayEndTime props represent the period being edited
    // If period="day", they represent the DAY period
    // If period="night", they represent the NIGHT period
    // For the overlay, we always want to show DAY in orange/yellow and NIGHT in purple
    let dayStartMinutes, dayEndMinutes, nightStartMinutes, nightEndMinutes
    
    if (period === 'day') {
      // dayStartTime and dayEndTime represent the DAY period
      dayStartMinutes = startMinutes
      dayEndMinutes = endMinutes
      // Night is the complement
      if (isOvernight) {
        // Day is overnight (e.g., 17:00-11:00), so night is 11:00-17:00
        nightStartMinutes = endMinutes
        nightEndMinutes = startMinutes
      } else {
        // Day is normal (e.g., 06:00-20:00), so night is 20:00-06:00
        nightStartMinutes = endMinutes
        nightEndMinutes = startMinutes
      }
    } else {
      // dayStartTime and dayEndTime represent the NIGHT period
      nightStartMinutes = startMinutes
      nightEndMinutes = endMinutes
      // Day is the complement
      if (isOvernight) {
        // Night is overnight (e.g., 20:00-06:00), so day is 06:00-20:00
        dayStartMinutes = endMinutes
        dayEndMinutes = startMinutes
      } else {
        // Night is normal (e.g., 20:00-06:00), so day is 06:00-20:00
        dayStartMinutes = endMinutes
        dayEndMinutes = startMinutes
      }
    }
    
    const dayStartAngle = minutesToAngle(dayStartMinutes)
    let dayEndAngle = minutesToAngle(dayEndMinutes)
    
    // For drawing, ensure we draw the arc correctly
    if (dayEndAngle < dayStartAngle) {
      dayEndAngle += 2 * Math.PI
    }

    // Calculate night period angles
    const nightStartAngle = minutesToAngle(nightStartMinutes)
    let nightEndAngle = minutesToAngle(nightEndMinutes)
    
    // For drawing, ensure we draw the arc correctly
    if (nightEndAngle < nightStartAngle) {
      nightEndAngle += 2 * Math.PI
    }
    
    // Draw night period overlay (purplish hue) - draw first
    ctx.strokeStyle = '#6b21a8' // Deep purple
    ctx.fillStyle = 'rgba(107, 33, 168, 0.15)' // Light purple with transparency
    ctx.lineWidth = 3
    ctx.beginPath()
    ctx.arc(centerX, centerY, radius, nightStartAngle, nightEndAngle, false)
    ctx.lineTo(centerX, centerY)
    ctx.closePath()
    ctx.fill()
    ctx.stroke()

    // Draw day period overlay (orange/yellow for photoperiod) - draw on top
    ctx.strokeStyle = '#f59e0b' // Orange
    ctx.fillStyle = 'rgba(251, 191, 36, 0.2)' // Yellow/Orange with transparency
    ctx.lineWidth = 3
    ctx.beginPath()
    ctx.arc(centerX, centerY, radius, dayStartAngle, dayEndAngle, false)
    ctx.lineTo(centerX, centerY)
    ctx.closePath()
    ctx.fill()
    ctx.stroke()
    
    // Markers use original start/end times (the selected period)
    const startAngle = minutesToAngle(startMinutes)
    let endAngle = minutesToAngle(endMinutes)
    if (isOvernight) {
      endAngle += 2 * Math.PI
    }

    // Draw yellow slider in the middle of the SELECTED period arc (the one being edited)
    // Use the original start/end times, not the calculated day/night periods
    // For overnight periods, calculate midpoint correctly
    let midAngle
    if (isOvernight) {
      // For overnight: calculate midpoint by adding 12 hours (half the 24-hour period)
      // Then normalize
      const midMinutes = (startMinutes + endMinutes + 1440) / 2 % 1440
      midAngle = minutesToAngle(midMinutes)
    } else {
      // For normal periods: simple midpoint
      midAngle = (startAngle + endAngle) / 2
    }
    // Normalize to 0-2π range
    if (midAngle < 0) midAngle += 2 * Math.PI
    if (midAngle >= 2 * Math.PI) midAngle -= 2 * Math.PI
    
    const midX = centerX + Math.cos(midAngle) * radius
    const midY = centerY + Math.sin(midAngle) * radius
    
    // Draw yellow slider circle
    ctx.fillStyle = '#fbbf24' // Yellow
    ctx.beginPath()
    ctx.arc(midX, midY, 10, 0, 2 * Math.PI)
    ctx.fill()
    ctx.strokeStyle = '#f59e0b' // Orange border
    ctx.lineWidth = 2
    ctx.stroke()

    // Draw start marker (red)
    const startX = centerX + Math.cos(startAngle) * radius
    const startY = centerY + Math.sin(startAngle) * radius
    ctx.fillStyle = '#dc2626' // Red
    ctx.beginPath()
    ctx.arc(startX, startY, 8, 0, 2 * Math.PI)
    ctx.fill()
    ctx.strokeStyle = '#ffffff'
    ctx.lineWidth = 2
    ctx.stroke()

    // Draw end marker (deep purple)
    const endX = centerX + Math.cos(endAngle) * radius
    const endY = centerY + Math.sin(endAngle) * radius
    ctx.fillStyle = '#6b21a8' // Deep purple
    ctx.beginPath()
    ctx.arc(endX, endY, 8, 0, 2 * Math.PI)
    ctx.fill()
    ctx.strokeStyle = '#ffffff'
    ctx.lineWidth = 2
    ctx.stroke()

    // Draw center dot
    ctx.fillStyle = '#374151'
    ctx.beginPath()
    ctx.arc(centerX, centerY, 4, 0, 2 * Math.PI)
    ctx.fill()
  }

  useEffect(() => {
    drawClock()
  }, [dayStartTime, dayEndTime, period])

  // Enforce locked photoperiod duration
  useEffect(() => {
    if (lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined) {
      const startMinutes = timeToMinutes(dayStartTime)
      const lockedDurationMinutes = lockedPhotoperiodHours * 60
      const expectedEndMinutes = (startMinutes + lockedDurationMinutes) % 1440
      const expectedEndTime = minutesToTime(expectedEndMinutes)
      
      // Only update if end time doesn't match expected
      if (dayEndTime !== expectedEndTime) {
        onDayEndChange(expectedEndTime)
      }
    }
  }, [dayStartTime, lockedPhotoperiodHours, dayEndTime, onDayEndChange])

  function handleMouseDown(event: React.MouseEvent) {
    const canvas = canvasRef.current
    if (!canvas) return

    const rect = canvas.getBoundingClientRect()
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2
    const clickX = event.clientX
    const clickY = event.clientY
    const distance = getDistanceFromCenter(event, rect)
    const radius = (canvas.width / 2) - 40

    // Check if click is near the circle (within 30px of the circle)
    if (Math.abs(distance - radius) > 30) return

    const clickAngle = getAngleFromMouse(event, rect)
    const clickMinutes = angleToMinutes(clickAngle)
    const startMinutes = timeToMinutes(dayStartTime)
    const endMinutes = timeToMinutes(dayEndTime)

    // Calculate marker positions in pixels
    const startAngle = minutesToAngle(startMinutes)
    const endAngle = minutesToAngle(endMinutes)
    const startX = centerX + Math.cos(startAngle) * radius
    const startY = centerY + Math.sin(startAngle) * radius
    const endX = centerX + Math.cos(endAngle) * radius
    const endY = centerY + Math.sin(endAngle) * radius

    // Calculate middle slider position (same calculation as drawing)
    let midAngle = (startAngle + endAngle) / 2
    if (endMinutes < startMinutes) {
      // For overnight, endAngle was adjusted by +2π for drawing
      midAngle = (startAngle + (endAngle + 2 * Math.PI)) / 2
    }
    // Normalize to 0-2π range
    if (midAngle < 0) midAngle += 2 * Math.PI
    if (midAngle >= 2 * Math.PI) midAngle -= 2 * Math.PI
    const midX = centerX + Math.cos(midAngle) * radius
    const midY = centerY + Math.sin(midAngle) * radius

    // Calculate pixel distance to each marker (more accurate than angular distance)
    const startPixelDist = Math.sqrt((clickX - startX) ** 2 + (clickY - startY) ** 2)
    const endPixelDist = Math.sqrt((clickX - endX) ** 2 + (clickY - endY) ** 2)
    const midPixelDist = Math.sqrt((clickX - midX) ** 2 + (clickY - midY) ** 2)

    // Marker radius is 8px for start/end, 10px for middle slider
    const markerThreshold = 15
    const midSliderThreshold = 18

    // Check if click is on the arc (between markers, within 20px of circle)
    const isOnArc = Math.abs(distance - radius) < 20
    
    // Check if angle is between start and end markers
    function normalizeAngle(angle: number): number {
      let normalized = angle % (2 * Math.PI)
      if (normalized < 0) normalized += 2 * Math.PI
      return normalized
    }
    
    const normClick = normalizeAngle(clickAngle)
    const normStart = normalizeAngle(startAngle)
    const normEnd = normalizeAngle(endAngle)
    
    let isBetweenMarkers = false
    if (endMinutes >= startMinutes) {
      // Normal case: start < end
      if (normStart <= normEnd) {
        isBetweenMarkers = normClick >= normStart && normClick <= normEnd
      } else {
        // Wraps around
        isBetweenMarkers = normClick >= normStart || normClick <= normEnd
      }
    } else {
      // Overnight case: end < start (wraps around)
      isBetweenMarkers = normClick >= normStart || normClick <= normEnd
    }

    // If photoperiod is locked, only allow dragging the period as a whole
    if (lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined) {
      // Only allow dragging the period (middle slider or arc)
      if (midPixelDist < midSliderThreshold && isBetweenMarkers) {
        // Click is on middle yellow slider - drag the entire period
        setIsDragging('period')
        setDragOffset(clickMinutes - startMinutes)
      } else if (isOnArc && isBetweenMarkers) {
        // Click is on the arc - drag the entire period
        setIsDragging('period')
        setDragOffset(clickMinutes - startMinutes)
      }
      // Ignore clicks on start/end markers when locked
      return
    }

    // Determine which marker is closer using pixel distance
    // Check middle slider first (highest priority)
    if (midPixelDist < midSliderThreshold && isBetweenMarkers) {
      // Click is on middle yellow slider - drag the entire period
      setIsDragging('period')
      setDragOffset(clickMinutes - startMinutes)
    } else if (startPixelDist < markerThreshold) {
      // Click is on start marker (red) - update immediately to snap to click position
      setIsDragging('start')
      onDayStartChange(minutesToTime(clickMinutes))
    } else if (endPixelDist < markerThreshold) {
      // Click is on end marker (purple) - update immediately to snap to click position
      setIsDragging('end')
      onDayEndChange(minutesToTime(clickMinutes))
    } else if (isOnArc && isBetweenMarkers && startPixelDist >= markerThreshold && endPixelDist >= markerThreshold) {
      // Click is on the arc (not near markers) - drag the entire period
      setIsDragging('period')
      setDragOffset(clickMinutes - startMinutes)
    } else {
      // Click is near circle but not on markers or arc - snap to nearest marker
      if (startPixelDist < endPixelDist) {
        setIsDragging('start')
        onDayStartChange(minutesToTime(clickMinutes))
      } else {
        setIsDragging('end')
        onDayEndChange(minutesToTime(clickMinutes))
      }
    }
  }

  useEffect(() => {
    if (!isDragging) return

    function handleMouseMove(event: MouseEvent) {
      const canvas = canvasRef.current
      if (!canvas) return

      const rect = canvas.getBoundingClientRect()
      const angle = getAngleFromMouse(event, rect)
      const minutes = angleToMinutes(angle)
      const time = minutesToTime(minutes)

      if (isDragging === 'start') {
        // Only allow if photoperiod is not locked
        if (lockedPhotoperiodHours === null || lockedPhotoperiodHours === undefined) {
          onDayStartChange(time)
        }
      } else if (isDragging === 'end') {
        // Only allow if photoperiod is not locked
        if (lockedPhotoperiodHours === null || lockedPhotoperiodHours === undefined) {
          onDayEndChange(time)
        }
      } else if (isDragging === 'period') {
        // Move entire period by maintaining the offset
        const newStartMinutes = minutes - dragOffset
        
        // Normalize to 0-1439 range
        let normalizedStart = newStartMinutes % 1440
        if (normalizedStart < 0) normalizedStart += 1440
        
        // Calculate end time
        let normalizedEnd: number
        if (lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined) {
          // Use locked duration
          const lockedDurationMinutes = lockedPhotoperiodHours * 60
          normalizedEnd = (normalizedStart + lockedDurationMinutes) % 1440
          if (normalizedEnd < 0) normalizedEnd += 1440
        } else {
          // Use current duration
          const startMinutes = timeToMinutes(dayStartTime)
          const endMinutes = timeToMinutes(dayEndTime)
          const periodDuration = endMinutes - startMinutes < 0 ? endMinutes - startMinutes + 1440 : endMinutes - startMinutes
          normalizedEnd = (normalizedStart + periodDuration) % 1440
          if (normalizedEnd < 0) normalizedEnd += 1440
        }
        
        onDayStartChange(minutesToTime(normalizedStart))
        onDayEndChange(minutesToTime(normalizedEnd))
      }
    }

    function handleMouseUp() {
      setIsDragging(null)
      setDragOffset(0)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isDragging, dragOffset, dayStartTime, dayEndTime, onDayStartChange, onDayEndChange])

  return (
    <div className="flex flex-col items-center">
      {label && (
        <label className="block text-sm font-medium text-gray-700 mb-4">
          {label}
        </label>
      )}
      <div className="flex items-start gap-6">
        {showPresetButtons && (
          <div className="flex flex-col gap-2 pt-4">
            <button
              onClick={() => {
                onDayStartChange('17:00')
                onDayEndChange('11:00')
              }}
              className="px-4 py-2 bg-green-600 text-white font-semibold rounded-md hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-green-500 shadow-md transition-colors"
              title="Set to Veg schedule: 17:00 - 11:00"
            >
              Veg
            </button>
            <button
              onClick={() => {
                onDayStartChange('17:00')
                onDayEndChange('05:00')
              }}
              className="px-4 py-2 bg-purple-600 text-white font-semibold rounded-md hover:bg-purple-700 focus:outline-none focus:ring-2 focus:ring-purple-500 shadow-md transition-colors"
              title="Set to Flower schedule: 17:00 - 05:00"
            >
              Flower
            </button>
          </div>
        )}
        <div className="relative">
          <canvas
            ref={canvasRef}
            width={300}
            height={300}
            onMouseDown={handleMouseDown}
            className="cursor-pointer"
          />
        </div>
        <div className="flex flex-col gap-4 pt-4">
          <div className="flex items-end gap-3">
            <div className="space-y-1 flex-1">
              <label className="block text-sm font-medium text-gray-700">
                Start Time
              </label>
              <input
                type="time"
                value={dayStartTime}
                onChange={(e) => {
                  if (lockedPhotoperiodHours === null || lockedPhotoperiodHours === undefined) {
                    onDayStartChange(e.target.value)
                  }
                }}
                disabled={lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined}
                className={`border-2 border-gray-400 rounded-md px-3 py-2 bg-white text-gray-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 w-full ${
                  lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined
                    ? 'opacity-50 cursor-not-allowed bg-gray-100'
                    : ''
                }`}
              />
              <p className="text-xs text-gray-500">
                <span className="text-red-600 font-medium">●</span> Red marker
                {lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined && ' (locked)'}
              </p>
            </div>
            {onRampUpChange && rampUpDuration !== undefined && (
              <div className="space-y-1 flex-1">
                <label className="block text-sm font-medium text-gray-700">
                  Ramp Up (min)
                </label>
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={rampUpDuration !== null && rampUpDuration !== undefined ? rampUpDuration : ''}
                  onChange={(e) => onRampUpChange(e.target.value ? parseInt(e.target.value) : null)}
                  className="border-2 border-gray-400 rounded-md px-3 py-2 bg-white text-gray-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 w-full"
                />
              </div>
            )}
          </div>
          <div className="flex items-end gap-3">
            <div className="space-y-1 flex-1">
              <label className="block text-sm font-medium text-gray-700">
                End Time
              </label>
              <input
                type="time"
                value={dayEndTime}
                onChange={(e) => {
                  if (lockedPhotoperiodHours === null || lockedPhotoperiodHours === undefined) {
                    onDayEndChange(e.target.value)
                  }
                }}
                disabled={lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined}
                className={`border-2 border-gray-400 rounded-md px-3 py-2 bg-white text-gray-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 w-full ${
                  lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined
                    ? 'opacity-50 cursor-not-allowed bg-gray-100'
                    : ''
                }`}
              />
              <p className="text-xs text-gray-500">
                <span className="text-purple-800 font-medium">●</span> Purple marker
                {lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined && ' (locked)'}
              </p>
            </div>
            {onRampDownChange && rampDownDuration !== undefined && (
              <div className="space-y-1 flex-1">
                <label className="block text-sm font-medium text-gray-700">
                  Ramp Down (min)
                </label>
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={rampDownDuration !== null && rampDownDuration !== undefined ? rampDownDuration : ''}
                  onChange={(e) => onRampDownChange(e.target.value ? parseInt(e.target.value) : null)}
                  className="border-2 border-gray-400 rounded-md px-3 py-2 bg-white text-gray-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 w-full"
                />
              </div>
            )}
          </div>
          <div className="pt-2 border-t border-gray-200">
            <div className="text-sm font-medium text-gray-700 mb-1">
              Photoperiod
            </div>
            <div className="text-lg font-semibold text-gray-900">
              {calculatePhotoperiod().toFixed(1)} hours
            </div>
          </div>
        </div>
      </div>
      <p className="text-xs text-gray-500 mt-4 text-center max-w-2xl">
        Click and drag the <span className="text-red-600 font-medium">red</span> marker for start time, <span className="text-purple-800 font-medium">purple</span> marker for end time, or drag the arc to move the entire period. You can also edit times directly using the input fields.
      </p>
    </div>
  )
}

