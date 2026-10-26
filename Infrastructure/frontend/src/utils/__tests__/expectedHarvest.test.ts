import { describe, expect, it } from 'vitest'

import { formatHarvestDay } from '../expectedHarvest'

describe('formatHarvestDay', () => {
  it('renders a floor date as a short month and day', () => {
    expect(formatHarvestDay('2027-01-03')).toBe('Jan 3')
    expect(formatHarvestDay('2026-11-22')).toBe('Nov 22')
  })

  it('never shifts the calendar day with the viewer zone', () => {
    expect(formatHarvestDay('2027-01-01')).toBe('Jan 1')
    expect(formatHarvestDay('2026-12-31')).toBe('Dec 31')
  })
})
