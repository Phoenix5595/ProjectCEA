import { fireEvent, render } from '@testing-library/react'
import type { ClimatePeriod } from '../../types/climatePeriod'
import { describe, expect, it } from 'vitest'
import { useState } from 'react'
import ClimatePeriodsTable from '../ClimatePeriodsTable'
import { normalizeTypedTimeText } from '../timeInputNormalization'

function makePeriods(): Array<Record<string, unknown>> {
  return [{
    period_name: 'Day',
    start_time: '06:00',
    end_time: '18:00',
    ramp_minutes: 0,
    heating_setpoint: 22,
    cooling_setpoint: 28,
    vpd_setpoint: 1.1,
    co2_setpoint: 900,
    details: '',
  }]
}

describe('normalizeTypedTimeText', () => {
  it('keeps valid HH:MM text untouched', () => {
    expect(normalizeTypedTimeText('14:00')).toBe('14:00')
    expect(normalizeTypedTimeText('06:15')).toBe('06:15')
  })

  it('normalizes four-digit entries to HH:MM', () => {
    expect(normalizeTypedTimeText('1400')).toBe('14:00')
    expect(normalizeTypedTimeText('1630')).toBe('16:30')
  })

  it('normalizes three-digit entries to H:MM', () => {
    expect(normalizeTypedTimeText('930')).toBe('09:30')
  })

  it('normalizes one and two digit hours to HH:00', () => {
    expect(normalizeTypedTimeText('9')).toBe('09:00')
    expect(normalizeTypedTimeText('12')).toBe('12:00')
  })

  it('preserves out-of-range or free text instead of inventing values', () => {
    expect(normalizeTypedTimeText('97')).toBe('97')
    expect(normalizeTypedTimeText('abc')).toBe('abc')
    expect(normalizeTypedTimeText('')).toBe('')
  })
})

describe('ClimatePeriodsTable time fields', () => {
  it('normalizes unseparated digits to HH:MM on blur without touching live typing', () => {
    function StatefulTable() {
      const [periods, setPeriods] = useState<ClimatePeriod[]>(makePeriods() as unknown as ClimatePeriod[])
      return <ClimatePeriodsTable periods={periods} onChange={setPeriods} />
    }
    const view = render(<StatefulTable />)
    const startInput = view.getAllByPlaceholderText('HH:MM')[0]

    fireEvent.change(startInput, { target: { value: '1400' } })
    expect((startInput as HTMLInputElement).value).toBe('1400')

    fireEvent.blur(startInput)
    expect((startInput as HTMLInputElement).value).toBe('14:00')
  })
})
