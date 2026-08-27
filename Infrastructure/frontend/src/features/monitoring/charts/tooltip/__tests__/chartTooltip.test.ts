import { describe, expect, it } from 'vitest'
import { formatTooltipValue, valueAtCursor } from '../chartTooltip'

describe('valueAtCursor', () => {
  it('interpolates a linear series between finite samples', () => {
    // Given: a linear ramp with values at either side of the cursor
    const xValues = [0, 10]
    const yValues = [10, 30]

    // When: the cursor is halfway through the ramp
    const value = valueAtCursor('linear', xValues, yValues, 5)

    // Then: the tooltip uses the interpolated value
    expect(value).toBe(20)
  })

  it('interpolates a sensor series between finite samples', () => {
    // Given: a continuous sensor trace with values around the cursor
    const xValues = [0, 10]
    const yValues = [10, 30]

    // When: the cursor is halfway through the trace
    const value = valueAtCursor('sensor', xValues, yValues, 5)

    // Then: the tooltip reports the trace value at that time
    expect(value).toBe(20)
  })

  it('holds a step series left value until its next timestamp', () => {
    // Given: a device state that changes at the next sample
    const xValues = [0, 10]
    const yValues = [0, 100]

    // When: the cursor is between state changes
    const value = valueAtCursor('step', xValues, yValues, 9)

    // Then: the tooltip reports the active left state
    expect(value).toBe(0)
  })

  it('uses the nearest actual point value', () => {
    // Given: sparse point values on either side of the cursor
    const xValues = [0, 10]
    const yValues = [10, 30]

    // When: the cursor is closer to the right point
    const value = valueAtCursor('point', xValues, yValues, 8)

    // Then: the tooltip preserves a real point value rather than interpolating
    expect(value).toBe(30)
  })

  it('returns null inside a null gap', () => {
    // Given: a missing sample adjacent to the cursor interval
    const xValues = [0, 10, 20]
    const yValues = [10, null, 30]

    // When: the cursor falls within the gap
    const value = valueAtCursor('linear', xValues, yValues, 5)

    // Then: no value is invented across the gap
    expect(value).toBeNull()
  })

  it('does not extrapolate before or after finite samples', () => {
    // Given: finite values with a bounded time domain
    const xValues = [0, 10]
    const yValues = [10, 30]

    // When: the cursor is outside that domain
    const beforeFirst = valueAtCursor('step', xValues, yValues, -1)
    const afterLast = valueAtCursor('linear', xValues, yValues, 11)

    // Then: neither side extrapolates a value
    expect(beforeFirst).toBeNull()
    expect(afterLast).toBeNull()
  })
})

describe('formatTooltipValue', () => {
  it('renders presentation precision and null gaps', () => {
    // Given: a series with manifest precision and a value or gap
    const presentation = { decimals: 2 }

    // When: tooltip row text is formatted
    const formatted = formatTooltipValue(12.345, presentation, '°C')
    const gap = formatTooltipValue(null, presentation, '°C')

    // Then: precision and the gap marker are preserved in rendered text
    expect(formatted).toBe(' 12.35 °C')
    expect(gap).toBe(' —')
  })
})
