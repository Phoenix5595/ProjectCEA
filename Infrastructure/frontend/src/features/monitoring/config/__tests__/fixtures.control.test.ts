import { describe, expect, it } from 'vitest'

import {
  ControlMonitoringResponse,
  ProjectionPublicationResponse,
} from '../../api/contracts/control'
import { controlProjectionFixture, controlRangeFixture } from '../fixtures.control'

const START = '2026-08-02T12:00:00.000Z'
const END = '2026-08-02T13:00:00.000Z'

describe('controlProjectionFixture', () => {
  it('emits a contract-valid normal projection publication', () => {
    const result = ProjectionPublicationResponse.safeParse(
      controlProjectionFixture('Flower Room', START, END),
    )

    expect(result.success).toBe(true)
    if (result.success) {
      expect(result.data.quality).toBe('estimated')
      expect(result.data.value).toHaveLength(2)
    }
  })

  it('emits a contract-valid unavailable publication for missing projections', () => {
    const result = ProjectionPublicationResponse.safeParse(
      controlProjectionFixture('Flower Room', START, END, 'missing-projection'),
    )

    expect(result.success).toBe(true)
    if (result.success) {
      expect(result.data.quality).toBe('unavailable')
      expect(result.data.value).toHaveLength(0)
    }
  })

  it('emits a contract-valid nullable projection sample beside recorded setpoints', () => {
    const recorded = ControlMonitoringResponse.safeParse(
      controlRangeFixture('Flower Room', START, END),
    )
    const projected = ProjectionPublicationResponse.safeParse(
      controlProjectionFixture('Flower Room', START, END, 'nullable-projection'),
    )

    expect(recorded.success).toBe(true)
    expect(projected.success).toBe(true)
    if (!recorded.success || !projected.success) return

    expect(recorded.data.climate[0]?.points[0]?.value).toBe(22)
    const heating = projected.data.value.flatMap((interval) =>
      interval.series.filter((point) => point.series_id.value === 'climate.heating_setpoint_target'),
    )
    const cooling = projected.data.value.flatMap((interval) =>
      interval.series.filter((point) => point.series_id.value === 'climate.cooling_setpoint_target'),
    )
    expect(heating.map((point) => point.value)).toEqual([22, 24])
    expect(cooling.map((point) => point.value)).toEqual([27, null])
  })
})
