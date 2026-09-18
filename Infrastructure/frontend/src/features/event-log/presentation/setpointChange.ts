/**
 * From-to rendering for self-contained setpoint-change events. Values carry
 * their device-type unit semantics from the backend: light is a 0-1 fraction
 * (displayed as a percent), heating/cooling are degrees Celsius, VPD is kPa,
 * and CO2 is ppm. Legacy payloads without previous_setpoint render nothing.
 */

const DEVICE_UNIT: Record<string, string> = {
  light: '%',
  heating: ' °C',
  cooling: ' °C',
  vpd: ' kPa',
  co2: ' ppm',
}

const FALLBACK_DEVICE_UNIT = ''

export function formatSetpointFromTo(payload: Record<string, unknown>): string | null {
  const previous = payload.previous_setpoint
  const current = payload.effective_setpoint
  if (typeof previous !== 'number' || typeof current !== 'number') return null
  const deviceType = typeof payload.device_type === 'string' ? payload.device_type : ''
  const unit = DEVICE_UNIT[deviceType] ?? FALLBACK_DEVICE_UNIT
  if (deviceType === 'light') {
    return `${formatFractionPercent(previous)}% → ${formatFractionPercent(current)}%`
  }
  return `${formatNumber(previous, unit)} → ${formatNumber(current, unit)}`
}

function formatFractionPercent(value: number): string {
  const percent = value * 100
  return `${round(percent)}`
}

function formatNumber(value: number, unit: string): string {
  return `${round(value)}${unit}`
}

function round(value: number): string {
  return Number(value.toFixed(1)).toString()
}
