import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import {
  MONITORING_THEMES,
  REQUIRED_MONITORING_TOKENS,
  assertThemeTokenCoverage,
  getThemeTokenCoverage,
} from '../designTokens'

// `import.meta.url` is not a `file:` URL under jsdom; resolve from the cwd.
const themesCss = readFileSync(
  path.resolve(process.cwd(), 'src/styles/themes.css'),
  'utf8',
)
const monitoringCss = readFileSync(
  path.resolve(process.cwd(), 'src/features/monitoring/styles/monitoring.css'),
  'utf8',
)

describe('monitoring design tokens', () => {
  it('covers every monitoring token in all six themes', () => {
    const coverage = assertThemeTokenCoverage(themesCss)
    expect(coverage).toHaveLength(6)
    expect(coverage.map((c) => c.theme)).toEqual([...MONITORING_THEMES])
    for (const entry of coverage) {
      expect(entry.missing).toEqual([])
    }
    expect(REQUIRED_MONITORING_TOKENS.length).toBeGreaterThan(0)
  })

  it('rejects a missing required token', () => {
    const synthetic = `[data-theme="precision-void"] {\n  --mon-family-temperature: #f87171;\n}`
    expect(() => assertThemeTokenCoverage(synthetic)).toThrow(/precision-void/)
    expect(() => assertThemeTokenCoverage(synthetic)).toThrow(/--mon-family-rh/)

    const coverage = getThemeTokenCoverage(synthetic)
    const missing = coverage.find((c) => c.theme === 'precision-void')?.missing ?? []
    expect(missing).toContain('--mon-family-rh')
    expect(missing).toContain('--mon-target-dash')
  })

  it('requires the new accessible error/warning tokens in every theme', () => {
    const coverage = assertThemeTokenCoverage(themesCss)
    const required = new Set([
      '--mon-error-text',
      '--mon-error-border',
      '--mon-error-icon',
      '--mon-warning-text',
    ])
    for (const token of required) {
      expect(REQUIRED_MONITORING_TOKENS).toContain(token)
      for (const entry of coverage) {
        expect(entry.missing).not.toContain(token)
      }
    }
  })

  it('rejects a theme missing --mon-error-text (failure probe)', () => {
    // Strip the new text token from one theme; the validator must surface it.
    const stripped = themesCss.replace(
      /(\[data-theme="precision-void"\][^}]+)--mon-error-text: #[^;]+; /,
      '$1',
    )
    expect(() => assertThemeTokenCoverage(stripped)).toThrow(/precision-void/)
    expect(() => assertThemeTokenCoverage(stripped)).toThrow(/--mon-error-text/)
  })

  it('keeps the compact monitoring layout spacing contracts', () => {
    expect(monitoringCss).toMatch(/\.mon-toolbar\s*\{[^}]*margin-bottom:\s*0;/s)
    expect(monitoringCss).toMatch(/\.mon-side \.mon-card\s*\{[^}]*padding:\s*4px;/s)
    expect(monitoringCss).toMatch(/\.mon-main \.mon-card\s*\{[^}]*padding:\s*4px;/s)
    expect(monitoringCss).toMatch(/\.mon-card__title\s*\{[^}]*margin-bottom:\s*0;/s)
    expect(monitoringCss).toMatch(/\.mon-chart \.mon-legend\s*\{[^}]*padding-top:\s*0;[^}]*padding-bottom:\s*0;/s)
    expect(monitoringCss).toMatch(/\.mon-side \.mon-card table td,[\s\S]*\.mon-side \.mon-card table th\s*\{[^}]*padding:\s*0;/)
  })
})
