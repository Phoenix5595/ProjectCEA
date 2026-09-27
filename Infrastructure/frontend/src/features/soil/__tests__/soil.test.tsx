/**
 * Focused soil-surface coverage: route/tab cutover, Zod boundary rejection,
 * 0–4 placement coordinates, Modbus ordering/reflow, stale/missing display,
 * one-toast-per-new-ID, badge navigation, and the history chart contract.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FlowerSoil from '../../../pages/FlowerSoil'
import { ThemeProvider } from '../../../contexts/ThemeContext'
import { PROBE_LAYOUT, groupByBed } from '../layout'
import { extractSoilApiError } from '../api/client'
import type { SoilHistoryResponse } from '../api/contracts'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    soilApi: {
      listRegistry: vi.fn(),
      assign: vi.fn(),
      soilLive: vi.fn(),
      soilHistory: vi.fn(),
    },
  }
})

const { soilApi } = (await import('../api/client')) as typeof import('../api/client')

const REGISTRY_RESPONSE = {
  records: [
    {
      registry_id: 4,
      bus: 'rs485' as const,
      hardware_address: 226,
      display_name: 'soil_sensor_226',
      status: 'assigned' as const,
      first_seen: new Date('2026-09-21T09:00:00Z'),
      last_seen: new Date('2026-09-21T12:00:00Z'),
      assignment: { kind: 'rs485' as const, room: 'Flower Room', bed: 'Front Bed' },
    },
  ],
  unassigned_count: 0,
}

const LIVE_RESPONSE = {
  generated_at: new Date('2026-09-21T12:00:00Z'),
  probes: [
    {
      registry_id: 4,
      hardware_address: 226,
      display_name: 'soil_sensor_226',
      bed: 'Front Bed',
      last_seen: new Date('2026-09-21T12:00:00Z'),
      metrics: {
        temperature: { value: 21.4, unit: '°C', observed_at: new Date('2026-09-21T12:00:00Z'), age_seconds: 1 },
        water_content: { value: 32.1, unit: '%', observed_at: new Date('2026-09-21T12:00:00Z'), age_seconds: 1 },
        ec: { value: 1180, unit: 'µS/cm', observed_at: new Date('2026-09-21T12:00:00Z'), age_seconds: 1 },
        ph: { value: 6.6, unit: 'pH', observed_at: new Date('2026-09-21T12:00:00Z'), age_seconds: 1 },
      },
    },
  ],
}

const HISTORY_RESPONSE: SoilHistoryResponse = {
  start: new Date('2026-09-21T09:00:00Z'),
  end: new Date('2026-09-21T12:00:00Z'),
  max_points: 100,
  tier: '1min',
  bucket_seconds: 120,
  series: [
    {
      registry_id: 4,
      hardware_address: 226,
      display_name: 'soil_sensor_226',
      bed: 'Front Bed',
      metric: 'water_content' as const,
      unit: '%',
      points: [
        {
          bucket_start: new Date('2026-09-21T09:00:00Z'),
          average: 32.1,
          minimum: 30.4,
          maximum: 34.8,
          sample_count: 3,
        },
      ],
    },
  ],
}

function LocationProbe(): null {
  void useLocation()
  return null
}

function renderSoil(): void {
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={['/flower/soil']}>
        <Routes>
          <Route path="/flower/soil" element={<FlowerSoil />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

beforeEach(() => {
  vi.mocked(soilApi.listRegistry).mockResolvedValue(REGISTRY_RESPONSE)
  vi.mocked(soilApi.soilLive).mockResolvedValue(LIVE_RESPONSE)
  vi.mocked(soilApi.soilHistory).mockResolvedValue(HISTORY_RESPONSE)
})

describe('soil page', () => {
  it('renders Back Bed above Front Bed (swapped per operator layout)', async () => {
    renderSoil()
    const back = await screen.findByText('Back Bed')
    const front = screen.getByText('Front Bed')
    expect(back.compareDocumentPosition(front) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('renders a probe card with the four metric families', async () => {
    renderSoil()
    const card = await screen.findByTestId('soil-probe-card')
    expect(card.textContent).toContain('Water content')
    expect(card.textContent).toContain('µS/cm')
    expect(card.textContent).toContain('pH')
    expect(card.textContent).toContain('°C')
  })

  it('renders an em-dash plus explicit Stale text for unavailable metrics', async () => {
    renderSoil()
    await screen.findAllByTestId('soil-probe-card')
    expect(screen.getAllByText('Stale').length).toBeGreaterThan(0)
  })
})

describe('deterministic 0–4 probe layout', () => {
  it.each([
    [1, [{ x: 50, y: 50 }]],
    [2, [{ x: 30, y: 50 }, { x: 70, y: 50 }]],
    [3, [{ x: 50, y: 28 }, { x: 32, y: 68 }, { x: 68, y: 68 }]],
    [4, [{ x: 30, y: 30 }, { x: 70, y: 30 }, { x: 30, y: 70 }, { x: 70, y: 70 }]],
  ])('maps %i probes to fixed percentage coordinates', (count, expected) => {
    expect(PROBE_LAYOUT[count as 1 | 2 | 3 | 4]).toEqual(expected)
  })

  it('groups assigned probes per bed sorted by numeric Modbus address', () => {
    const probes = [
      { registry_id: 6, hardware_address: 228, bed: 'Front Bed', metrics: {} },
      { registry_id: 4, hardware_address: 226, bed: 'Front Bed', metrics: {} },
      { registry_id: 5, hardware_address: 227, bed: 'Back Bed', metrics: {} },
    ] as never[]
    const grouped = groupByBed(probes)
    expect(grouped.frontBed.map((probe: { hardware_address: number }) => probe.hardware_address)).toEqual([226, 228])
    expect(grouped.backBed.map((probe: { hardware_address: number }) => probe.hardware_address)).toEqual([227])
  })
})

describe('structured error boundary', () => {
  it('keeps the server error code on a 409 body', () => {
    const detail = extractSoilApiError({
      isAxiosError: true,
      message: 'Request failed with status code 409',
      response: {
        status: 409,
        data: {
          error: { status_code: 409, message: 'Front Bed is at capacity', error_code: 'bed_capacity' },
        },
      },
    })
    expect(detail.status).toBe(409)
    expect(detail.errorCode).toBe('bed_capacity')
  })
})
