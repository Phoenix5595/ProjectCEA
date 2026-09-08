import { describe, expect, it } from 'vitest'
import { getEventDisplay, getKnownEventTypes } from '../presentation/eventRegistry'

describe('getEventDisplay', () => {
  it('returns a human-readable label for known event types', () => {
    const display = getEventDisplay('relay.state_changed')
    expect(display.label).toBe('Relay state changed')
    expect(display.severity).toBe('info')
  })

  it('returns a fallback display for unknown event types', () => {
    const display = getEventDisplay('unknown.event_type')
    expect(display.label).toBe('Unknown event type')
    expect(display.severity).toBe('info')
  })

  it('classifies failsafe events as critical', () => {
    const display = getEventDisplay('system.failsafe_raised')
    expect(display.severity).toBe('critical')
  })

  it('classifies sensor degraded events as warning', () => {
    const display = getEventDisplay('sensor.degraded')
    expect(display.severity).toBe('warning')
  })
})

describe('getKnownEventTypes', () => {
  it('returns a non-empty list of known event type strings', () => {
    const types = getKnownEventTypes()
    expect(types.length).toBeGreaterThan(0)
    expect(types).toContain('relay.state_changed')
  })

  it('returns strings only', () => {
    for (const t of getKnownEventTypes()) {
      expect(typeof t).toBe('string')
    }
  })
})
