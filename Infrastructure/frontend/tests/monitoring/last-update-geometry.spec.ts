import { test, expect } from '@playwright/test'
import { fixtureUrl } from './fixtureUrl'

const WIDTHS = [375, 768, 1280] as const

const PAGES = [
  {
    path: '/flower/monitoring',
    tables: ['Averages', 'Front Cluster', 'Back Cluster'],
  },
  {
    path: '/vegetation/monitoring',
    tables: ['Sensor Values'],
  },
] as const

for (const width of WIDTHS) {
  for (const pageDefinition of PAGES) {
    test(`${pageDefinition.path} stacks Last Update at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 })
      await page.goto(fixtureUrl(pageDefinition.path, testInfo))

      for (const tableName of pageDefinition.tables) {
        const table = page.getByRole('table', { name: tableName })
        const lastUpdate = table
          .locator('tbody tr')
          .filter({ hasText: 'Last Update' })
          .locator('.mon-last-update')
        const spans = lastUpdate.locator(':scope > span')

        await expect(lastUpdate).toBeVisible()
        await expect(spans).toHaveCount(2)
        await expect(spans.nth(0)).toHaveCSS('display', 'block')
        await expect(spans.nth(1)).toHaveCSS('display', 'block')

        const geometry = await spans.evaluateAll(elements =>
          elements.map(element => {
            const rect = element.getBoundingClientRect()
            return { left: rect.left, top: rect.top, height: rect.height }
          })
        )
        expect(geometry[1]?.left).toBe(geometry[0]?.left)
        expect(geometry[1]?.top).toBeGreaterThan(geometry[0]?.top ?? 0)
        expect(geometry[0]?.height).toBeGreaterThan(0)
        expect(geometry[1]?.height).toBeGreaterThan(0)
      }
    })
  }
}
