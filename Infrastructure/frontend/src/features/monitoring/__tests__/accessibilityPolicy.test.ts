/**
 * Accessibility policy failure-path tests.
 *
 * Proves the chart accessibility validator rejects a contract that omits a
 * required capability (focus visibility, table alternative, keyboard controls,
 * or an accessible name) and accepts a complete contract. This is the failure
 * gate for Todo 29: a chart region that cannot be operated or understood
 * without a pointer or without a data alternative must fail policy.
 */
import { describe, expect, it } from 'vitest'
import {
  AccessibilityPolicyError,
  validateChartAccessibility,
  type ChartAccessibilityContract,
} from '../accessibilityPolicy'

const COMPLETE: ChartAccessibilityContract = {
  accessibleName: 'Temperature, RH & VPD - Main Graph',
  description: 'Temperature, relative humidity and VPD over the selected range.',
  tableAlternative: true,
  keyboardControls: true,
  focusVisible: true,
  colorContrast: true,
}

describe('accessibilityPolicy', () => {
  it('accepts a complete chart accessibility contract', () => {
    expect(validateChartAccessibility(COMPLETE)).toBe(COMPLETE)
  })

  it('rejects missing focus and table alternative', () => {
    const contract: ChartAccessibilityContract = {
      ...COMPLETE,
      tableAlternative: false,
      focusVisible: false,
    }
    expect(() => validateChartAccessibility(contract)).toThrow(AccessibilityPolicyError)
    try {
      validateChartAccessibility(contract)
    } catch (err) {
      const policyError = err as AccessibilityPolicyError
      expect(policyError.missing).toContain('tableAlternative')
      expect(policyError.missing).toContain('focusVisible')
      expect(policyError.missing).not.toContain('accessibleName')
      expect(policyError.missing).not.toContain('keyboardControls')
    }
  })

  it('rejects a missing accessible name', () => {
    const contract: ChartAccessibilityContract = { ...COMPLETE, accessibleName: '   ' }
    expect(() => validateChartAccessibility(contract)).toThrow(AccessibilityPolicyError)
  })

  it('rejects missing keyboard controls', () => {
    const contract: ChartAccessibilityContract = { ...COMPLETE, keyboardControls: false }
    expect(() => validateChartAccessibility(contract)).toThrow(AccessibilityPolicyError)
  })

  it('rejects missing color contrast', () => {
    const contract: ChartAccessibilityContract = { ...COMPLETE, colorContrast: false }
    expect(() => validateChartAccessibility(contract)).toThrow(AccessibilityPolicyError)
    try {
      validateChartAccessibility(contract)
    } catch (err) {
      const policyError = err as AccessibilityPolicyError
      expect(policyError.missing).toContain('colorContrast')
      expect(policyError.missing).not.toContain('accessibleName')
      expect(policyError.missing).not.toContain('tableAlternative')
      expect(policyError.missing).not.toContain('keyboardControls')
      expect(policyError.missing).not.toContain('focusVisible')
    }
  })

  it('accepts a contract asserting color contrast', () => {
    const minimal: ChartAccessibilityContract = {
      accessibleName: 'X',
      tableAlternative: true,
      keyboardControls: true,
      focusVisible: true,
      colorContrast: true,
    }
    expect(validateChartAccessibility(minimal)).toBe(minimal)
  })
})
