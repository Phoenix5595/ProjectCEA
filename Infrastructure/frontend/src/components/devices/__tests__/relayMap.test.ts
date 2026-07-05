import { describe, it, expect } from 'vitest'
import {
  RELAY_TO_CHANNEL,
  CHANNEL_TO_RELAY,
  getRelayNumber,
  splitRelayByPhysicalLayout,
  type RelayChannelViewModel,
} from '../relayViewModel'

describe('RELAY_TO_CHANNEL', () => {
  const entries: [number, number][] = [
    [1, 15], [2, 0], [3, 14], [4, 1], [5, 13], [6, 2], [7, 12], [8, 3],
    [9, 11], [10, 4], [11, 10], [12, 5], [13, 9], [14, 6], [15, 8], [16, 7],
  ]

  for (const [relay, channel] of entries) {
    it(`maps relay ${relay} → channel ${channel}`, () => {
      expect(RELAY_TO_CHANNEL[relay]).toBe(channel)
    })
  }

  it('has exactly 16 entries', () => {
    expect(Object.keys(RELAY_TO_CHANNEL)).toHaveLength(16)
  })
})

describe('CHANNEL_TO_RELAY', () => {
  const entries: [number, number][] = [
    [15, 1], [0, 2], [14, 3], [1, 4], [13, 5], [2, 6], [12, 7], [3, 8],
    [11, 9], [4, 10], [10, 11], [5, 12], [9, 13], [6, 14], [8, 15], [7, 16],
  ]

  for (const [channel, relay] of entries) {
    it(`maps channel ${channel} → relay ${relay}`, () => {
      expect(CHANNEL_TO_RELAY[channel]).toBe(relay)
    })
  }

  it('has exactly 16 entries', () => {
    expect(Object.keys(CHANNEL_TO_RELAY)).toHaveLength(16)
  })
})

describe('round-trip RELAY_TO_CHANNEL → CHANNEL_TO_RELAY', () => {
  for (let relay = 1; relay <= 16; relay++) {
    it(`relay ${relay} round-trips correctly`, () => {
      const channel = RELAY_TO_CHANNEL[relay]
      expect(CHANNEL_TO_RELAY[channel]).toBe(relay)
    })
  }
})

describe('getRelayNumber', () => {
  it('returns the correct relay for each channel', () => {
    const channels: [number, number][] = [
      [0, 2], [1, 4], [2, 6], [3, 8], [4, 10], [5, 12], [6, 14], [7, 16],
      [8, 15], [9, 13], [10, 11], [11, 9], [12, 7], [13, 5], [14, 3], [15, 1],
    ]
    for (const [channel, expected] of channels) {
      expect(getRelayNumber(channel)).toBe(expected)
    }
  })
})

describe('splitRelayByPhysicalLayout', () => {
  const mockChannels: RelayChannelViewModel[] = Array.from({ length: 16 }, (_, n) => ({
    channel: n,
    pinLabel: `GP${n < 8 ? 'A' : 'B'}${n < 8 ? n : n - 8}`,
    isStateKnown: true,
    isActive: false,
    isAssigned: false,
    assignedDeviceName: null,
    deviceName: `device-ch${n}`,
    displayType: null,
    location: null,
    cluster: null,
    lastStateChangeAt: null,
  }))

  const { leftColumn, rightColumn } = splitRelayByPhysicalLayout(mockChannels)

  it('leftColumn has exactly 8 entries', () => {
    expect(leftColumn).toHaveLength(8)
  })

  it('rightColumn has exactly 8 entries', () => {
    expect(rightColumn).toHaveLength(8)
  })

  it('leftColumn[0..7] maps to relays 1→8 in order (channels 15,0,14,1,13,2,12,3)', () => {
    const expectedChannels = [15, 0, 14, 1, 13, 2, 12, 3]
    expect(leftColumn.map((vm) => vm.channel)).toEqual(expectedChannels)
  })

  it('rightColumn[0..7] maps to relays 16→9 in order (channels 7,8,6,9,5,10,4,11)', () => {
    const expectedChannels = [7, 8, 6, 9, 5, 10, 4, 11]
    expect(rightColumn.map((vm) => vm.channel)).toEqual(expectedChannels)
  })

  it('all 16 original view-models are present across both columns (no duplicates, no missing)', () => {
    const all = [...leftColumn, ...rightColumn]
    const seen = new Set(all.map((vm) => vm.channel))
    expect(seen).toHaveLength(16)
    expect([...seen].sort((a, b) => a - b)).toEqual(Array.from({ length: 16 }, (_, i) => i))
  })
})
