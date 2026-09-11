import { expect, test } from '@playwright/test'
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

const LEGACY_REDIRECTS = [
  { path: '/laboratory/climate', destination: '/laboratory' },
  { path: '/laboratory/water', destination: '/laboratory' },
  { path: '/laboratory/infrastructure', destination: '/laboratory' },
  { path: '/flower/soil', destination: '/flower' },
] as const

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

for (const legacyRedirect of LEGACY_REDIRECTS) {
  test(`redirects ${legacyRedirect.path} to ${legacyRedirect.destination}`, async ({ page }, testInfo) => {
    await page.goto(fixtureUrl(legacyRedirect.path, testInfo))
    await page.waitForURL((url) => url.pathname === legacyRedirect.destination)

    expect(new URL(page.url()).pathname).toBe(legacyRedirect.destination)
  })
}
