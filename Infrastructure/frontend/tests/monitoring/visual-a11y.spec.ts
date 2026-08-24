/**
 * Monitoring accessibility and visual QA browser coverage.
 *
 * Runs axe-core against both native monitoring pages at 375/768/1280 px and
 * across all six themes, asserting zero serious/critical violations. Also
 * verifies the keyboard/control alternatives (legend toggle, reset zoom, table
 * disclosure), canvas labelling (aria-label + aria-describedby), table
 * alternative discoverability (aria-expanded/aria-controls), reduced-motion
 * behavior, and that no request leaves the exact fixture origin.
 */
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { MONITORING_THEMES } from '../../src/features/monitoring/designTokens'
import { fixtureUrl } from './fixtureUrl'

const WIDTHS = [375, 768, 1280]

const PAGES = [
  {
    path: '/flower/monitoring',
    climateHeading: 'Flower climate conditions',
    deviceHeading: 'Flower atmosphere & equipment',
  },
  {
    path: '/vegetation/monitoring',
    climateHeading: 'Veg climate conditions',
    deviceHeading: 'Veg atmosphere & equipment',
  },
]

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', (req) => {
    const url = req.url()
    if (url.includes('/grafana/')) violations.push(`grafana: ${url}`)
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

function seriousCritical(results: {
  violations: { id: string; impact?: string | null; nodes: unknown[] }[]
}) {
  return results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
}

for (const page of PAGES) {
  for (const width of WIDTHS) {
    test(`axe has no serious/critical violations on ${page.path} at ${width}px with forced error`, async ({
      page: p,
      }, testInfo) => {
      await p.setViewportSize({ width, height: 900 })
      const violations = trackViolations(p)
      await p.goto(fixtureUrl(page.path, testInfo, 'error', 'force-error'))
      await expect(p.getByRole('heading', { name: page.climateHeading })).toBeVisible()
      await expect(p.getByRole('heading', { name: page.deviceHeading })).toBeVisible()
      await expect(p.locator('.mon-banner--error').first()).toBeVisible()

      const results = await new AxeBuilder({ page: p })
        .include('.mon-page')
        .analyze()
      const bad = seriousCritical(results)
      expect(
        bad.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
        `axe violations on ${page.path} at ${width}px with forced error`,
      ).toEqual([])
      expect(violations).toEqual([])
    })
  }
}

test('legend toggles and reset zoom are keyboard-operable', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring', testInfo))
  await expect(page.getByRole('heading', { name: 'Flower climate conditions' })).toBeVisible()

  const swatch = page.locator('.mon-legend__swatch').first()
  await expect(swatch).toBeVisible()
  const pressedBefore = await swatch.getAttribute('aria-pressed')
  await swatch.focus()
  await page.keyboard.press('Enter')
  const pressedAfter = await swatch.getAttribute('aria-pressed')
  expect(pressedAfter).not.toBe(pressedBefore)

  await page.getByRole('button', { name: 'Reset all series' }).first().click()
  await expect(page.locator('.mon-legend__swatch').first()).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: 'Reset Zoom' }).click()
  expect(violations).toEqual([])
})

test('chart canvas has an accessible name and description', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring', testInfo))
  await expect(page.getByRole('heading', { name: 'Flower climate conditions' })).toBeVisible()

  const chart = page.getByRole('img', { name: 'Flower climate conditions' })
  await expect(chart).toBeVisible()
  const describedBy = await chart.getAttribute('aria-describedby')
  expect(describedBy).toBeTruthy()
  await expect(page.locator(`#${describedBy}`)).toHaveText(/Temperature, relative humidity and VPD/)
  expect(violations).toEqual([])
})

test('table alternative is discoverable via aria-expanded and aria-controls', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring', testInfo))
  await expect(page.getByRole('heading', { name: 'Flower atmosphere & equipment' })).toBeVisible()

  const toggle = page.getByRole('button', { name: 'View data as table' }).first()
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  const controls = await toggle.getAttribute('aria-controls')
  expect(controls).toBeTruthy()
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator(`#${controls}`)).toBeVisible()
  expect(violations).toEqual([])
})

test('reduced-motion disables transitions on interactive controls', async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 1280, height: 900 })
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring', testInfo))
  await expect(page.getByRole('heading', { name: 'Flower climate conditions' })).toBeVisible()

  const toggle = page.getByRole('button', { name: 'View data as table' }).first()
  const transition = await toggle.evaluate((el) => getComputedStyle(el).transitionDuration)
  expect(transition).toBe('0s')
  expect(violations).toEqual([])
})

for (const theme of MONITORING_THEMES) {
  test(`axe has no serious/critical violations on flower in ${theme} theme with forced error`, async ({ page }, testInfo) => {
    await page.addInitScript((t) => localStorage.setItem('cea-theme', t), theme)
    await page.setViewportSize({ width: 1280, height: 900 })
    const violations = trackViolations(page)
    await page.goto(fixtureUrl('/flower/monitoring', testInfo, 'error', 'force-error'))
    await expect(page.getByRole('heading', { name: 'Flower climate conditions' })).toBeVisible()
    await expect(page.locator('.mon-banner--error').first()).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)

    const results = await new AxeBuilder({ page })
      .include('.mon-page')
      .analyze()
    const bad = seriousCritical(results)
    expect(
      bad.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
      `axe violations in ${theme} theme with forced error`,
    ).toEqual([])
    expect(violations).toEqual([])
  })
}
