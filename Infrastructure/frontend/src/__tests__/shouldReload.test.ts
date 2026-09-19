import { describe, expect, it } from 'vitest'
import { shouldReload } from '../main'

describe('shouldReload', () => {
  it('allows the first reload with no stamp', () => {
    expect(shouldReload(null, 1_000, 5_000)).toBe(true)
  })

  it('suppresses repeats inside the guard window', () => {
    expect(shouldReload(1_000, 1_000 + 4_999, 5_000)).toBe(false)
  })

  it('allows suppression to lapse at the exact boundary', () => {
    expect(shouldReload(1_000, 1_000 + 5_000, 5_000)).toBe(true)
  })
})
