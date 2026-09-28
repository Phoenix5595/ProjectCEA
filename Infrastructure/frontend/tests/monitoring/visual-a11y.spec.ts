/**
 * Monitoring accessibility and visual QA browser coverage.
 *
 * Runs axe-core against both native monitoring pages at the two configured desktop viewports and
 * across all six themes, asserting zero serious/critical violations. Also
 * verifies the keyboard/control alternatives (legend toggle, reset zoom, table
 * disclosure), canvas labelling (aria-label + aria-describedby), table
 * alternative discoverability (aria-expanded/aria-controls), reduced-motion
 * behavior, and that no request leaves the exact fixture origin.
 */
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import {
  MONITORING_THEMES,
  REQUIRED_MONITORING_TOKENS,
} from '../../src/features/monitoring/designTokens'
import { fixtureUrl } from './fixtureUrl'

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
  page.on('request', req => {
    const url = req.url()
    if (url.includes('/grafana/')) violations.push(`grafana: ${url}`)
    const violation = describeViolation(url)
    if (violation !== null) violations.push(`${violation}: ${url}`)
  })
  return violations
}

function wcagContrast(foreground: string, background: string): number {
  const luminance = (color: string): number => {
    const channels = color
      .match(/\d+(?:\.\d+)?/g)
      ?.slice(0, 3)
      .map(Number)
    if (channels === undefined || channels.length !== 3) {
      throw new Error(`Cannot read computed RGB color: ${color}`)
    }
    const [red, green, blue] = channels.map(channel => {
      const value = channel / 255
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
    })
    if (red === undefined || green === undefined || blue === undefined) {
      throw new Error(`Incomplete computed RGB color: ${color}`)
    }
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue
  }
  const lighter = Math.max(luminance(foreground), luminance(background))
  const darker = Math.min(luminance(foreground), luminance(background))
  return (lighter + 0.05) / (darker + 0.05)
}

function seriousCritical(results: {
  violations: { id: string; impact?: string | null; nodes: unknown[] }[]
}) {
  return results.violations.filter(v => v.impact === 'serious' || v.impact === 'critical')
}

for (const page of PAGES) {
  test(`axe has no serious/critical violations on ${page.path} with forced error`, async ({
    page: p,
  }, testInfo) => {
    const violations = trackViolations(p)
    await p.goto(fixtureUrl(page.path, testInfo, 'error', 'force-error'))
    await expect(p.getByRole('heading', { name: page.climateHeading })).toBeVisible()
    await expect(p.getByRole('heading', { name: page.deviceHeading })).toBeVisible()
    await expect(p.locator('.mon-banner--error').first()).toBeVisible()

    const results = await new AxeBuilder({ page: p }).include('.mon-page').analyze()
    const bad = seriousCritical(results)
    expect(
      bad.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
      `axe violations on ${page.path} with forced error`
    ).toEqual([])
    expect(violations).toEqual([])
  })
}

test('sensor rail toggles and reset zoom are keyboard-operable', async ({ page }, testInfo) => {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring', testInfo))
  await expect(page.getByRole('heading', { name: 'Flower climate conditions' })).toBeVisible()

  const boxToggle = page.getByRole('button', { name: /Dry Bulb/ }).first()
  await expect(boxToggle).toBeVisible()
  const pressedBefore = await boxToggle.getAttribute('aria-pressed')
  await boxToggle.focus()
  await page.keyboard.press('Enter')
  const pressedAfter = await boxToggle.getAttribute('aria-pressed')
  expect(pressedAfter).not.toBe(pressedBefore)

  await page.keyboard.press('Enter')
  await expect(boxToggle).toHaveAttribute('aria-pressed', pressedBefore ?? 'false')

  await page.getByRole('button', { name: 'Reset Zoom' }).click()
  expect(violations).toEqual([])
})

test('chart canvas has an accessible name and description', async ({ page }, testInfo) => {
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

test('table alternative is discoverable via aria-expanded and aria-controls', async ({
  page,
}, testInfo) => {
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
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/monitoring', testInfo))
  await expect(page.getByRole('heading', { name: 'Flower climate conditions' })).toBeVisible()

  const toggle = page.getByRole('button', { name: 'View data as table' }).first()
  const transition = await toggle.evaluate(el => getComputedStyle(el).transitionDuration)
  expect(transition).toBe('0s')
  expect(violations).toEqual([])
})

for (const theme of MONITORING_THEMES) {
  test(`axe has no serious/critical violations on flower in ${theme} theme with forced error`, async ({
    page,
  }, testInfo) => {
    await page.addInitScript(t => localStorage.setItem('cea-theme', t), theme)
    const violations = trackViolations(page)
    await page.goto(fixtureUrl('/flower/monitoring', testInfo, 'error', 'force-error'))
    await expect(page.getByRole('heading', { name: 'Flower climate conditions' })).toBeVisible()
    await expect(page.locator('.mon-banner--error').first()).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    const missingTokens = await page.evaluate(tokens => {
      const style = getComputedStyle(document.documentElement)
      return tokens.filter(token => style.getPropertyValue(token).trim() === '')
    }, REQUIRED_MONITORING_TOKENS)
    expect(missingTokens, `${theme} monitoring custom properties`).toEqual([])

    const results = await new AxeBuilder({ page }).include('.mon-page').analyze()
    const bad = seriousCritical(results)
    expect(
      bad.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
      `axe violations in ${theme} theme with forced error`
    ).toEqual([])
    expect(violations).toEqual([])
  })
}
for (const theme of MONITORING_THEMES) {
  test(`active sector controls keep AA contrast in ${theme}`, async ({ page }, testInfo) => {
    const violations = trackViolations(page)
    await page.addInitScript(value => localStorage.setItem('cea-theme', value), theme)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto(fixtureUrl('/flower/control', testInfo))
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)

    const primaryNavigation = page.getByRole('navigation', { name: 'Primary navigation' })
    const activeSidebarLink = primaryNavigation.getByRole('link', { name: 'Flower', exact: true })
    const activeTopTab = page.getByRole('link', { name: 'Control', exact: true })
    const saveButton = page.getByRole('button', { name: 'SAVE', exact: true })
    await expect(activeSidebarLink).toBeVisible()
    await expect(activeTopTab).toBeVisible()
    await expect(saveButton).toBeVisible()

    const sidebarStyle = await activeSidebarLink.evaluate(element => {
      const style = getComputedStyle(element)
      return { foreground: style.color, background: style.backgroundColor }
    })
    const topTabStyle = await activeTopTab.evaluate(element => {
      const style = getComputedStyle(element)
      return { foreground: style.color, background: style.backgroundColor }
    })
    expect(
      wcagContrast(sidebarStyle.foreground, sidebarStyle.background),
      `${theme} sidebar active link`
    ).toBeGreaterThanOrEqual(4.5)
    expect(
      wcagContrast(topTabStyle.foreground, topTabStyle.background),
      `${theme} top ribbon active tab`
    ).toBeGreaterThanOrEqual(4.5)

    await saveButton.hover()
    const expectedSaveStyle = await saveButton.evaluate(() => {
      const probe = document.createElement('span')
      probe.style.color = 'var(--accent-hover-foreground)'
      probe.style.backgroundColor = 'var(--accent-hover)'
      document.body.append(probe)
      const style = getComputedStyle(probe)
      const expected = { foreground: style.color, background: style.backgroundColor }
      probe.remove()
      return expected
    })
    await expect
      .poll(
        () =>
          saveButton.evaluate(element => {
            const style = getComputedStyle(element)
            return { foreground: style.color, background: style.backgroundColor }
          }),
        { message: `${theme} SAVE hover style` }
      )
      .toEqual(expectedSaveStyle)
    expect(
      wcagContrast(expectedSaveStyle.foreground, expectedSaveStyle.background),
      `${theme} hovered save button`
    ).toBeGreaterThanOrEqual(4.5)
    expect(violations).toEqual([])
  })
}
