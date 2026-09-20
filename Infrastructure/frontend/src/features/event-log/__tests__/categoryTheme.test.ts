import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { categoryTheme, displayCategoryOf, EVENT_CATEGORY_LABELS } from '../presentation/categoryTheme'

const themesCss = readFileSync(
  path.resolve(process.cwd(), 'src/styles/themes.css'),
  'utf8',
)

const CATEGORIES = [
  'relay',
  'manual_override',
  'ramp',
  'control',
  'mutation',
  'alarm',
  'system',
  'sensor',
] as const

const THEMES = [
  'precision-void',
  'control-room',
  'verdant-growth',
  'spectrum',
  'obsidian',
  'botanical',
] as const

describe('display category split', () => {
  it('funnels sensor/device health events into the Sensors bucket', () => {
    expect(displayCategoryOf({ category: 'system', type: 'sensor.degraded' })).toBe('sensor')
    expect(displayCategoryOf({ category: 'system', type: 'device.timeout' })).toBe('sensor')
  })

  it('keeps platform events in the System bucket', () => {
    expect(displayCategoryOf({ category: 'system', type: 'system.failsafe_raised' })).toBe('system')
    expect(displayCategoryOf({ category: 'system', type: 'transport.degraded' })).toBe('system')
  })

  it('passes all other categories through untouched', () => {
    expect(displayCategoryOf({ category: 'relay', type: 'relay.commanded' })).toBe('relay')
    expect(displayCategoryOf({ category: 'nonsense', type: 'x.y' })).toBe('nonsense')
  })
})

describe('event category theme', () => {
  it('resolves a chip, border, and text tuple for each of the 8 categories', () => {
    for (const category of CATEGORIES) {
      const visual = categoryTheme(category)
      expect(visual.label).toBe(EVENT_CATEGORY_LABELS[category as keyof typeof EVENT_CATEGORY_LABELS])
      expect(visual.chip).toMatch(/bg-event-/)
      expect(visual.chip).toMatch(/border-event-/)
      expect(visual.text).toMatch(/text-event-/)
    }
  })

  it('falls back safely for an unknown category', () => {
    const visual = categoryTheme('no_such_category')
    expect(visual.label).toBe('Other')
    expect(visual.chip).toContain('bg-surface-tertiary')
    expect(visual.text).toContain('text-text-default')
  })

  it('gives all 8 buckets distinct hues', () => {
    const hues = new Set(CATEGORIES.map((category) => categoryTheme(category).text))
    expect(hues.size).toBe(CATEGORIES.length)
  })

  it('pairs every colour with its text label', () => {
    for (const category of CATEGORIES) {
      expect(categoryTheme(category).label.length).toBeGreaterThan(0)
    }
  })

  it('declares the event tokens in every theme', () => {
    for (const theme of THEMES) {
      const block = themesCss.split(`[data-theme="${theme}"] {`)[1]?.split('\n}')[0] ?? ''
      assertBlockHasCategoryTokens(theme, block)
    }
  })

  it('declares no --event-* tokens outside theme blocks (non-scoped leak guard)', () => {
    const withoutThemeBlocks = themesCss.replace(/\[data-theme="[a-z-]+"\] \{[\s\S]*?\n\}/g, '')
    expect(withoutThemeBlocks).not.toContain('--event-')
  })
})

function assertBlockHasCategoryTokens(theme: (typeof THEMES)[number], block: string): void {
  for (const category of CATEGORIES) {
    const cssName = category.replace('_', '-')
    for (const variant of ['', '-dim', '-border']) {
      expect(block, `${theme} --event-${cssName}${variant}`).toContain(`--event-${cssName}${variant}:`)
    }
  }
}
