import { useEffect } from 'react'
import {
  timeToMinutes,
  minutesToAngle,
  isOvernightPeriod,
  calculateMidAngle,
} from '../utils/timeMath'

interface CircularClockFaceProps {
  canvasRef: React.RefObject<HTMLCanvasElement | null>
  size: number
  dayStartTime: string
  dayEndTime: string
  period: 'day' | 'night'
}

/**
 * Pure canvas rendering component for the circular time picker clock face.
 * Renders hour markers, day/night period overlays, and draggable markers.
 * No state or effects - receives all data via props and draws directly.
 */
export default function CircularClockFace({
  canvasRef,
  size,
  dayStartTime,
  dayEndTime,
  period,
}: CircularClockFaceProps) {
  const drawClock = () => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const centerX = size / 2
    const centerY = size / 2
    const radius = size / 2 - 20
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
      const angle = ((hour - 12) / 24) * 2 * Math.PI - Math.PI / 2
      const x1 = centerX + Math.cos(angle) * radius
      const y1 = centerY + Math.sin(angle) * radius
      const x2 = centerX + Math.cos(angle) * markerRadius
      const y2 = centerY + Math.sin(angle) * markerRadius

      ctx.beginPath()
      ctx.moveTo(x1, y1)
      ctx.lineTo(x2, y2)
      ctx.stroke()

      const labelX = centerX + Math.cos(angle) * (markerRadius - 20)
      const labelY = centerY + Math.sin(angle) * (markerRadius - 20)
      ctx.fillStyle = '#6b7280'
      ctx.fillText(hour.toString(), labelX, labelY)
    }

    // Draw period arc - always show the DAY period overlay
    const startMinutes = timeToMinutes(dayStartTime)
    const endMinutes = timeToMinutes(dayEndTime)
    const isOvernight = isOvernightPeriod(dayStartTime, dayEndTime)

    // The dayStartTime and dayEndTime props represent the period being edited
    // If period="day", they represent the DAY period
    // If period="night", they represent the NIGHT period
    // For the overlay, we always want to show DAY in orange/yellow and NIGHT in purple
    let dayStartMinutes: number, dayEndMinutes: number, nightStartMinutes: number, nightEndMinutes: number

    if (period === 'day') {
      dayStartMinutes = startMinutes
      dayEndMinutes = endMinutes
      nightStartMinutes = endMinutes
      nightEndMinutes = startMinutes
    } else {
      nightStartMinutes = startMinutes
      nightEndMinutes = endMinutes
      dayStartMinutes = endMinutes
      dayEndMinutes = startMinutes
    }

    const dayStartAngle = minutesToAngle(dayStartMinutes)
    let dayEndAngle = minutesToAngle(dayEndMinutes)

    if (dayEndAngle < dayStartAngle) {
      dayEndAngle += 2 * Math.PI
    }

    // Calculate night period angles
    const nightStartAngle = minutesToAngle(nightStartMinutes)
    let nightEndAngle = minutesToAngle(nightEndMinutes)

    if (nightEndAngle < nightStartAngle) {
      nightEndAngle += 2 * Math.PI
    }

    // Draw night period overlay (purplish hue) - draw first
    ctx.strokeStyle = '#6b21a8'
    ctx.fillStyle = 'rgba(107, 33, 168, 0.15)'
    ctx.lineWidth = 3
    ctx.beginPath()
    ctx.arc(centerX, centerY, radius, nightStartAngle, nightEndAngle, false)
    ctx.lineTo(centerX, centerY)
    ctx.closePath()
    ctx.fill()
    ctx.stroke()

    // Draw day period overlay (orange/yellow for photoperiod) - draw on top
    ctx.strokeStyle = '#f59e0b'
    ctx.fillStyle = 'rgba(251, 191, 36, 0.2)'
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

    // Draw yellow slider in the middle of the SELECTED period arc
    const midAngle = calculateMidAngle(startMinutes, endMinutes, isOvernight)

    const midX = centerX + Math.cos(midAngle) * radius
    const midY = centerY + Math.sin(midAngle) * radius

    // Draw yellow slider circle
    ctx.fillStyle = '#fbbf24'
    ctx.beginPath()
    ctx.arc(midX, midY, 10, 0, 2 * Math.PI)
    ctx.fill()
    ctx.strokeStyle = '#f59e0b'
    ctx.lineWidth = 2
    ctx.stroke()

    // Draw start marker (red)
    const startX = centerX + Math.cos(startAngle) * radius
    const startY = centerY + Math.sin(startAngle) * radius
    ctx.fillStyle = '#dc2626'
    ctx.beginPath()
    ctx.arc(startX, startY, 8, 0, 2 * Math.PI)
    ctx.fill()
    ctx.strokeStyle = '#ffffff'
    ctx.lineWidth = 2
    ctx.stroke()

    // Draw end marker (deep purple)
    const endX = centerX + Math.cos(endAngle) * radius
    const endY = centerY + Math.sin(endAngle) * radius
    ctx.fillStyle = '#6b21a8'
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
  }, [dayStartTime, dayEndTime, period, size])

  return null
}
