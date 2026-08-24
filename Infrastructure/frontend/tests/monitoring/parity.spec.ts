import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { flowerManifest, vegManifest } from '../../src/features/monitoring/config'
import type { MonitoringManifest } from '../../src/features/monitoring/config'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

const NATIVE_PAGES: ReadonlyArray<{
  readonly path: string
  readonly manifest: MonitoringManifest
}> = [
  { path: '/flower/monitoring', manifest: flowerManifest },
  { path: '/vegetation/monitoring', manifest: vegManifest },
]

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (request) => {
    const url = request.url()
    if (url.includes('/grafana/')) violations.push(`grafana: ${url}`)
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

for (const nativePage of NATIVE_PAGES) {
  test(`renders every canonical ${nativePage.manifest.room} panel natively`, async ({ page }, testInfo) => {
    const violations = trackViolations(page)
    await page.goto(fixtureUrl(nativePage.path, testInfo))

    for (const panel of nativePage.manifest.panels) {
      if (panel.kind === 'table') {
        await expect(page.getByRole('table', { name: panel.title })).toBeVisible()
        for (const row of panel.rows) {
          await expect(page.getByRole('table', { name: panel.title })).toContainText(row)
        }
        continue
      }
      await expect(page.getByRole('heading', { name: panel.title })).toBeVisible()
      await expect(page.getByRole('img', { name: panel.title })).toBeVisible()
      expect(panel.series.map((series) => series.name)).toHaveLength(
        new Set(panel.series.map((series) => series.name)).size,
      )
    }

    expect(violations).toEqual([])
  })
}

test('keeps Soil and Laboratory monitoring on GrafanaPanel', () => {
  const pages = [
    'src/pages/FlowerSoil.tsx',
    'src/pages/LaboratoryClimate.tsx',
    'src/pages/LaboratoryWater.tsx',
    'src/pages/LaboratoryInfrastructure.tsx',
  ]

  for (const page of pages) {
    const source = readFileSync(path.resolve(process.cwd(), page), 'utf8')
    expect(source, page).toMatch(/import GrafanaPanel from ['"]\.\.\/components\/GrafanaPanel['"]/) 
    expect(source, page).toContain('<GrafanaPanel')
  }
})
