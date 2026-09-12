import { test, expect } from '@playwright/test'
import { fixtureUrl } from './fixtureUrl'

test('monitoring desktop geometry matches the historical layout contract', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(fixtureUrl('/flower/monitoring', testInfo))
  await expect(page.locator('.mon-layout')).toBeVisible()

  const geometry = await page.locator('.mon-layout').evaluate(layout => {
    const pageElement = layout.closest<HTMLElement>('.mon-page')
    const toolbar = pageElement?.querySelector<HTMLElement>('.mon-toolbar')
    const sidebar = layout.querySelector<HTMLElement>('.mon-side')
    const main = layout.querySelector<HTMLElement>('.mon-main')
    const table = sidebar?.querySelector('table')
    const sidebarRect = sidebar?.getBoundingClientRect()
    const mainRect = main?.getBoundingClientRect()
    return {
      pagePadding: pageElement ? getComputedStyle(pageElement).padding : '',
      toolbarDisplay: toolbar ? getComputedStyle(toolbar).display : '',
      toolbarFlexWrap: toolbar ? getComputedStyle(toolbar).flexWrap : '',
      toolbarPadding: toolbar ? getComputedStyle(toolbar).padding : '',
      toolbarMarginBottom: toolbar ? getComputedStyle(toolbar).marginBottom : '',
      gridTemplateColumns: getComputedStyle(layout).gridTemplateColumns,
      sidebarWidth: sidebarRect?.width ?? 0,
      mainWidth: mainRect?.width ?? 0,
      mainLeft: mainRect?.left ?? 0,
      tableWidth: table?.getBoundingClientRect().width ?? 0,
    }
  })

  expect(geometry).toMatchObject({
    pagePadding: '16px',
    toolbarDisplay: 'flex',
    toolbarFlexWrap: 'wrap',
    toolbarPadding: '0px',
    toolbarMarginBottom: '0px',
    gridTemplateColumns: '340px 684px',
    sidebarWidth: 340,
    tableWidth: 330,
  })
  expect(geometry.mainWidth).toBeGreaterThan(geometry.sidebarWidth)
  expect(geometry.mainLeft).toBeGreaterThan(geometry.sidebarWidth)
})
