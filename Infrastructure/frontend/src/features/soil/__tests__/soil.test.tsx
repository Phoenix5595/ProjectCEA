/**
 * Focused soil-surface coverage: bed order, free marker positions, closed
 * markers, stale/missing display, badge navigation, pointer-to-bed
 * fractions, and the history chart contract.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ThemeProvider } from '../../../contexts/ThemeContext'
import FlowerSoil from '../../../pages/FlowerSoil'
import { extractSoilApiError } from '../api/client'
import type { SoilHistoryResponse } from '../api/contracts'
import { fracFromPointer } from '../geometry'
import { adaptSoilHistory } from '../history'

vi.mock('../api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    soilApi: {
      listRegistry: vi.fn(),
      assign: vi.fn(),
      soilLive: vi.fn(),
      soilHistory: vi.fn(),
      heatmap: vi.fn(),
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
      sensor_uid: '00000000-0000-4000-8000-000000000004',
      identity_status: 'confirmed' as const,
      pending_candidate_registry_id: null,
      pending_since: null,
      display_name: 'soil_sensor_226',
      status: 'assigned' as const,
      first_seen: new Date('2026-09-21T09:00:00Z'),
      last_seen: new Date('2026-09-21T12:00:00Z'),
      assignment: {
        kind: 'rs485' as const,
        room: 'Flower Room',
        bed: 'Front Bed',
        x_frac: 0.25,
        y_frac: 0.75,
      },
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
        temperature: {
          value: 21.4,
          unit: '°C',
          observed_at: new Date('2026-09-21T12:00:00Z'),
          age_seconds: 1,
        },
        water_content: {
          value: 32.1,
          unit: '%',
          observed_at: new Date('2026-09-21T12:00:00Z'),
          age_seconds: 1,
        },
        ec: {
          value: 1180,
          unit: 'µS/cm',
          observed_at: new Date('2026-09-21T12:00:00Z'),
          age_seconds: 1,
        },
        ph: {
          value: 6.6,
          unit: 'pH',
          observed_at: new Date('2026-09-21T12:00:00Z'),
          age_seconds: 1,
        },
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
    </ThemeProvider>
  )
}

beforeEach(() => {
  // jsdom has no 2-D canvas; the bed figure skips painting on a null context.
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  vi.mocked(soilApi.listRegistry).mockResolvedValue(REGISTRY_RESPONSE)
  vi.mocked(soilApi.soilLive).mockResolvedValue(LIVE_RESPONSE)
  vi.mocked(soilApi.soilHistory).mockResolvedValue(HISTORY_RESPONSE)
  vi.mocked(soilApi.heatmap).mockResolvedValue({
    metric: 'water_content',
    stale_after_hours: 72,
    beds: [
      {
        rack_id: 5,
        name: 'Front Bed',
        width_ft: 4,
        length_ft: 4,
        points: [
          {
            registry_id: 6,
            hardware_address: 6,
            x_frac: 0.7,
            y_frac: 0.7,
            value: 41,
            active: false,
            removed_at: new Date(Date.now() - 36 * 3600_000),
          },
        ],
      },
    ],
  })
})

describe('soil page', () => {
  it('renders Back Bed above Front Bed (swapped per operator layout)', async () => {
    renderSoil()
    const back = await screen.findByText('Back Bed')
    const front = screen.getByText('Front Bed')
    expect(back.compareDocumentPosition(front) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('places active markers at the stored bed fractions', async () => {
    renderSoil()
    const marker = await screen.findByTestId('soil-marker-226')
    expect(marker).toHaveStyle({ left: '25%', top: '75%' })
    expect(marker).toHaveTextContent('#226 32.1 %')
  })

  it('shows recently closed placements as hollow markers with their age', async () => {
    renderSoil()
    expect(await screen.findByTestId('soil-marker-closed-6')).toHaveTextContent('#6 · 36 h')
  })

  it('renders a probe card with the four metric families', async () => {
    renderSoil()
    const card = await screen.findByTestId('soil-probe-card')
    expect(card.textContent).toContain('Water content')
    expect(card.textContent).toContain('µS/cm')
    expect(card.textContent).toContain('pH')
    expect(card.textContent).toContain('°C')
  })

  it('shows missing and stale freshness for the corresponding metric', async () => {
    const probe = LIVE_RESPONSE.probes[0]
    if (probe === undefined) throw new Error('soil fixture probe missing')
    const now = new Date()
    const stale = new Date(now.getTime() - 60_000)
    vi.mocked(soilApi.soilLive).mockResolvedValue({
      ...LIVE_RESPONSE,
      probes: [
        {
          ...probe,
          metrics: {
            ...probe.metrics,
            water_content: null,
            ec: { ...probe.metrics.ec, observed_at: stale },
            ph: { ...probe.metrics.ph, observed_at: now },
            temperature: { ...probe.metrics.temperature, observed_at: now },
          },
        },
      ],
    })

    renderSoil()
    const card = await screen.findByTestId('soil-probe-card')
    const metricLabels = within(card)
      .getAllByRole('term')
      .map(term => (term.textContent ?? '').replace(/\s+/g, ' ').trim())
    expect(metricLabels).toEqual(['Water content No reading', 'EC Stale', 'pH', 'Temperature'])
    expect(within(card).getByText('—')).toBeInTheDocument()
  })

  it('shows a persistent settings badge for pending sensor identities', async () => {
    const record = REGISTRY_RESPONSE.records[0]
    if (record === undefined) throw new Error('soil registry fixture record missing')
    vi.mocked(soilApi.listRegistry).mockResolvedValue({
      ...REGISTRY_RESPONSE,
      records: [
        {
          ...record,
          status: 'unassigned',
          assignment: null,
          identity_status: 'pending',
          pending_candidate_registry_id: null,
          pending_since: new Date('2026-09-21T12:00:00Z'),
        },
      ],
    })

    renderSoil()
    expect(
      await screen.findByRole('button', { name: /sensor identity needs confirmation/i })
    ).toBeInTheDocument()
  })
})

describe('soil history labels', () => {
  it('uses the operator-facing metric label rather than the wire enum', () => {
    const series = adaptSoilHistory(HISTORY_RESPONSE).series
    expect(series[0]?.label).toBe('Front Bed #226 Water content (%)')
  })
})

describe('bed pointer fractions', () => {
  it('clamps to the bed and rounds to three decimals', () => {
    const rect = { left: 100, top: 50, width: 200, height: 400 } as DOMRect
    expect(fracFromPointer(rect, 150, 350)).toEqual({ x_frac: 0.25, y_frac: 0.75 })
    expect(fracFromPointer(rect, 133.3333, 50)).toEqual({ x_frac: 0.167, y_frac: 0 })
    expect(fracFromPointer(rect, 40, 900)).toEqual({ x_frac: 0, y_frac: 1 })
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
          error: {
            status_code: 409,
            message: 'Registry record 7 is a CAN node',
            error_code: 'not_rs485',
          },
        },
      },
    })
    expect(detail.status).toBe(409)
    expect(detail.errorCode).toBe('not_rs485')
  })
})
