import { describe, expect, it } from 'vitest'

import { eventDetailLine } from '../presentation/eventSourceParts'

import { makeEventEntry, makeEventEntryWith } from './testFactories'

describe('eventDetailLine', () => {
  it('summarises relay events with the entity label, room and state', () => {
    const channel = makeEventEntryWith({
      type: 'relay.state_changed',
      category: 'relay',
      payload: { room: 'Flower Room', state: true },
      entity: { entityType: 'relay_channel', entityId: '2' },
    })
    expect(eventDetailLine(channel)).toBe('Channel 2 · Flower Room · ON')

    const device = makeEventEntryWith({
      type: 'relay.commanded',
      category: 'relay',
      payload: { room: 'Veg Room', observed_state: false },
      entity: { entityType: 'device', entityId: 'heater-1' },
    })
    expect(eventDetailLine(device)).toBe('heater-1 · Veg Room · OFF')
  })

  it('summarises control events with the controller and setpoint change', () => {
    const changed = makeEventEntryWith({
      type: 'control.setpoint_changed',
      category: 'control',
      payload: {
        room: 'Veg Room',
        controller: 'pid',
        device_type: 'heating',
        previous_setpoint: 24,
        effective_setpoint: 25,
      },
    })
    expect(eventDetailLine(changed)).toBe('heating · Veg Room · SP 24→25')

    const firstSetpoint = makeEventEntryWith({
      type: 'control.setpoint_changed',
      category: 'control',
      payload: { room: 'Veg Room', controller: 'pid', effective_setpoint: 25 },
    })
    expect(eventDetailLine(firstSetpoint)).toBe('pid · Veg Room · SP 25')
  })

  it('summarises ramp events with the ramp type and value change', () => {
    const ramp = makeEventEntryWith({
      type: 'ramp.started',
      category: 'ramp',
      payload: { room: 'Flower Room', ramp_type: 'light', start_value: 0, target_value: 80 },
    })
    expect(eventDetailLine(ramp)).toBe('light · Flower Room · 0→80')

    const targetOnly = makeEventEntryWith({
      type: 'ramp.started',
      category: 'ramp',
      payload: { room: 'Flower Room', ramp_type: 'light', target_value: 80 },
    })
    expect(eventDetailLine(targetOnly)).toBe('light · Flower Room · →80')
  })

  it('summarises manual overrides with the Toronto expiry clock', () => {
    const entry = makeEventEntryWith({
      type: 'manual_override.started',
      category: 'manual_override',
      payload: { room: 'Flower Room', mode: 'manual', expires_at: '2026-10-25T18:30:00Z' },
      entity: { entityType: 'device', entityId: 'heater-1' },
    })
    expect(eventDetailLine(entry)).toBe('heater-1 · Flower Room · manual until 14:30')

    const timed = makeEventEntryWith({
      type: 'manual_override.started',
      category: 'manual_override',
      payload: { mode: 'manual', duration_seconds: 450 },
      entity: null,
    })
    expect(eventDetailLine(timed)).toBe('manual for 8 min')
  })

  it('summarises mutation events with the first field change', () => {
    const entry = makeEventEntryWith({
      type: 'config.updated',
      category: 'mutation',
      payload: {
        room: 'Flower Room',
        operation: 'update',
        changes: [{ key: 'channel', before: 2, after: 5 }],
      },
      entity: { entityType: 'device', entityId: '1' },
    })
    expect(eventDetailLine(entry)).toBe('device 1 · Flower Room · channel: 2→5')
  })

  it('counts the remaining mutation changes', () => {
    const entry = makeEventEntryWith({
      type: 'config.updated',
      category: 'mutation',
      payload: {
        operation: 'update',
        changes: [
          { key: 'channel', before: 2, after: 5 },
          { key: 'name', before: 'a', after: 'b' },
          { key: 'notes', before: null, after: 'x' },
        ],
      },
      entity: { entityType: 'device', entityId: '1' },
    })
    expect(eventDetailLine(entry)).toBe('device 1 · channel: 2→5 +2 more')
  })

  it('summarises alarm events with the code, state and room', () => {
    const entry = makeEventEntryWith({
      type: 'alarm.raised',
      category: 'alarm',
      payload: { room: 'Flower Room', alarm_code: 'temp_high', state: 'raised' },
    })
    expect(eventDetailLine(entry)).toBe('temp_high · raised · Flower Room')
  })

  it('summarises system events with the component and state', () => {
    const entry = makeEventEntryWith({
      type: 'transport.degraded',
      category: 'system',
      payload: { component: 'transport', state: 'degraded', room: 'Veg Room' },
    })
    expect(eventDetailLine(entry)).toBe('transport · degraded')
  })

  it('falls back to the room for any other category', () => {
    const entry = makeEventEntry('1-0', 'custom.thing', 'custom', { room: 'Veg Room' })
    expect(eventDetailLine(entry)).toBe('Veg Room')
  })

  it('returns a dash when no source field is present', () => {
    const entry = makeEventEntry('1-0', 'custom.thing', 'relay')
    expect(eventDetailLine(entry)).toBe('—')
  })
})
