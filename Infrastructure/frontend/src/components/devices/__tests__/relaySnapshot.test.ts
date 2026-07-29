import { describe, it, expect } from 'vitest'
import type { RelayBoardStateResponse } from '../../../types/relay'

describe('RelayBoardSnapshot structure', () => {
  it('has 16 channels', () => {
    const snapshot: RelayBoardStateResponse = {
      channels: Array(16).fill(false),
      timestamps: Array(16).fill(null),
      mcp_connected: true,
      simulation: false,
      modes: Array(16).fill(null),
      override_expires_at: Array(16).fill(null),
    }
    expect(snapshot.channels.length).toBe(16)
    expect(snapshot.timestamps.length).toBe(16)
    expect(snapshot.modes.length).toBe(16)
    expect(snapshot.override_expires_at.length).toBe(16)
  })

  it('tracks MCP connection state', () => {
    const snapshot: RelayBoardStateResponse = {
      channels: Array(16).fill(false),
      timestamps: Array(16).fill(null),
      mcp_connected: false,
      simulation: false,
      modes: Array(16).fill(null),
      override_expires_at: Array(16).fill(null),
    }
    expect(snapshot.mcp_connected).toBe(false)
  })

  it('tracks simulation mode', () => {
    const snapshot: RelayBoardStateResponse = {
      channels: Array(16).fill(false),
      timestamps: Array(16).fill(null),
      mcp_connected: true,
      simulation: true,
      modes: Array(16).fill(null),
      override_expires_at: Array(16).fill(null),
    }
    expect(snapshot.simulation).toBe(true)
  })
})
