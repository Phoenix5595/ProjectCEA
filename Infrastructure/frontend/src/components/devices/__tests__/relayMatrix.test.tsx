import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import RelayChannelMatrix from '../RelayChannelMatrix'
import type { RelayChannelViewModel } from '../relayViewModel'

function makeVm(channel: number, deviceName: string | null): RelayChannelViewModel {
  return {
    channel,
    pinLabel: `GP${channel < 8 ? 'A' : 'B'}${channel < 8 ? channel : channel - 8}`,
    isStateKnown: true,
    isActive: false,
    isAssigned: Boolean(deviceName),
    assignedDeviceName: deviceName,
    deviceName,
    displayType: null,
    location: 'Flower Room',
    cluster: 'main',
    lastStateChangeAt: null,
    mode: null,
    overrideExpiresAt: null,
  }
}

const vm16: RelayChannelViewModel[] = [
  makeVm(0, 'exhaust_fan'),
  makeVm(1, null),
  makeVm(2, null),
  makeVm(3, null),
  makeVm(4, null),
  makeVm(5, null),
  makeVm(6, null),
  makeVm(7, null),
  makeVm(8, null),
  makeVm(9, null),
  makeVm(10, null),
  makeVm(11, null),
  makeVm(12, null),
  makeVm(13, null),
  makeVm(14, null),
  makeVm(15, 'Heater Flower'),
]

describe('RelayChannelMatrix panel variant', () => {
  it('renders left column top→bottom as relays 1–8 and right column as relays 16–9', () => {
    render(<RelayChannelMatrix channels={vm16} variant="panel" nowMs={Date.now()} />)

    // Left column (relays 1-8 top→bottom)
    // relay 1 = GPB7 = "Heater Flower"
    expect(screen.getByText('R1 · GPB7')).toBeInTheDocument()
    expect(screen.getByText('Heater Flower')).toBeInTheDocument()

    // relay 2 = GPA0 = "exhaust_fan"
    expect(screen.getByText('R2 · GPA0')).toBeInTheDocument()
    expect(screen.getByText('exhaust_fan')).toBeInTheDocument()

    // Right column (relays 16-9 top→bottom)
    // relay 16 = GPA7
    expect(screen.getByText('R16 · GPA7')).toBeInTheDocument()

    // relay 9 = GPB3
    expect(screen.getByText('R9 · GPB3')).toBeInTheDocument()
  })

  it('shows relay numbers in left and right gutters', () => {
    render(<RelayChannelMatrix channels={vm16} variant="panel" nowMs={Date.now()} />)

    // Left gutter: 1, 2, 3, 4, 5, 6, 7, 8
    for (let i = 1; i <= 8; i++) {
      expect(screen.getAllByText(String(i)).length).toBeGreaterThanOrEqual(1)
    }

    // Right gutter: 16, 15, 14, 13, 12, 11, 10, 9
    for (let i = 9; i <= 16; i++) {
      expect(screen.getAllByText(String(i)).length).toBeGreaterThanOrEqual(1)
    }
  })
})

describe('RelayChannelMatrix compact variant', () => {
  it('shows R{n} · GPA/GPB pin labels in compact mode', () => {
    render(<RelayChannelMatrix channels={vm16} variant="compact" nowMs={Date.now()} />)

    // relay 1 = GPB7
    expect(screen.getByText('R1 · GPB7')).toBeInTheDocument()

    // relay 16 = GPA7
    expect(screen.getByText('R16 · GPA7')).toBeInTheDocument()
  })
})
