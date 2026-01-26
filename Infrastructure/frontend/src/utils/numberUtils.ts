/**
 * Safe parseInt with fallback value
 */
export function safeParseInt(value: string | number | undefined | null, fallback: number = 0): number {
  if (value === undefined || value === null || value === '') {
    return fallback
  }
  
  const parsed = parseInt(String(value), 10)
  return isNaN(parsed) ? fallback : parsed
}

/**
 * Safe parseFloat with fallback value
 */
export function safeParseFloat(value: string | number | undefined | null, fallback: number = 0): number {
  if (value === undefined || value === null || value === '') {
    return fallback
  }
  
  const parsed = parseFloat(String(value))
  return isNaN(parsed) ? fallback : parsed
}

/**
 * Clamp a number between min and max values
 */
export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

/**
 * Safe parseInt with clamping
 */
export function safeParseIntClamped(value: string | number | undefined | null, min: number, max: number, fallback: number = 0): number {
  return clamp(safeParseInt(value, fallback), min, max)
}

/**
 * Safe parseFloat with clamping
 */
export function safeParseFloatClamped(value: string | number | undefined | null, min: number, max: number, fallback: number = 0): number {
  return clamp(safeParseFloat(value, fallback), min, max)
}
