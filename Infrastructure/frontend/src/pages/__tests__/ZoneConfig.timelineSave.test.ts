import { describe, expect, it } from 'vitest'
import { shouldPersistLegacyTimelineValues } from '../ZoneConfig'

describe('ZoneConfig legacy timeline save policy', () => {
  it('does not persist timeline-owned values while a non-constant timeline baseline is active', () => {
    expect(shouldPersistLegacyTimelineValues(false, {
      room: { location: 'flower', cluster: 'main' },
      baseConfigRevision: 'config-1',
      periods: [],
      photoperiod: {
        dayStartTime: '06:00',
        nightStartTime: '18:00',
        rampUpMinutes: 20,
        rampDownMinutes: 20,
      },
    })).toBe(false)
  })

  it('retains legacy persistence for timeline-unavailable fallback tables', () => {
    expect(shouldPersistLegacyTimelineValues(false, null)).toBe(true)
  })
})
