import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

import RelayChannelMatrix from '../RelayChannelMatrix'
import { RELAY_TO_CHANNEL, buildRelayChannelViewModels } from '../relayViewModel'
import type { RelayChannelViewModel } from '../relayViewModel'
import type { ControlSnapshotResponse } from '../../../services/api/devices'

/** Physical R label for one MCP channel, so every tile testid stays unique. */
const PHYSICAL_RELAY_BY_CHANNEL: Record<number, number> = Object.fromEntries(
  Object.entries(RELAY_TO_CHANNEL).map(([relay, channel]) => [channel, Number(relay)] as const)
)

const ALL_CHANNELS = Array.from({ length: 16 }, (_, index) => index)

function tile(channel: number): string {
  return `relay-tile-${PHYSICAL_RELAY_BY_CHANNEL[channel]}`
}

function channelVm(
  channel: number,
  overrides: Partial<RelayChannelViewModel> = {}
): RelayChannelViewModel {
  return {
    channel,
    physicalRelay: PHYSICAL_RELAY_BY_CHANNEL[channel],
    pinLabel: '',
    isStateKnown: true,
    observedState: false,
    isActive: false,
    isAssigned: false,
    deviceId: null,
    assignedDeviceName: null,
    deviceName: null,
    displayType: null,
    location: null,
    cluster: null,
    changedAt: null,
    commandMode: null,
    commandExpiresAt: null,
    syncing: false,
    stale: false,
    alarm: null,
    lastCommandSucceeded: null,
    recoveryPending: false,
    ...overrides,
  }
}

function assigned(channel: number, deviceId: number, deviceName: string): RelayChannelViewModel {
  return channelVm(channel, {
    isAssigned: true,
    deviceId,
    assignedDeviceName: deviceName,
    deviceName,
    displayType: 'Exhaust',
    location: 'Veg Room',
    cluster: 'main',
  })
}

/** All 16 real tiles, with the supplied channels replaced by their fixtures. */
function matrixChannels(
  overrides: Record<number, RelayChannelViewModel> = {}
): RelayChannelViewModel[] {
  return ALL_CHANNELS.map(channel => overrides[channel] ?? channelVm(channel))
}

describe('RelayChannelMatrix drag to move', () => {
  it('labels an empty target and moves the source binding there', () => {
    const onMoveRelay = vi.fn()
    render(
      <RelayChannelMatrix
        channels={matrixChannels({ 0: assigned(0, 3, 'heating_v_1') })}
        nowMs={0}
        onMoveRelay={onMoveRelay}
      />
    )

    const source = screen.getByTestId(tile(0))
    const target = screen.getByTestId(tile(5))
    const transfer = { setData: vi.fn(), effectAllowed: 'none', dropEffect: 'none' }
    expect(source).toHaveAttribute('draggable', 'true')

    fireEvent.dragStart(source, { dataTransfer: transfer })
    fireEvent.dragEnter(target)
    fireEvent.dragOver(target, { dataTransfer: transfer })

    expect(within(target).getByTestId('relay-drop-label')).toHaveTextContent('Move here')

    fireEvent.drop(target)
    fireEvent.dragEnd(source)

    expect(onMoveRelay).toHaveBeenCalledWith(3, 0, 5)
    expect(screen.queryByTestId('relay-drop-label')).not.toBeInTheDocument()
  })

  it('labels an occupied target as a swap with its device name', () => {
    render(
      <RelayChannelMatrix
        channels={matrixChannels({
          0: assigned(0, 3, 'heating_v_1'),
          5: assigned(5, 2, 'Chilled Back'),
        })}
        nowMs={0}
        onMoveRelay={vi.fn()}
      />
    )

    const source = screen.getByTestId(tile(0))
    const target = screen.getByTestId(tile(5))
    const transfer = { setData: vi.fn(), effectAllowed: 'none', dropEffect: 'none' }
    fireEvent.dragStart(source, { dataTransfer: transfer })
    fireEvent.dragOver(target, { dataTransfer: transfer })

    expect(within(target).getByTestId('relay-drop-label')).toHaveTextContent(
      'Swap with Chilled Back'
    )
    expect(within(source).queryByTestId('relay-drop-label')).not.toBeInTheDocument()
  })

  it('marks moving tiles busy and stops accepting drags until the move settles', () => {
    render(
      <RelayChannelMatrix
        channels={matrixChannels({
          0: assigned(0, 3, 'heating_v_1'),
          5: assigned(5, 2, 'Chilled Back'),
        })}
        nowMs={0}
        onMoveRelay={vi.fn()}
        movingChannels={new Set([0, 5])}
      />
    )

    const source = screen.getByTestId(tile(0))
    expect(source).toHaveAttribute('aria-busy', 'true')
    expect(source).toHaveAttribute('draggable', 'false')
    expect(within(source).getByText('Moving…')).toBeInTheDocument()
  })

  it('never enables drags without a move handler or outside the panel variant', () => {
    const { unmount } = render(
      <RelayChannelMatrix
        channels={matrixChannels({ 0: assigned(0, 3, 'heating_v_1') })}
        nowMs={0}
      />
    )
    expect(screen.getByTestId(tile(0))).toHaveAttribute('draggable', 'false')
    unmount()

    render(
      <RelayChannelMatrix
        channels={matrixChannels({ 0: assigned(0, 3, 'heating_v_1') })}
        nowMs={0}
        variant="compact"
        onMoveRelay={vi.fn()}
      />
    )
    expect(screen.getByTestId(tile(0))).toHaveAttribute('draggable', 'false')
  })
})

describe('relay view model device identity', () => {
  it('carries the assignment device_id for drag targets and null otherwise', () => {
    const snapshot = {
      relays: [
        {
          channel: 0,
          physical_relay: 2,
          pin_label: '',
          observed_state: false,
          assignment: { device_id: 3, device_name: 'heating_v_1', device_type: 'heating' },
        },
      ],
    } as unknown as ControlSnapshotResponse

    const channels = buildRelayChannelViewModels(snapshot)
    expect(channels[0].deviceId).toBe(3)
    expect(channels[5].deviceId).toBeNull()
  })
})
