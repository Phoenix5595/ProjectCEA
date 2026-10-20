import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const soilMocks = vi.hoisted(() => ({
  listRegistry: vi.fn(),
  assign: vi.fn(),
  resolveIdentity: vi.fn(),
}))

vi.mock('../../../features/soil', async importOriginal => {
  const actual = await importOriginal<typeof import('../../../features/soil')>()
  return {
    ...actual,
    soilApi: {
      ...actual.soilApi,
      listRegistry: soilMocks.listRegistry,
      assign: soilMocks.assign,
      resolveIdentity: soilMocks.resolveIdentity,
    },
  }
})

import SensorSettingsPanel from '../SensorSettingsPanel'

const PENDING_RECORD = {
  registry_id: 2,
  bus: 'rs485' as const,
  hardware_address: 233,
  sensor_uid: '00000000-0000-4000-8000-000000000002',
  identity_status: 'pending' as const,
  pending_candidate_registry_id: 1,
  pending_since: new Date('2026-10-19T12:00:00Z'),
  display_name: 'soil_sensor_233',
  status: 'unassigned' as const,
  first_seen: new Date('2026-10-19T12:00:00Z'),
  last_seen: new Date('2026-10-19T12:00:00Z'),
  assignment: null,
}

const ELIGIBLE_RECORD = {
  registry_id: 1,
  bus: 'rs485' as const,
  hardware_address: 226,
  sensor_uid: '00000000-0000-4000-8000-000000000001',
  identity_status: 'confirmed' as const,
  pending_candidate_registry_id: null,
  pending_since: null,
  display_name: 'soil_sensor_226',
  status: 'assigned' as const,
  first_seen: new Date('2026-10-01T12:00:00Z'),
  last_seen: new Date(Date.now() - 11 * 60 * 1000),
  assignment: { kind: 'rs485' as const, room: 'Flower Room', bed: 'Front Bed' },
}

beforeEach(() => {
  vi.clearAllMocks()
  soilMocks.listRegistry.mockResolvedValue({
    records: [PENDING_RECORD, ELIGIBLE_RECORD],
    unassigned_count: 1,
  })
  soilMocks.resolveIdentity.mockResolvedValue(ELIGIBLE_RECORD)
})

describe('SensorSettingsPanel identity confirmation', () => {
  it('preselects the stale candidate and offers same/new decisions', async () => {
    render(<SensorSettingsPanel />)

    const target = await screen.findByRole('combobox', {
      name: 'Previous identity for soil_sensor_233',
    })
    expect(target).toHaveValue('1')
    expect(screen.getByRole('button', { name: 'Same sensor as soil_sensor_226' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New sensor' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Same sensor as soil_sensor_226' }))

    expect(soilMocks.resolveIdentity).toHaveBeenCalledWith(2, {
      decision: 'same',
      target_registry_id: 1,
    })
  })
})
