import { describe, expect, it } from 'vitest'

import { flowerManifest } from '../config/flowerManifest'
import { vegManifest } from '../config/vegManifest'

function panelIds(manifest: typeof flowerManifest | typeof vegManifest): string[] {
  return manifest.panels.map(panel => panel.id)
}

describe('active monitoring manifests', () => {
  it('defines exactly the active six Flower and four Veg panels', () => {
    expect(panelIds(flowerManifest)).toEqual([
      'flower-averages',
      'flower-climate',
      'flower-front',
      'flower-back',
      'flower-systems',
      'flower-statistics',
    ])
    expect(panelIds(vegManifest)).toEqual([
      'veg-values',
      'veg-climate',
      'veg-systems',
      'veg-statistics',
    ])
  })

  it('selects chart series by normalized semantic fields', () => {
    const charts = [...flowerManifest.panels, ...vegManifest.panels].filter(
      panel => panel.kind === 'timeseries'
    )

    for (const chart of charts) {
      expect(chart.sources.length).toBeGreaterThan(0)
      expect(chart.families.length).toBeGreaterThan(0)
    }
  })

  it('excludes the retired Water Level Avg row from Flower averages', () => {
    const averages = flowerManifest.panels.find(panel => panel.id === 'flower-averages')
    if (averages === undefined || averages.kind !== 'table') {
      throw new Error('Flower averages table is required')
    }

    expect(averages.rows).not.toContain('Water Level Avg')
  })

  it('gives heating, cooling and VPD setpoints their own dotted colour in both climate panels', () => {
    for (const [manifest, panelId] of [
      [flowerManifest, 'flower-climate'],
      [vegManifest, 'veg-climate'],
    ] as const) {
      const panel = manifest.panels.find(candidate => candidate.id === panelId)
      if (panel === undefined || panel.kind !== 'timeseries') throw new Error(`${panelId} missing`)
      expect(panel.sources).toContain('climate')
      const specs = ['heating_setpoint', 'cooling_setpoint', 'vpd_setpoint'].map(name =>
        panel.series.find(spec => spec.name === name)
      )
      expect(specs.map(spec => spec?.displayName)).toEqual([
        'Heating Setpoint',
        'Cooling Setpoint',
        'VPD Setpoint',
      ])
      expect(specs.every(spec => spec?.lineStyle === 'dot')).toBe(true)
      expect(new Set(specs.map(spec => spec?.color)).size).toBe(3)
    }
  })
})
