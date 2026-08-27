import { describe, expect, it } from 'vitest'

import { ProjectionPublicationResponse } from '../../api/contracts/control'
import { controlProjectionFixture } from '../fixtures.control'

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
      expect(result.data.value).toHaveLength(1)
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
})
