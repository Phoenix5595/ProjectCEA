import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../services/api'
import FlowerGrowWizard from '../FlowerGrowWizard'

const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))

vi.mock('../../../services/api', () => ({
  apiClient: { createFlowerGrowPlan: vi.fn() },
}))
vi.mock('sonner', () => ({ toast }))

function renderWizard() {
  const onClose = vi.fn()
  const onCreated = vi.fn()
  render(<FlowerGrowWizard open onClose={onClose} onCreated={onCreated} />)
  return { onClose, onCreated }
}

function setAnchorDate(value: string) {
  fireEvent.change(screen.getByLabelText('Anchor date'), { target: { value } })
}

describe('FlowerGrowWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.createFlowerGrowPlan).mockResolvedValue({
      grow_plan_id: 'gp-1',
      crop_batch_id: 1,
      events: [],
    })
  })

  it('previews every phase from the start-of-flower anchor', () => {
    renderWizard()

    expect(screen.getByLabelText('Start of flower')).toBeChecked()
    setAnchorDate('2026-11-02')

    expect(screen.getByText('2026-10-19 → 2026-11-01: Bed veg (Flower Room)')).toBeInTheDocument()
    expect(screen.getByText('2026-11-02 → 2026-11-22: Stretch (Flower Room)')).toBeInTheDocument()
    expect(screen.getByText('2026-11-23 → 2026-12-13: Bulk (Flower Room)')).toBeInTheDocument()
    expect(screen.getByText('2026-12-14 → 2027-01-03: Ripen (Flower Room)')).toBeInTheDocument()
    expect(screen.getByText('2027-01-03 → 2027-01-03: Harvest (Flower Room)')).toBeInTheDocument()
    expect(screen.getByText('2027-01-04 → 2027-01-10: Drying (Flower Room)')).toBeInTheDocument()
    expect(screen.getByLabelText('Bed veg start')).toHaveValue('2026-10-19')
    expect(screen.getByLabelText('Automatic submode changes')).toBeChecked()
  })

  it('mirrors the anchor date into a read-only bed veg start for the veg-bed anchor', () => {
    renderWizard()

    fireEvent.click(screen.getByLabelText('Start of veg beds'))
    setAnchorDate('2026-10-19')

    const bedVegStart = screen.getByLabelText('Bed veg start')
    expect(bedVegStart).toHaveValue('2026-10-19')
    expect(bedVegStart).toHaveAttribute('readonly')
  })

  it('submits the exact anchored grow plan payload', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('crypto', { randomUUID: () => 'uuid-1' })
    const { onClose, onCreated } = renderWizard()

    setAnchorDate('2026-11-02')
    await user.click(screen.getByRole('button', { name: 'Create plan' }))

    await waitFor(() => expect(apiClient.createFlowerGrowPlan).toHaveBeenCalledOnce())
    expect(apiClient.createFlowerGrowPlan).toHaveBeenCalledWith({
      idempotency_key: 'uuid-1',
      crop_name: 'Flower cycle',
      environment: 'indoor',
      anchor: 'flower_start',
      anchor_date: '2026-11-02',
      bed_veg_start: '2026-10-19',
      bed_veg_days: 14,
      stretch_days: 21,
      bulk_days: 21,
      ripen_days: 21,
      drying_days: 7,
      pot_start: null,
      clone_start: null,
      auto_mode_transition: true,
    })
    expect(onCreated).toHaveBeenCalledOnce()
    expect(onClose).toHaveBeenCalledOnce()
    vi.unstubAllGlobals()
  })

  it('prefills the optional pot window one fortnight before the veg bed', async () => {
    const user = userEvent.setup()
    renderWizard()

    setAnchorDate('2026-11-02')
    await user.click(screen.getByLabelText('Veg pot'))

    expect(screen.getByLabelText('Pot start')).toHaveValue('2026-10-05')
    expect(screen.getByText('2026-10-05 → 2026-10-18: Pot veg (Veg Room)')).toBeInTheDocument()
  })

  it('requires the date of a checked optional phase', async () => {
    const user = userEvent.setup()
    renderWizard()

    setAnchorDate('2026-11-02')
    await user.click(screen.getByLabelText('Veg pot'))
    fireEvent.change(screen.getByLabelText('Pot start'), { target: { value: '' } })

    expect(screen.getByText('Pot start is required')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create plan' })).toBeDisabled()
  })
})
