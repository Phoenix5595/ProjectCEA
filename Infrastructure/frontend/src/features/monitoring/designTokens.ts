/**
 * Monitoring design-system contract.
 *
 * Single source of truth for the CSS custom properties that the Flower/Veg
 * monitoring dashboards consume at runtime. Every token listed here MUST be
 * present in every theme block of `src/styles/themes.css`; the accompanying
 * test (`__tests__/designTokens.test.ts`) enforces that coverage so a theme
 * can never silently drop a chart-family color, envelope, target, or state
 * token.
 *
 * The token set is additive-only: it never removes or renames an existing
 * theme variable, and it never touches product pages or Grafana code.
 */

/** The six themes that must each carry the full monitoring token set. */
export const MONITORING_THEMES = [
  'precision-void',
  'control-room',
  'verdant-growth',
  'spectrum',
  'obsidian',
  'botanical',
] as const

/**
 * Every monitoring token that must exist in every theme.
 *
 * Families: temperature (left axis), rh, vpd, co2, pressure, device, light
 * (right axis). Node variants distinguish Flower front/back. Envelope tokens
 * style the min/max band around the sensor mean. Recorded/projected tokens
 * distinguish historical effective targets from simulated future ones
 * (projected uses the same family color at lower opacity with a dash).
 * Sun/moon tokens paint the plot-wide photoperiod background intervals.
 * Focus/tooltip/stale/error tokens cover interaction and provenance states.
 */
export const REQUIRED_MONITORING_TOKENS = [
  // Metric families
  '--mon-family-temperature',
  '--mon-family-rh',
  '--mon-family-vpd',
  '--mon-family-co2',
  '--mon-family-pressure',
  '--mon-family-device',
  '--mon-family-light',
  // Node variants
  '--mon-node-front',
  '--mon-node-back',
  // Min/max envelope
  '--mon-envelope-fill',
  '--mon-envelope-stroke',
  // Recorded vs projected targets
  '--mon-target-recorded',
  '--mon-target-projected',
  '--mon-target-projected-opacity',
  '--mon-target-dash',
  // Sun/moon overlays
  '--mon-sun-bg',
  '--mon-moon-bg',
  // Focus / tooltip / stale / error states
  '--mon-focus-ring',
  '--mon-tooltip-bg',
  '--mon-tooltip-border',
  '--mon-tooltip-text',
  '--mon-stale',
  '--mon-error',
  // Accessible text/edge tokens for error and warning states. Text tokens
  // guarantee >= 4.5:1 contrast against the banner/card surface without
  // touching global palettes; border/icon tokens are used by banner variants.
  '--mon-error-text',
  '--mon-error-border',
  '--mon-error-icon',
  '--mon-warning-text',
  // Secondary text (table headers, timezone label) with AA contrast on the
  // card surface. Distinct from the app-wide `--text-secondary`, which the
  // global `:root` overrides to `--text-muted`.
  '--mon-text-secondary',
  // Axis placement (temperature left, other families right)
  '--mon-axis-left',
  '--mon-axis-right',
] as const

export interface ThemeTokenCoverage {
  theme: string
  missing: string[]
}

/** Parse `[data-theme="..."] { ... }` blocks into a theme -> token-set map. */
export function parseThemeBlocks(css: string): Map<string, Set<string>> {
  const blocks = new Map<string, Set<string>>()
  const blockRe = /\[data-theme="([^"]+)"\]\s*\{([^}]*)\}/g
  let block: RegExpExecArray | null
  while ((block = blockRe.exec(css)) !== null) {
    const [, theme, body] = block
    const tokens = new Set<string>()
    const tokenRe = /(--[a-zA-Z0-9-]+)\s*:/g
    let token: RegExpExecArray | null
    while ((token = tokenRe.exec(body)) !== null) {
      tokens.add(token[1])
    }
    blocks.set(theme, tokens)
  }
  return blocks
}

/** Non-throwing coverage report for every known theme. */
export function getThemeTokenCoverage(css: string): ThemeTokenCoverage[] {
  const blocks = parseThemeBlocks(css)
  return MONITORING_THEMES.map((theme) => {
    const tokens = blocks.get(theme) ?? new Set<string>()
    const missing = REQUIRED_MONITORING_TOKENS.filter((t) => !tokens.has(t))
    return { theme, missing }
  })
}

/**
 * Throws with theme + token when any required monitoring token is absent
 * from any theme. Returns the coverage report when everything is present.
 */
export function assertThemeTokenCoverage(css: string): ThemeTokenCoverage[] {
  const coverage = getThemeTokenCoverage(css)
  for (const entry of coverage) {
    if (entry.missing.length > 0) {
      throw new Error(
        `Theme "${entry.theme}" is missing required monitoring token(s): ${entry.missing.join(', ')}`,
      )
    }
  }
  return coverage
}
