/**
 * Accessibility policy for the monitoring chart regions.
 *
 * A pure, dependency-free contract that every chart region must satisfy before
 * it is considered accessible. The room pages and the browser axe-core spec
 * both rely on these requirements; the accompanying Vitest test proves the
 * validator rejects a contract that omits a required capability.
 */

export interface ChartAccessibilityContract {
  /** Non-empty accessible name for the chart region (canvas `aria-label`). */
  accessibleName: string
  /** Longer description linked via `aria-describedby`. */
  description?: string
  /** A semantic table alternative is reachable (e.g. `aria-expanded`/`aria-controls`). */
  tableAlternative: boolean
  /** Every pointer action has a keyboard/control equivalent. */
  keyboardControls: boolean
  /** Keyboard focus is visible. */
  focusVisible: boolean
  /** Text/background contrast for status/error text meets WCAG 2.2 AA (>= 4.5:1). */
  colorContrast: boolean
}

export class AccessibilityPolicyError extends Error {
  readonly missing: string[]

  constructor(missing: string[]) {
    super(`Chart accessibility contract incomplete: missing ${missing.join(', ')}`)
    this.name = 'AccessibilityPolicyError'
    this.missing = missing
  }
}

/**
 * Validates a chart accessibility contract, throwing `AccessibilityPolicyError`
 * listing every required capability that is absent. Returns the contract
 * unchanged when it is complete.
 */
export function validateChartAccessibility(
  contract: ChartAccessibilityContract,
): ChartAccessibilityContract {
  const missing: string[] = []
  if (contract.accessibleName.trim() === '') missing.push('accessibleName')
  if (!contract.tableAlternative) missing.push('tableAlternative')
  if (!contract.keyboardControls) missing.push('keyboardControls')
  if (!contract.focusVisible) missing.push('focusVisible')
  if (!contract.colorContrast) missing.push('colorContrast')
  if (missing.length > 0) throw new AccessibilityPolicyError(missing)
  return contract
}
