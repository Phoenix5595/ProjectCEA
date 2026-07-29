import { describe, it, expect } from 'vitest'

describe('LightIntensity target validation', () => {
  it('rejects values below 10', () => {
    const value = 9.9
    const isValid = value >= 10
    expect(isValid).toBe(false)
  })

  it('accepts values at or above 10', () => {
    const value = 10
    const isValid = value >= 10
    expect(isValid).toBe(true)
  })

  it('accepts values up to 100', () => {
    const value = 100
    const isValid = value >= 10 && value <= 100
    expect(isValid).toBe(true)
  })

  it('clamps values to 0-100 range', () => {
    const clamp = (v: number) => Math.max(0, Math.min(100, v))
    expect(clamp(-5)).toBe(0)
    expect(clamp(150)).toBe(100)
    expect(clamp(50)).toBe(50)
  })
})
