import { useState, useEffect, useCallback } from 'react'
import {
  timeToMinutes,
  minutesToTime,
  minutesToAngle,
  angleToMinutes,
  getAngleFromMouse,
  getDistanceFromCenter,
  normalizeAngle,
  isOvernightPeriod,
  calculateMidAngle,
} from '../utils/timeMath'

interface UseClockInteractionProps {
  canvasRef: React.RefObject<HTMLCanvasElement | null>
  dayStartTime: string
  dayEndTime: string
  onDayStartChange: (time: string) => void
  onDayEndChange: (time: string) => void
  lockedPhotoperiodHours?: number | null
}

interface UseClockInteractionReturn {
  isDragging: 'start' | 'end' | 'period' | null
  handleMouseDown: (event: React.MouseEvent) => void
}

export function useClockInteraction({
  canvasRef,
  dayStartTime,
  dayEndTime,
  onDayStartChange,
  onDayEndChange,
  lockedPhotoperiodHours = null,
}: UseClockInteractionProps): UseClockInteractionReturn {
  const [isDragging, setIsDragging] = useState<'start' | 'end' | 'period' | null>(null)
  const [dragOffset, setDragOffset] = useState<number>(0)

  const handleMouseDown = useCallback(
    (event: React.MouseEvent) => {
      const canvas = canvasRef.current
      if (!canvas) return

      const rect = canvas.getBoundingClientRect()
      const centerX = rect.left + rect.width / 2
      const centerY = rect.top + rect.height / 2
      const clickX = event.clientX
      const clickY = event.clientY
      const distance = getDistanceFromCenter(event, rect)
      const radius = canvas.width / 2 - 18

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

      // Calculate middle slider position
      const isOvernight = isOvernightPeriod(dayStartTime, dayEndTime)
      const midAngle = calculateMidAngle(startMinutes, endMinutes, isOvernight)
      const midX = centerX + Math.cos(midAngle) * radius
      const midY = centerY + Math.sin(midAngle) * radius

      // Calculate pixel distance to each marker
      const startPixelDist = Math.sqrt((clickX - startX) ** 2 + (clickY - startY) ** 2)
      const endPixelDist = Math.sqrt((clickX - endX) ** 2 + (clickY - endY) ** 2)
      const midPixelDist = Math.sqrt((clickX - midX) ** 2 + (clickY - midY) ** 2)

      const markerThreshold = 15
      const midSliderThreshold = 18

      // Check if click is on the arc (between markers, within 20px of circle)
      const isOnArc = Math.abs(distance - radius) < 20

      // Check if angle is between start and end markers
      const normClick = normalizeAngle(clickAngle)
      const normStart = normalizeAngle(startAngle)
      const normEnd = normalizeAngle(endAngle)

      let isBetweenMarkers = false
      if (endMinutes >= startMinutes) {
        if (normStart <= normEnd) {
          isBetweenMarkers = normClick >= normStart && normClick <= normEnd
        } else {
          isBetweenMarkers = normClick >= normStart || normClick <= normEnd
        }
      } else {
        isBetweenMarkers = normClick >= normStart || normClick <= normEnd
      }

      // If photoperiod is locked, only allow dragging the period as a whole
      if (lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined) {
        if (midPixelDist < midSliderThreshold && isBetweenMarkers) {
          setIsDragging('period')
          setDragOffset(clickMinutes - startMinutes)
        } else if (isOnArc && isBetweenMarkers) {
          setIsDragging('period')
          setDragOffset(clickMinutes - startMinutes)
        }
        return
      }

      // Determine which marker is closer using pixel distance
      if (midPixelDist < midSliderThreshold && isBetweenMarkers) {
        setIsDragging('period')
        setDragOffset(clickMinutes - startMinutes)
      } else if (startPixelDist < markerThreshold) {
        setIsDragging('start')
        onDayStartChange(minutesToTime(clickMinutes))
      } else if (endPixelDist < markerThreshold) {
        setIsDragging('end')
        onDayEndChange(minutesToTime(clickMinutes))
      } else if (
        isOnArc &&
        isBetweenMarkers &&
        startPixelDist >= markerThreshold &&
        endPixelDist >= markerThreshold
      ) {
        setIsDragging('period')
        setDragOffset(clickMinutes - startMinutes)
      } else {
        // Snap to nearest marker
        if (startPixelDist < endPixelDist) {
          setIsDragging('start')
          onDayStartChange(minutesToTime(clickMinutes))
        } else {
          setIsDragging('end')
          onDayEndChange(minutesToTime(clickMinutes))
        }
      }
    },
    [canvasRef, dayStartTime, dayEndTime, onDayStartChange, onDayEndChange, lockedPhotoperiodHours]
  )

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
        if (lockedPhotoperiodHours === null || lockedPhotoperiodHours === undefined) {
          onDayStartChange(time)
        }
      } else if (isDragging === 'end') {
        if (lockedPhotoperiodHours === null || lockedPhotoperiodHours === undefined) {
          onDayEndChange(time)
        }
      } else if (isDragging === 'period') {
        const newStartMinutes = minutes - dragOffset

        // Normalize to 0-1439 range
        let normalizedStart = newStartMinutes % 1440
        if (normalizedStart < 0) normalizedStart += 1440

        if (lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined) {
          onDayStartChange(minutesToTime(normalizedStart))
        } else {
          // Use current duration for unlocked photoperiod
          const startMins = timeToMinutes(dayStartTime)
          const endMins = timeToMinutes(dayEndTime)
          const periodDuration =
            endMins - startMins < 0 ? endMins - startMins + 1440 : endMins - startMins
          let normalizedEnd = (normalizedStart + periodDuration) % 1440
          if (normalizedEnd < 0) normalizedEnd += 1440

          onDayStartChange(minutesToTime(normalizedStart))
          onDayEndChange(minutesToTime(normalizedEnd))
        }
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
  }, [isDragging, dragOffset, dayStartTime, dayEndTime, onDayStartChange, onDayEndChange, canvasRef, lockedPhotoperiodHours])

  return {
    isDragging,
    handleMouseDown,
  }
}
