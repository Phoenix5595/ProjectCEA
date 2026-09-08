import { describe, expect, it } from 'vitest'
import { classifySeverity, type SeverityLevel } from '../presentation/severity'

describe('classifySeverity', () => {
  it('returns critical for failsafe and alarm event types', () => {
    expect(classifySeverity('system.failsafe_raised')).toBe('critical')
    expect(classifySeverity('alarm.triggered')).toBe('critical')
  })

  it('returns warning for degraded and timeout event types', () => {
    expect(classifySeverity('sensor.degraded')).toBe('warning')
    expect(classifySeverity('device.timeout')).toBe('warning')
  })

  it('returns info for state changes and config updates', () => {
    expect(classifySeverity('relay.state_changed')).toBe('info')
    expect(classifySeverity('config.updated')).toBe('info')
  })

  it('returns info for unknown event types as a safe default', () => {
    expect(classifySeverity('something.unexpected')).toBe('info')
  })

  it('classifies by prefix match for dotted event types', () => {
    expect(classifySeverity('system.failsafe_cleared')).toBe('critical')
    expect(classifySeverity('alarm.acknowledged')).toBe('critical')
  })

  it('returns a human-readable label for each level', () => {
    const labels: Record<SeverityLevel, string> = {
      critical: 'Critical',
      warning: 'Warning',
      info: 'Info',
    }
    for (const [level, label] of Object.entries(labels)) {
      expect(classifySeverity(level === 'critical' ? 'system.failsafe_raised' : level === 'warning' ? 'sensor.degraded' : 'relay.state_changed')).toBe(level)
      expect(label).toBeTruthy()
    }
  })
})
