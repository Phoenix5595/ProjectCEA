import { describe, expect, it } from 'vitest'
import { formatSetpointFromTo } from '../presentation/setpointChange'

describe('formatSetpointFromTo', () => {
  it.each([
    ['light', 0.442, 0.438, '44.2% → 43.8%'],
    ['light', 0.2, 0.8, '20% → 80%'],
    ['heating', 22.0, 22.6, '22 °C → 22.6 °C'],
    ['cooling', 21.0, 21.5, '21 °C → 21.5 °C'],
    ['vpd', 0.75, 0.77, '0.8 kPa → 0.8 kPa'],
    ['co2', 800, 810, '800 ppm → 810 ppm'],
  ] as const)('renders %s values with correct units', (deviceType, previous, current, expected) => {
    expect(formatSetpointFromTo({ device_type: deviceType, previous_setpoint: previous, effective_setpoint: current })).toBe(expected)
  })

  it('returns nothing for legacy payloads without previous_setpoint', () => {
    expect(formatSetpointFromTo({ device_type: 'light', effective_setpoint: 0.4 })).toBeNull()
  })

  it('returns null for non-numeric values', () => {
    expect(formatSetpointFromTo({ device_type: 'light', previous_setpoint: 'low', effective_setpoint: 0.4 })).toBeNull()
    expect(formatSetpointFromTo({ previous_setpoint: 1, effective_setpoint: null })).toBeNull()
  })

  it('renders unknown device types with raw values', () => {
    expect(formatSetpointFromTo({ device_type: 'humidifier', previous_setpoint: 60, effective_setpoint: 62 })).toBe('60 → 62')
  })
})
