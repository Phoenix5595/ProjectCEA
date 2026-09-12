import { test, expect } from '@playwright/test'
import { fixtureUrl } from './fixtureUrl'

const WIDTHS = [375, 768, 1280] as const

for (const width of WIDTHS) {
  test(`monitoring table/sidebar geometry remains compact at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(fixtureUrl('/flower/monitoring', testInfo))
    await expect(page.locator('.mon-layout')).toBeVisible()

    const geometry = await page.locator('.mon-layout').evaluate(layout => {
      const sidebar = layout.querySelector<HTMLElement>('.mon-side')
      const main = layout.querySelector<HTMLElement>('.mon-main')
      const table = sidebar?.querySelector('table')
      const sidebarRect = sidebar?.getBoundingClientRect()
      const mainRect = main?.getBoundingClientRect()
      return {
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        gridTemplateColumns: getComputedStyle(layout).gridTemplateColumns,
        sidebarWidth: sidebarRect?.width ?? 0,
        sidebarLeft: sidebarRect?.left ?? 0,
        sidebarBottom: sidebarRect?.bottom ?? 0,
        mainWidth: mainRect?.width ?? 0,
        mainLeft: mainRect?.left ?? 0,
        mainTop: mainRect?.top ?? 0,
        tableWidth: table?.getBoundingClientRect().width ?? 0,
      }
    })
    console.log(JSON.stringify({ width, geometry }))

    expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth)
    if (width >= 1100) {
      expect(Math.round(geometry.sidebarWidth)).toBe(340)
      expect(geometry.gridTemplateColumns.startsWith('340px')).toBe(true)
      expect(geometry.tableWidth).toBeLessThanOrEqual(340)
      expect(geometry.mainWidth).toBeGreaterThan(geometry.sidebarWidth)
      expect(geometry.mainLeft).toBeGreaterThan(geometry.sidebarLeft)
    } else {
      expect(Math.abs(geometry.mainLeft - geometry.sidebarLeft)).toBeLessThan(1)
      expect(geometry.mainTop).toBeGreaterThan(geometry.sidebarBottom)
    }
  })
}
