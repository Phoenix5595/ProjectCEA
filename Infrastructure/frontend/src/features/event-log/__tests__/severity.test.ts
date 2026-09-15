import { describe, expect, it } from 'vitest'
import { SEVERITY_LABELS, type SeverityLevel } from '../presentation/severity'

describe('severity presentation', () => {
  it('provides a human-readable label for every envelope severity', () => {
    const levels: readonly SeverityLevel[] = ['info', 'warning', 'error', 'critical']

    for (const level of levels) {
      expect(SEVERITY_LABELS[level]).toBeTruthy()
    }
  })
})
