import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ClimatePeriodsTable from '../../../components/ClimatePeriodsTable'
import type { ClimatePeriod } from '../../../types/climatePeriod'
import { sampleMetricSeries } from '../../../utils/climatePeriodTimeline'

const seedPeriod = (overrides?: Partial<ClimatePeriod>): ClimatePeriod => ({
  period_name: 'Seedling',
  start_time: '06:00',
  end_time: '18:00',
  ramp_minutes: 30,
  heating_setpoint: 22,
  cooling_setpoint: 24,
  vpd_setpoint: 1.2,
  co2_setpoint: 800,
  details: 'baseline',
  ...overrides,
})

function ControlledTable({
  initial = [seedPeriod()],
  validationErrors = [],
}: {
  initial?: ClimatePeriod[]
  validationErrors?: string[]
}) {
  const [periods, setPeriods] = useState<ClimatePeriod[]>(initial)
  return (
    <>
      <ClimatePeriodsTable
        periods={periods}
        onChange={setPeriods}
        validationErrors={validationErrors}
      />
      <pre data-testid="serialized-periods">{JSON.stringify(periods)}</pre>
    </>
  )
}

describe('ClimatePeriodsTable baseline', () => {
  afterEach(() => {
    vi.clearAllTimers()
  })

  it('renders every column and exposes HTML5 validation constraints (not component enforcement)', () => {
    render(<ControlledTable initial={[seedPeriod()]} />)

    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(
      expect.arrayContaining([
        'Period',
        'Start',
        'End',
        'Ramp',
        'Heat',
        'Cool',
        'VPD',
        'CO₂',
        'Details',
      ]),
    )

    const row = screen.getAllByRole('row')[1]
    const periodInput = within(row).getByPlaceholderText('Period name')
    expect(periodInput).toHaveValue('Seedling')

    const startInput = within(row).getAllByPlaceholderText('HH:MM')[0]
    expect(startInput).toHaveAttribute('pattern', '[0-9]{2}:[0-9]{2}')

    const rampInput = within(row).getByDisplayValue('30')
    expect(rampInput).toHaveAttribute('min', '0')
    expect(rampInput).toHaveAttribute('max', '240')

    const heatInput = within(row).getByDisplayValue('22')
    expect(heatInput).toHaveAttribute('min', '10')
    expect(heatInput).toHaveAttribute('max', '35')
    expect(heatInput).toHaveAttribute('step', '0.5')
  })

  it('propagates exact value edits through onChange', async () => {
    const user = userEvent.setup()
    render(<ControlledTable initial={[seedPeriod()]} />)

    const row = screen.getAllByRole('row')[1]
    const nameInput = within(row).getByPlaceholderText('Period name')
    const startInput = within(row).getAllByPlaceholderText('HH:MM')[0]
    const endInput = within(row).getAllByPlaceholderText('HH:MM')[1]
    const rampInput = within(row).getByDisplayValue('30')
    const heatInput = within(row).getByDisplayValue('22')

    await user.clear(nameInput)
    await user.type(nameInput, 'Veg stretch')

    await user.clear(startInput)
    await user.type(startInput, '05:30')

    await user.clear(endInput)
    await user.type(endInput, '23:00')

    fireEvent.change(rampInput, { target: { value: '45' } })
    fireEvent.change(heatInput, { target: { value: '23.5' } })

    const serialized = screen.getByTestId('serialized-periods')
    const periods: ClimatePeriod[] = JSON.parse(serialized.textContent!)
    expect(periods).toHaveLength(1)
    expect(periods[0]).toMatchObject({
      period_name: 'Veg stretch',
      start_time: '05:30',
      end_time: '23:00',
      ramp_minutes: 45,
      heating_setpoint: 23.5,
    })
  })

  it('enforces add/remove count boundaries', async () => {
    const user = userEvent.setup()
    render(<ControlledTable initial={[seedPeriod()]} />)

    const addButton = screen.getByRole('button', { name: /add period/i })
    for (let i = 0; i < 6; i++) {
      await user.click(addButton)
    }
    expect(screen.getAllByRole('row')).toHaveLength(8) // header + 7 rows

    await user.click(addButton)
    expect(screen.getAllByRole('row')).toHaveLength(8)

    let removeButtons = screen.getAllByTitle('Remove period')
    expect(removeButtons).toHaveLength(7)

    for (let i = 0; i < 6; i++) {
      await user.click(removeButtons[0])
      removeButtons = screen.getAllByTitle('Remove period')
    }
    expect(screen.getAllByRole('row')).toHaveLength(2)
    expect(screen.getByTitle('Remove period')).toBeDisabled()
  })

  it('propagates out-of-range numeric and malformed time values unchanged', async () => {
    const user = userEvent.setup()
    render(<ControlledTable initial={[seedPeriod()]} />)

    const row = screen.getAllByRole('row')[1]
    const heatInput = within(row).getByDisplayValue('22')
    const rampInput = within(row).getByDisplayValue('30')
    const startInput = within(row).getAllByPlaceholderText('HH:MM')[0]

    fireEvent.change(heatInput, { target: { value: '50' } })
    fireEvent.change(rampInput, { target: { value: '300' } })

    await user.clear(startInput)
    await user.type(startInput, 'ab:cd')

    const serialized = screen.getByTestId('serialized-periods')
    const periods: ClimatePeriod[] = JSON.parse(serialized.textContent!)
    expect(periods[0].heating_setpoint).toBe(50)
    expect(periods[0].ramp_minutes).toBe(300)
    expect(periods[0].start_time).toBe('ab:cd')
  })

  it('surfaces validationErrors supplied by the parent save boundary', () => {
    render(
      <ControlledTable
        initial={[seedPeriod()]}
        validationErrors={['Overlapping periods detected']}
      />,
    )
    expect(screen.getByText(/validation errors/i)).toBeInTheDocument()
    expect(screen.getByText(/overlapping periods detected/i)).toBeInTheDocument()
  })

  it('mounts independently and its output feeds timeline sampling correctly', () => {
    const initial = [
      seedPeriod({
        period_name: 'Day',
        start_time: '06:00',
        end_time: '18:00',
        heating_setpoint: 20,
        cooling_setpoint: null,
        vpd_setpoint: null,
        co2_setpoint: null,
      }),
      seedPeriod({
        period_name: 'Night',
        start_time: '18:00',
        end_time: '06:00',
        heating_setpoint: 18,
        cooling_setpoint: null,
        vpd_setpoint: null,
        co2_setpoint: null,
      }),
    ]
    render(<ControlledTable initial={initial} />)

    // Independence: no timeline-specific markup should be present.
    expect(screen.queryByTestId('timeline-night-band')).not.toBeInTheDocument()
    expect(screen.queryByTestId('timeline-handle-night-start')).not.toBeInTheDocument()

    const serialized = screen.getByTestId('serialized-periods')
    const periods: ClimatePeriod[] = JSON.parse(serialized.textContent!)
    const series = sampleMetricSeries(periods, 'heating')
    expect(series[12 * 60]).toBe(20)
    expect(series[18 * 60 + 30]).toBe(18)
    expect(series[0]).toBe(18)

    // Edit the table and verify the sampled trajectory updates.
    const row = screen.getAllByRole('row')[1]
    const heatInput = within(row).getByDisplayValue('20')
    fireEvent.change(heatInput, { target: { value: '25' } })

    const updated: ClimatePeriod[] = JSON.parse(serialized.textContent!)
    expect(updated[0].heating_setpoint).toBe(25)
    expect(screen.queryByTestId('timeline-night-band')).not.toBeInTheDocument()
  })
})
