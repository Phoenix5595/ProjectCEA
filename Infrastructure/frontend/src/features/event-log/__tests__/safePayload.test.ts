import { describe, expect, it } from 'vitest'
import { extractSafeFields, formatPayloadValue } from '../presentation/safePayload'

describe('extractSafeFields', () => {
  it('returns allowlisted fields and drops unknown keys', () => {
    const payload = { device_id: 'heater-1', state: 'on', secret_token: 'abc', password: 'xyz', internal_note: 'skip' }
    const safe = extractSafeFields(payload)
    expect(safe).toEqual({ device_id: 'heater-1', state: 'on' })
    expect(safe).not.toHaveProperty('secret_token')
    expect(safe).not.toHaveProperty('password')
    expect(safe).not.toHaveProperty('internal_note')
  })

  it('preserves numeric and boolean safe fields', () => {
    const payload = { setpoint: 22.5, active: true, room: 'flower' }
    expect(extractSafeFields(payload)).toEqual({ setpoint: 22.5, active: true, room: 'flower' })
  })

  it('returns an empty object for payloads with no safe fields', () => {
    expect(extractSafeFields({ api_key: 'secret', token: 'abc' })).toEqual({})
  })

  it('returns an empty object for empty payloads', () => {
    expect(extractSafeFields({})).toEqual({})
  })

  it('does not mutate the original payload', () => {
    const payload = { device_id: 'fan-1', password: 'secret' }
    const original = { ...payload }
    extractSafeFields(payload)
    expect(payload).toEqual(original)
  })
})

describe('formatPayloadValue', () => {
  it('formats strings as-is', () => {
    expect(formatPayloadValue('flower')).toBe('flower')
  })

  it('formats numbers with reasonable precision', () => {
    expect(formatPayloadValue(22.5)).toBe('22.5')
    expect(formatPayloadValue(42)).toBe('42')
  })

  it('formats booleans as lowercase', () => {
    expect(formatPayloadValue(true)).toBe('true')
    expect(formatPayloadValue(false)).toBe('false')
  })

  it('formats null and undefined as a dash', () => {
    expect(formatPayloadValue(null)).toBe('--')
    expect(formatPayloadValue(undefined)).toBe('--')
  })

  it('truncates long string values', () => {
    const long = 'a'.repeat(200)
    const result = formatPayloadValue(long)
    expect(result.length).toBeLessThanOrEqual(103)
    expect(result.endsWith('...')).toBe(true)
  })

  it('serializes objects as compact JSON', () => {
    expect(formatPayloadValue({ a: 1 })).toBe('{"a":1}')
  })
})
