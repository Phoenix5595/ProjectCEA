import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ControlSnapshotResponse } from '../../services/api/devices'

const mocks = vi.hoisted(() => ({
  getControlSnapshot: vi.fn(),
  getLightsForZone: vi.fn(async () => [{ device_name: 'veg_light', display_name: 'Veg Light' }]),
  commandDevice: vi.fn(),
}))

vi.mock('../../services/api', () => ({
  apiClient: {
    getControlSnapshot: mocks.getControlSnapshot,
    getLightsForZone: mocks.getLightsForZone,
    commandDevice: mocks.commandDevice,
  },
}))

import ManualLightControl from '../ManualLightControl'


describe('ManualLightControl', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('derives command state from the shared snapshot without polling it directly', async () => {
    const snapshot = {
      relays: [
        {
          assignment: {
            location: 'Veg Room',
            cluster: 'main',
            device_type: 'light',
          },
          command_expires_at: new Date(Date.now() + 30_000).toISOString(),
          command_mode: 'manual_on',
        },
      ],
    } as unknown as ControlSnapshotResponse

    render(
      <ManualLightControl
        location="Veg Room"
        cluster="main"
        snapshot={snapshot}
        compact
      />
    )

    expect(await screen.findByText('Timer: 1 min remaining')).toBeInTheDocument()
    expect(mocks.getControlSnapshot).not.toHaveBeenCalled()
  })
  it('derives the manual mode from the shared snapshot', async () => {
    const snapshot = {
      relays: [
        {
          assignment: {
            location: 'Veg Room',
            cluster: 'main',
            device_type: 'light',
          },
          command_expires_at: null,
          command_mode: 'manual_off',
        },
      ],
    } as unknown as ControlSnapshotResponse
    render(
      <ManualLightControl
        location="Veg Room"
        cluster="main"
        snapshot={snapshot}
        compact
      />
    )

    expect((await screen.findByRole('button', { name: 'Off' })).className).toContain('ring-2')
    expect(mocks.getControlSnapshot).not.toHaveBeenCalled()
  })

})
