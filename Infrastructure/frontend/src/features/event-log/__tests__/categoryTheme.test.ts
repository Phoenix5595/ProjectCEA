import { describe, it, expect } from 'vitest'

import {
  categoryTheme,
  displayCategoryOf,
  EVENT_BUCKET_ORDERS,
  EVENT_CATEGORY_LABELS,
  type EventBucketCount,
} from '../presentation/categoryTheme'

const BUCKET_COUNTS: readonly EventBucketCount[] = [6, 8]

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

describe('display category split', () => {
  it.each(BUCKET_COUNTS)(
    'funnels sensor/device health events into the Sensors bucket in the %d-bucket policy',
    bucketCount => {
      expect(displayCategoryOf({ category: 'system', type: 'sensor.degraded' }, bucketCount)).toBe(
        'sensor'
      )
      expect(displayCategoryOf({ category: 'system', type: 'device.timeout' }, bucketCount)).toBe(
        'sensor'
      )
    }
  )

  it('keeps platform events in the System bucket in both policies', () => {
    for (const bucketCount of BUCKET_COUNTS) {
      expect(displayCategoryOf({ category: 'system', type: 'system.failsafe_raised' }, bucketCount)).toBe(
        'system'
      )
      expect(displayCategoryOf({ category: 'system', type: 'transport.degraded' }, bucketCount)).toBe(
        'system'
      )
    }
  })

  it('merges raw ramp into Control and raw mutation into System in the six-bucket policy', () => {
    expect(displayCategoryOf({ category: 'ramp', type: 'ramp.started' }, 6)).toBe('control')
    expect(displayCategoryOf({ category: 'mutation', type: 'config.updated' }, 6)).toBe('system')
  })

  it('keeps Ramp and Mutation as their own buckets in the eight-bucket policy', () => {
    expect(displayCategoryOf({ category: 'ramp', type: 'ramp.started' }, 8)).toBe('ramp')
    expect(displayCategoryOf({ category: 'mutation', type: 'config.updated' }, 8)).toBe('mutation')
  })

  it('exposes exactly the two canonical slot orders', () => {
    expect(EVENT_BUCKET_ORDERS[6]).toEqual([
      'relay',
      'sensor',
      'control',
      'manual_override',
      'alarm',
      'system',
    ])
    expect(EVENT_BUCKET_ORDERS[8]).toEqual([
      'relay',
      'sensor',
      'ramp',
      'control',
      'manual_override',
      'mutation',
      'alarm',
      'system',
    ])
  })

  it('passes all other categories through untouched in both policies', () => {
    for (const bucketCount of BUCKET_COUNTS) {
      expect(displayCategoryOf({ category: 'relay', type: 'relay.commanded' }, bucketCount)).toBe(
        'relay'
      )
      expect(
        displayCategoryOf({ category: 'control', type: 'control.setpoint_changed' }, bucketCount)
      ).toBe('control')
      expect(displayCategoryOf({ category: 'nonsense', type: 'x.y' }, bucketCount)).toBe('nonsense')
    }
  })
})

describe('event category theme', () => {
  it('resolves a chip, border, and text tuple for each of the 8 categories', () => {
    for (const category of CATEGORIES) {
      const visual = categoryTheme(category)
      expect(visual.label).toBe(
        EVENT_CATEGORY_LABELS[category as keyof typeof EVENT_CATEGORY_LABELS]
      )
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
    const hues = new Set(CATEGORIES.map(category => categoryTheme(category).text))
    expect(hues.size).toBe(CATEGORIES.length)
  })
})
