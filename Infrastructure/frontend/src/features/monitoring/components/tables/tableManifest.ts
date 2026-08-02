/**
 * Mapping helpers between manifest table rows and sensor names.
 *
 * The canonical Grafana tables order rows by a fixed CASE expression
 * (dry_bulb, wet_bulb, rh, vpd, co2, pressure, secondary_temp, secondary_rh,
 * water_level). This module maps a manifest display label back to its base
 * sensor name and, given a node suffix, to the full sensor name used by the
 * live/statistics payloads.
 */

/** Display label -> base sensor name (canonical CASE mapping). */
export const ROW_TO_BASE: Record<string, string> = {
  'Dry Bulb': 'dry_bulb',
  'Wet Bulb': 'wet_bulb',
  RH: 'rh',
  VPD: 'vpd',
  CO2: 'co2',
  Pressure: 'pressure',
  'Secondary Temp': 'secondary_temp',
  'Secondary RH': 'secondary_rh',
  'Water Level': 'water_level',
}

/** Base sensor name -> unit family. */
export const BASE_TO_FAMILY: Record<string, string> = {
  dry_bulb: 'celsius',
  wet_bulb: 'celsius',
  secondary_temp: 'celsius',
  rh: 'percent',
  secondary_rh: 'percent',
  vpd: 'kpa',
  co2: 'ppm',
  pressure: 'hpa',
  water_level: 'mm',
}

/** Full sensor name for a manifest row label and node suffix. */
export function sensorNameForRow(row: string, suffix: string): string | null {
  const base = ROW_TO_BASE[row]
  if (!base) return null
  return `${base}_${suffix}`
}

/** Unit family for a manifest row label, or null when unknown. */
export function familyForRow(row: string): string | null {
  const base = ROW_TO_BASE[row]
  if (!base) return null
  return BASE_TO_FAMILY[base] ?? null
}

/** Strip a trailing " Avg" from an averages-table row label. */
export function baseLabelForAverage(row: string): string {
  return row.endsWith(' Avg') ? row.slice(0, -' Avg'.length) : row
}

/** Parse a statistics display label into its base sensor name and node suffix. */
function parseStatLabel(row: string): { base: string; suffix: string } | null {
  let suffix = 'v'
  let label = row
  if (label.endsWith(' - Front')) {
    suffix = 'f'
    label = label.slice(0, -' - Front'.length)
  } else if (label.endsWith(' - Back')) {
    suffix = 'b'
    label = label.slice(0, -' - Back'.length)
  }
  const base = label.replace(/\s*\([^)]*\)\s*$/, '').trim()
  const baseName = ROW_TO_BASE[base]
  if (!baseName) return null
  return { base: baseName, suffix }
}

/** Full sensor name for a statistics display label, or null when unknown. */
export function sensorNameForStatRow(row: string): string | null {
  const parsed = parseStatLabel(row)
  return parsed ? `${parsed.base}_${parsed.suffix}` : null
}

/** Unit family for a statistics display label, or null when unknown. */
export function familyForStatRow(row: string): string | null {
  const parsed = parseStatLabel(row)
  return parsed ? (BASE_TO_FAMILY[parsed.base] ?? null) : null
}
