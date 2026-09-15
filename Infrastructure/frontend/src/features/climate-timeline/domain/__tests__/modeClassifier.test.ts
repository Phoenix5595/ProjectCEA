import { describe, expect, it } from 'vitest'
import { isCanonicalConstantMode } from '../modeClassifier'

describe('isCanonicalConstantMode', () => {
  it.each(['sleep', 'SLEEP', ' sleep ', 'drying', 'DRYING', ' drying '])(
    'returns true for the canonical constant mode %s',
    modeName => {
      // Given: a mode name with casing or whitespace variation.
      // When: the mode is classified for timeline rendering.
      // Then: only canonical constant modes use the constant branch.
      expect(isCanonicalConstantMode(modeName)).toBe(true)
    }
  )

  it.each(['veg', 'flower', 'stretch', 'bulk', 'ripen', 'unknown', ''])(
    'returns false for the scheduled mode %s',
    modeName => {
      // Given: a scheduled or unknown mode name.
      // When: the mode is classified for timeline rendering.
      // Then: it remains eligible for the multi-period timeline.
      expect(isCanonicalConstantMode(modeName)).toBe(false)
    }
  )
})
