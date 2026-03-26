/**
 * Consolidated time conversion utilities for CircularTimePicker and climatePeriodTimeline.
 * All time math functions are defined here once and imported where needed.
 */

import type { MouseEvent as ReactMouseEvent } from 'react'

/**
 * Convert time string (HH:MM) to minutes since midnight.
 * Handles both simple HH:MM and trimmed input.
 */
export function timeToMinutes(time: string): number {
  const parts = time.trim().split(':')
  const h = Number(parts[0])
  const m = Number(parts[1] ?? 0)
  if (Number.isNaN(h) || Number.isNaN(m)) return 0
  return (h * 60 + m) % 1440
}

/**
 * Convert minutes since midnight to time string (HH:MM).
 */
export function minutesToTime(minutes: number): string {
  const hours = Math.floor(minutes / 60) % 24
  const mins = minutes % 60
  return `${hours.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}`
}

/**
 * Convert angle (radians) to minutes.
 * 12:00 noon = top of circle (angle -π/2), 00:00 midnight = bottom (angle π/2)
 */
export function angleToMinutes(angle: number): number {
  let normalizedAngle = angle % (2 * Math.PI)
  if (normalizedAngle < 0) normalizedAngle += 2 * Math.PI

  const rotatedAngle = (normalizedAngle + Math.PI / 2) % (2 * Math.PI)
  const hours = (rotatedAngle / (2 * Math.PI)) * 24
  const actualHours = (hours + 12) % 24
  const roundedHours = Math.round(actualHours) % 24
  return roundedHours * 60
}

/**
 * Convert minutes to angle (radians).
 * 12:00 noon = top of circle (angle -π/2), 00:00 midnight = bottom
 */
export function minutesToAngle(minutes: number): number {
  const hours = minutes / 60
  return ((hours - 12) / 24) * 2 * Math.PI - Math.PI / 2
}

/**
 * Calculate photoperiod (day duration) in hours.
 */
export function calculatePhotoperiod(dayStartTime: string, dayEndTime: string): number {
  const startMinutes = timeToMinutes(dayStartTime)
  const endMinutes = timeToMinutes(dayEndTime)
  let duration = endMinutes - startMinutes
  if (duration < 0) {
    duration += 1440
  }
  return duration / 60
}

/**
 * Check if a period is an overnight period (end time is before start time).
 */
export function isOvernightPeriod(startTime: string, endTime: string): boolean {
  const startMinutes = timeToMinutes(startTime)
  const endMinutes = timeToMinutes(endTime)
  return endMinutes < startMinutes
}

/**
 * Calculate the midpoint angle for a period arc.
 * Handles overnight periods correctly.
 */
export function calculateMidAngle(
  startMinutes: number,
  endMinutes: number,
  isOvernight: boolean
): number {
  let midAngle: number
  if (isOvernight) {
    const midMinutes = ((startMinutes + endMinutes + 1440) / 2) % 1440
    midAngle = minutesToAngle(midMinutes)
  } else {
    const startAngle = minutesToAngle(startMinutes)
    const endAngle = minutesToAngle(endMinutes)
    midAngle = (startAngle + endAngle) / 2
  }
  // Normalize to 0-2π range
  if (midAngle < 0) midAngle += 2 * Math.PI
  if (midAngle >= 2 * Math.PI) midAngle -= 2 * Math.PI
  return midAngle
}

/**
 * Get angle from mouse position relative to center.
 * Returns angle in the same coordinate system as minutesToAngle (noon at top = -Math.PI/2)
 */
export function getAngleFromMouse(
  event: globalThis.MouseEvent | ReactMouseEvent,
  rect: DOMRect
): number {
  const centerX = rect.left + rect.width / 2
  const centerY = rect.top + rect.height / 2
  const x = event.clientX - centerX
  const y = event.clientY - centerY
  return Math.atan2(y, x)
}

/**
 * Get distance from center in pixels.
 */
export function getDistanceFromCenter(
  event: globalThis.MouseEvent | ReactMouseEvent,
  rect: DOMRect
): number {
  const centerX = rect.left + rect.width / 2
  const centerY = rect.top + rect.height / 2
  const x = event.clientX - centerX
  const y = event.clientY - centerY
  return Math.sqrt(x * x + y * y)
}

/**
 * Normalize angle to 0-2π range.
 */
export function normalizeAngle(angle: number): number {
  let normalized = angle % (2 * Math.PI)
  if (normalized < 0) normalized += 2 * Math.PI
  return normalized
}
