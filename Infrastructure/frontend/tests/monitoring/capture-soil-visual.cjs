/* Capture Flower soil page screenshots at both approved viewports for visual QA. */
const { chromium } = require('@playwright/test')
const fs = require('fs')
const path = require('path')

const BASE = 'http://127.0.0.1:4173'
const OUT = path.resolve(__dirname, '../../.omo/evidence/flower-soil-scada')
const VIEWPORTS = [
  { width: 1920, height: 1080, slug: '1920x1080' },
  { width: 1280, height: 1440, slug: '1280x1440' },
]
const SCENARIOS = [{ slug: 'probes', scenario: 'soil-probes-2' }, { slug: 'badge', scenario: 'unassigned-after-mount' }]

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  for (const viewport of VIEWPORTS) {
    const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } })
    const page = await context.newPage()
    for (const entry of SCENARIOS) {
      const url = `${BASE}/flower/soil?fixtureSession=visual-${viewport.slug}-${entry.slug}&scenario=${entry.scenario}`
      await page.goto(url, { waitUntil: 'networkidle' })
      await page.waitForTimeout(1500)
      await page.screenshot({
        path: path.join(OUT, `soil-${entry.slug}-${viewport.slug}.png`),
        fullPage: true,
      })
      console.log(`captured ${entry.slug} at ${viewport.slug}`)
    }
    await context.close()
  }
  await browser.close()
})()
