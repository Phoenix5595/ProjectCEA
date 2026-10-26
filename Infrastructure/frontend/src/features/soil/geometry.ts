/** Bed geometry helpers for RS-485 soil probe placement (R9). */

/** Beds in display order: Back Bed first (left in Sensor Settings, top on the Soil page). */
export const BED_DISPLAY_ORDER = ['Back Bed', 'Front Bed'] as const

/** Round a bed fraction to the 3 decimals stored in `soil_probe_placement`. */
export function roundFrac(value: number): number {
  return Math.round(value * 1000) / 1000
}

export function clampFrac(value: number): number {
  return Math.min(1, Math.max(0, value))
}

/** Bed fractions (`x_frac` across the width, `y_frac` along the length) for a pointer. */
export function fracFromPointer(
  rect: DOMRect,
  clientX: number,
  clientY: number
): { x_frac: number; y_frac: number } {
  const x = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5
  const y = rect.height > 0 ? (clientY - rect.top) / rect.height : 0.5
  return { x_frac: roundFrac(clampFrac(x)), y_frac: roundFrac(clampFrac(y)) }
}
