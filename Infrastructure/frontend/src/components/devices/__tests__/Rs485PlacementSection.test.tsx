import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const soilMocks = vi.hoisted(() => ({
  listRegistry: vi.fn(),
  assign: vi.fn(),
  unassign: vi.fn(),
  beds: vi.fn(),
  busStatus: vi.fn(),
}))

vi.mock('../../../features/soil', async importOriginal => {
  const actual = await importOriginal<typeof import('../../../features/soil')>()
  return {
    ...actual,
    soilApi: { ...actual.soilApi, ...soilMocks },
  }
})

import Rs485PlacementSection from '../Rs485PlacementSection'

function probe(
  registryId: number,
  address: number,
  assignment: {
    room: string
    bed: string | null
    x_frac: number | null
    y_frac: number | null
  } | null
) {
  return {
    registry_id: registryId,
    bus: 'rs485' as const,
    hardware_address: address,
    sensor_uid: `00000000-0000-4000-8000-0000000000${registryId}`,
    identity_status: 'confirmed' as const,
    pending_candidate_registry_id: null,
    pending_since: null,
    display_name: `soil_sensor_${address}`,
    status: assignment === null ? ('unassigned' as const) : ('assigned' as const),
    first_seen: new Date('2026-10-20T12:00:00Z'),
    last_seen: new Date('2026-10-26T12:00:00Z'),
    assignment: assignment === null ? null : { kind: 'rs485' as const, ...assignment },
  }
}

const RECORDS = [
  probe(12, 2, null),
  probe(13, 3, null),
  probe(14, 4, { room: 'Flower Room', bed: 'Back Bed', x_frac: 0.3, y_frac: 0.4 }),
  probe(15, 5, { room: 'Veg Room', bed: null, x_frac: null, y_frac: null }),
]

beforeEach(() => {
  vi.clearAllMocks()
  soilMocks.listRegistry.mockResolvedValue({ records: RECORDS, unassigned_count: 2 })
  soilMocks.beds.mockResolvedValue({
    beds: [
      { rack_id: 1, name: 'Back Bed', width_ft: 4, length_ft: 4 },
      { rack_id: 5, name: 'Front Bed', width_ft: 4, length_ft: 4 },
    ],
  })
  soilMocks.busStatus.mockResolvedValue({ state: 'ok', address: null, at: null })
  soilMocks.assign.mockResolvedValue(RECORDS[0])
  soilMocks.unassign.mockResolvedValue(RECORDS[2])
})

function row(name: string): HTMLElement {
  const table = screen.getByRole('table', { name: 'RS485 probes' })
  const cell = within(table).getByText(name)
  return cell.closest('tr') as HTMLElement
}

describe('Rs485PlacementSection', () => {
  it('renders each probe in its zone, placed tags at their bed position', async () => {
    render(<Rs485PlacementSection />)

    const unassigned = await screen.findByTestId('soil-unassigned')
    expect(within(unassigned).getByTestId('soil-tag-2')).toBeInTheDocument()
    expect(within(unassigned).getByTestId('soil-tag-3')).toBeInTheDocument()
    const back = screen.getByTestId('soil-bed-back')
    const placed = within(back).getByTestId('soil-tag-4')
    expect(placed).toHaveStyle({ left: '30%', top: '40%' })
    expect(within(screen.getByTestId('soil-veg')).getByTestId('soil-tag-5')).toBeInTheDocument()
    expect(within(screen.getByTestId('soil-bed-front')).queryByText(/Soil/)).toBeNull()
    expect(screen.getByText('Back Bed · 4 × 4 ft')).toBeInTheDocument()
    expect(screen.queryByTestId('soil-bus-status')).toBeNull()
  })

  it('places with the row buttons and unassigns placed probes', async () => {
    render(<Rs485PlacementSection />)
    await screen.findByTestId('soil-tag-3')

    fireEvent.click(within(row('Soil 3')).getByRole('button', { name: 'Veg Room' }))
    expect(soilMocks.assign).toHaveBeenCalledWith(13, { kind: 'rs485', room: 'Veg Room' })

    await screen.findByTestId('soil-tag-2')
    fireEvent.click(within(row('Soil 2')).getByRole('button', { name: 'Back Bed' }))
    expect(soilMocks.assign).toHaveBeenLastCalledWith(12, {
      kind: 'rs485',
      room: 'Flower Room',
      bed: 'Back Bed',
      x_frac: 0.5,
      y_frac: 0.5,
    })

    await screen.findByTestId('soil-tag-4')
    const placedRow = row('Soil 4')
    expect(within(placedRow).getByRole('button', { name: 'Back Bed' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    fireEvent.click(within(placedRow).getByRole('button', { name: 'Unassigned' }))
    expect(soilMocks.unassign).toHaveBeenCalledWith(14)
  })

  it('shows the power-cycle banner with the reserved address', async () => {
    soilMocks.busStatus.mockResolvedValue({
      state: 'power_cycle_required',
      address: 3,
      at: new Date('2026-10-26T12:00:00Z'),
    })

    render(<Rs485PlacementSection />)

    expect(await screen.findByTestId('soil-bus-status')).toHaveTextContent(
      'Power-cycle the new probe to finish address 3'
    )
  })
})
